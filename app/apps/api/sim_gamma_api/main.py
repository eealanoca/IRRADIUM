import json
import os
import shutil
from functools import lru_cache
from datetime import date
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from uuid import uuid4
from threading import Lock

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, FiniteFloat, field_validator

from sim_gamma.application.services.air_route_calibration import (
    AIR_CALIBRATION_METHOD, AIR_COMPARISON_METHOD, air_calibration_mesh, write_air_calibration,
)
from sim_gamma.application.services.cad_preview import CadPreviewService
from sim_gamma.application.services.case_store import CaseStore
from sim_gamma.application.services.configuration_store import ConfigurationStore
from sim_gamma.application.services.deterministic_jobs import DeterministicJobs
from sim_gamma.application.services.monte_carlo_jobs import MonteCarloJobs
from sim_gamma.application.services.movement_export import (
    build_from_deterministic_case,
    validate_movement_document,
    write_movement_document,
)
from sim_gamma.application.services.source_inventory import SourceInventory
from sim_gamma.simulators.registry import BackendRegistry
from phits_extension.availability import detect_phits_availability

from sim_gamma.branding import NAME, FULL_TITLE, SUBTITLE, VERSION
from .settings import settings
from .route_models import AirCalibrationComparisonRequest, AirReferenceRequest, AirRouteCalibrationRequest, RouteConfiguration, RouteDoseQuery, RouteProfileMutation, RouteResumeRequest, RouteStudyRequest
from sim_gamma.domain.route_dose import WORKFLOW as ROUTE_WORKFLOW, SOURCE_DIAMETER_MM, integrate_dose, source_endpoints
from .services import ScopedService, stop_all_jobs
from .desktop_session import DesktopWindows
from sim_gamma.domain.irradiators import catalog, current_irradiator, definition, UnsupportedIrradiator


app = FastAPI(
    title=FULL_TITLE,
    version=VERSION,
    description=SUBTITLE,
)
desktop_windows = DesktopWindows(os.environ.get("IRRADIUM_DESKTOP_SESSION") == "1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{os.environ.get('SIM_GAMMA_WEB_PORT', '3001')}",
        f"http://127.0.0.1:{os.environ.get('SIM_GAMMA_WEB_PORT', '3001')}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def irradiator_scope(request, call_next):
    category = "IV"
    if request.url.path.startswith("/irradiators/"):
        parts = request.scope["path"].split("/", 3)
        if len(parts) != 4:
            return JSONResponse(status_code=404, content={"detail": "Ruta de irradiador inválida"})
        category = parts[2]
        try:
            definition(category, require_enabled=True)
        except UnsupportedIrradiator as error:
            return JSONResponse(status_code=409, content={"detail": str(error)})
        except ValueError as error:
            return JSONResponse(status_code=404, content={"detail": str(error)})
        request.scope["path"] = "/" + parts[3]
        request.scope["raw_path"] = request.scope["path"].encode()
    token = current_irradiator.set(category)
    try:
        response = await call_next(request)
        response.headers["X-IRRADIUM-Irradiator"] = category
        return response
    finally:
        current_irradiator.reset(token)


@app.get("/api/irradiators")
def irradiator_catalog():
    return {"irradiators": catalog(), "application": NAME}


source_inventory = ScopedService("source_inventory")
configuration_store = ScopedService("configuration_store")
case_store = ScopedService("case_store")
cad_preview = ScopedService("cad_preview")
deterministic_jobs = ScopedService("deterministic_jobs")
monte_carlo_jobs = ScopedService("monte_carlo_jobs")
backend_registry = BackendRegistry(settings.monte_carlo_execution_enabled)
route_profile_lock = Lock()


@lru_cache(maxsize=1)
def _phits_capability():
    return detect_phits_availability()


def _require_phits_if_selected(parameters: Dict):
    if parameters.get("monte_carlo", {}).get("dose_tally") != "phits":
        return
    capability = _phits_capability()
    if not capability.available:
        raise HTTPException(status_code=409, detail=capability.message)


@app.on_event("shutdown")
async def stop_local_deterministic_jobs():
    """Release interactive services while detached route studies keep running."""
    desktop_windows.cancel_pending()
    stop_all_jobs(preserve_route_studies=True)


class StepMaterialMutation(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    elements: Dict[str, float]
    density: float = Field(gt=0)
    fraction_type: Optional[Literal["atomic", "weight"]] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not all(
            character.isalnum() or character in ".-" for character in normalized
        ):
            raise ValueError("El nombre debe coincidir con un material detectado en el STEP")
        return normalized

    @field_validator("elements")
    @classmethod
    def validate_elements(cls, values: Dict[str, float]) -> Dict[str, float]:
        if not values:
            raise ValueError("La composición debe contener al menos un elemento")
        if any(not symbol.strip() or fraction <= 0 for symbol, fraction in values.items()):
            raise ValueError("Los símbolos y fracciones elementales deben ser positivos")
        return {symbol.strip(): fraction for symbol, fraction in values.items()}


class CaseCreate(BaseModel):
    irradiator_type: Optional[str] = None
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    simulation_profile_id: Optional[str] = Field(default=None, max_length=120)
    step_materials: Optional[List[StepMaterialMutation]] = None


class DeterministicParameters(BaseModel):
    work_section: str = Field(default="A", min_length=1, max_length=20)
    num_points: int = Field(default=10000, ge=100, le=1000000)
    cores: int = Field(default=-1, ge=-1)
    longitudinal_positions_mm: List[int] = Field(
        default_factory=lambda: [-800, -300, 200, 700],
        min_length=2,
        max_length=20,
    )

    @field_validator("longitudinal_positions_mm")
    @classmethod
    def validate_longitudinal_positions(cls, values: List[int]) -> List[int]:
        normalized = sorted(set(values))
        if len(normalized) < 2:
            raise ValueError("Se requieren al menos dos posiciones longitudinales diferentes")
        return normalized


class CaseUpdate(CaseCreate):
    deterministic_parameters: DeterministicParameters


class DeterministicRunCreate(DeterministicParameters):
    angle_degrees: float = Field(default=0)


class MovementSimulationParameters(BaseModel):
    default_work_section: str = Field(default="A", min_length=1, max_length=20)
    machine_angle_start_degrees: int = Field(default=0, ge=-360, le=360)
    machine_angle_stop_degrees: int = Field(default=360, ge=1, le=720)
    machine_angle_step_degrees: int = Field(default=10, ge=1, le=360)
    angular_positions_range: Tuple[int, int] = (1, 13)
    cad_rotation_degrees: float = Field(default=0, ge=-360, le=360)
    length_complete_lower_range_mm: Tuple[int, int] = (-800, 200)
    length_complete_upper_range_mm: Tuple[int, int] = (200, 700)
    resolution_length_mm: int = Field(default=500, ge=1)

    @field_validator("angular_positions_range")
    @classmethod
    def validate_angular_positions_range(cls, values: Tuple[int, int]) -> Tuple[int, int]:
        if values[0] < 1 or values[0] >= values[1]:
            raise ValueError("El rango de posiciones angulares no es válido")
        return values


class DeterministicSimulationParameters(BaseModel):
    num_points: int = Field(default=10000, ge=100, le=1000000)


class ComputeSimulationParameters(BaseModel):
    cpu_cores: int = Field(default=-1, ge=-1)

    @field_validator("cpu_cores")
    @classmethod
    def validate_cpu_cores(cls, value: int) -> int:
        if value == 0:
            raise ValueError("El límite de CPU debe ser −1 o un número mayor que cero")
        return value


class MonteCarloSimulationParameters(BaseModel):
    cell_size_cm: float = Field(default=2, gt=0)
    particles: int = Field(default=100000000, ge=1)
    batches: int = Field(default=100, ge=2, le=10000)
    relative_error_target: float = Field(default=0.03, gt=0, lt=1)
    dose_tally: Literal["flux", "heating", "alanine", "phits"] = "flux"


class SimulationParametersMutation(BaseModel):
    radionuclide: str = Field(default="Co-60", min_length=1, max_length=40)
    movement: MovementSimulationParameters
    deterministic: DeterministicSimulationParameters
    compute: ComputeSimulationParameters
    monte_carlo: MonteCarloSimulationParameters


class SimulationProfileMutation(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parameters: SimulationParametersMutation


class SourceMutation(BaseModel):
    identifier: str = Field(min_length=1, max_length=80)
    lower_point: Tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    upper_point: Tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    initial_activity_kci: FiniteFloat = Field(gt=0)
    initial_reading_date: date

    @field_validator("upper_point")
    @classmethod
    def require_active_length(cls, value, info):
        if value == info.data.get("lower_point"):
            raise ValueError("Los puntos de la fuente deben ser distintos; la longitud activa no puede ser cero.")
        return value


class MaterialMutation(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    transport_medium: str = Field(default="air", min_length=1, max_length=80)
    dosimeter_medium: str = Field(default="alanine", min_length=1, max_length=80)
    coefficient_quantity: str = Field(default="mass_energy_absorption", min_length=1, max_length=80)
    coefficient_units: str = Field(default="cm2/g", min_length=1, max_length=40)
    coefficient_source: str = Field(
        default="NIST X-Ray Mass Attenuation Coefficients, Table 4: Alanine",
        min_length=1,
        max_length=240,
    )
    elements: Dict[str, float]
    density: float = Field(gt=0)
    fraction_type: Literal["atomic", "weight"] = "weight"
    mu_en_over_rho: Dict[str, float]


class WorkSectionMutation(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    position: Tuple[float, float, float]


class MovementPublication(BaseModel):
    step_angle_degrees_best_real: List[float]
    step_length: Dict[str, float]
    work_section: str
    work_section_position: Tuple[float, float, float]
    average_dose: float


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "environment": NAME,
        "irradiator_type": current_irradiator.get(),
        "monte_carlo_execution_enabled": settings.monte_carlo_execution_enabled,
        "desktop_session": desktop_windows.enabled,
    }


@app.websocket("/api/desktop/session")
async def desktop_session(websocket: WebSocket):
    port = os.environ.get("SIM_GAMMA_WEB_PORT", "3001")
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    if not desktop_windows.enabled or websocket.headers.get("origin") not in origins:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    desktop_windows.connected()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        desktop_windows.disconnected()


def _default_source_radionuclide():
    if definition(current_irradiator.get()).get("transport") == ROUTE_WORKFLOW:
        return _route_configuration().radionuclide
    return "Co-60"


@app.get("/api/config/sources")
def get_sources(
    reference_date: Optional[date] = Query(default=None),
    radionuclide: Optional[str] = Query(default=None),
):
    try:
        return source_inventory.sources(reference_date, radionuclide or _default_source_radionuclide())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/config/radionuclides")
def get_radionuclides():
    return {
        "radionuclides": [
            {
                "id": radionuclide_id,
                "name": data["name"],
                "half_life_days": data["half_life"],
            }
            for radionuclide_id, data in source_inventory.radionuclides().items()
        ]
    }


@app.post("/api/config/sources", status_code=201)
def add_source(payload: SourceMutation):
    try:
        source_inventory.add(_source_payload(payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return source_inventory.sources(radionuclide=_default_source_radionuclide())


@app.put("/api/config/sources/{source_index}")
def update_source(source_index: int, payload: SourceMutation):
    try:
        source_inventory.update(source_index, _source_payload(payload))
    except IndexError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return source_inventory.sources(radionuclide=_default_source_radionuclide())


@app.delete("/api/config/sources/{source_index}")
def delete_source(source_index: int):
    try:
        source_inventory.delete(source_index)
    except IndexError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return source_inventory.sources(radionuclide=_default_source_radionuclide())


@app.get("/api/config/materials")
def get_materials():
    return {"materials": configuration_store.materials()}


@app.post("/api/config/materials", status_code=201)
def add_material(payload: MaterialMutation):
    raise HTTPException(
        status_code=409,
        detail="La calibración del simulador solo admite aire como medio de transporte",
    )


@app.put("/api/config/materials/{material_name}")
def update_material(material_name: str, payload: MaterialMutation):
    if material_name != "air" or payload.name != "air":
        raise HTTPException(
            status_code=409,
            detail="Solo puede editarse la calibración del simulador realizada en aire",
        )
    configuration_store.save_material(payload.model_dump(), material_name)
    return get_materials()


@app.delete("/api/config/materials/{material_name}")
def delete_material(material_name: str):
    raise HTTPException(
        status_code=409,
        detail="La calibración del simulador en aire es obligatoria",
    )


@app.get("/api/config/work-sections")
def get_work_sections():
    return {"positions": configuration_store.work_sections()}


@app.get("/api/config/simulation-parameters")
def get_simulation_parameters():
    return configuration_store.simulation_parameters()


@app.put("/api/config/simulation-parameters")
def update_simulation_parameters(payload: SimulationParametersMutation):
    parameters = payload.model_dump()
    _validate_simulation_parameter_ranges(parameters)
    configuration_store.save_simulation_parameters(parameters)
    return parameters


def _validate_simulation_parameter_ranges(parameters):
    if parameters["radionuclide"] not in source_inventory.radionuclides():
        raise HTTPException(status_code=422, detail="Radionúclido no configurado")
    movement = parameters["movement"]
    if movement["machine_angle_start_degrees"] >= movement["machine_angle_stop_degrees"]:
        raise HTTPException(status_code=422, detail="El ángulo inicial debe ser menor que el final")
    if movement["angular_positions_range"][0] >= movement["angular_positions_range"][1]:
        raise HTTPException(status_code=422, detail="El rango de posiciones angulares no es válido")
    for key in ("length_complete_lower_range_mm", "length_complete_upper_range_mm"):
        if movement[key][0] > movement[key][1]:
            raise HTTPException(status_code=422, detail="Los rangos verticales no son válidos")


@app.get("/api/config/simulation-profiles")
def get_simulation_profiles():
    return {"profiles": configuration_store.simulation_profiles()}


@app.post("/api/config/simulation-profiles", status_code=201)
def save_simulation_profile(payload: SimulationProfileMutation):
    parameters = payload.parameters.model_dump()
    _validate_simulation_parameter_ranges(parameters)
    _require_phits_if_selected(parameters)
    configuration_store.save_simulation_parameters(parameters)
    return configuration_store.save_simulation_profile(payload.name, parameters)


@app.delete("/api/config/simulation-profiles/{profile_id}")
def delete_simulation_profile(profile_id: str):
    try:
        configuration_store.delete_simulation_profile(profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return get_simulation_profiles()


@app.post("/api/config/work-sections", status_code=201)
def add_work_section(payload: WorkSectionMutation):
    configuration_store.save_work_section(payload.name, list(payload.position))
    return get_work_sections()


@app.put("/api/config/work-sections/{section_name}")
def update_work_section(section_name: str, payload: WorkSectionMutation):
    configuration_store.save_work_section(
        payload.name,
        list(payload.position),
        section_name,
    )
    return get_work_sections()


@app.delete("/api/config/work-sections/{section_name}")
def delete_work_section(section_name: str):
    try:
        configuration_store.delete_work_section(section_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return get_work_sections()


@app.get("/api/assets/load")
def get_default_load_metadata():
    path = settings.example_step
    return {
        "name": path.name,
        "kind": "STEP",
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "scope": "load-geometry-only",
    }


@app.get("/api/assets/load/step")
def get_default_load_step():
    return FileResponse(
        settings.example_step,
        media_type="application/step",
        filename="load.step",
    )


@app.get("/api/assets/load/preview")
def get_default_load_preview():
    mesh = cad_preview.derive_mesh(settings.example_step)
    return FileResponse(mesh, media_type="model/stl", filename="step-preview.stl")


@app.get("/api/assets/load/preview/components")
def get_default_load_preview_components():
    return _component_preview_manifest(settings.example_step)


@app.post("/api/cad/preview")
async def preview_step(step: UploadFile = File(...)):
    _validate_step(step)
    upload_root = settings.cache_root / "step_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / (uuid4().hex + ".step")
    await _save_upload(step, path)
    return await run_in_threadpool(_component_preview_manifest, path)


@app.get("/api/cad/preview/components/{digest}/{filename}")
def get_component_preview_mesh(digest: str, filename: str):
    try:
        mesh = cad_preview.component_mesh_path(digest, filename)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(mesh, media_type="model/stl", filename=filename)


@app.get("/api/cases")
def list_cases():
    return {"irradiator_type": current_irradiator.get(), "cases": list(case_store.list())}


@app.post("/api/cases", status_code=201)
def create_case(payload: CaseCreate):
    if payload.irradiator_type is not None and payload.irradiator_type != current_irradiator.get():
        raise HTTPException(status_code=422, detail="La categoría del caso no coincide con el espacio seleccionado")
    try:
        case = case_store.create(
            payload.name,
            payload.description,
            payload.simulation_profile_id,
        )
        if payload.step_materials is not None:
            manifest = cad_preview.derive_component_meshes(settings.example_step)
            material_tags = sorted({part["material"] for part in manifest["parts"]})
            case = case_store.set_step_materials(
                case["case_id"],
                [material.model_dump() for material in payload.step_materials],
                material_tags,
            )
        return case
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    try:
        return case_store.get(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.put("/api/cases/{case_id}")
def update_case(case_id: str, payload: CaseUpdate):
    if payload.irradiator_type is not None and payload.irradiator_type != current_irradiator.get():
        raise HTTPException(status_code=422, detail="La categoría de un caso no se puede cambiar")
    try:
        _require_legacy_case(case_id)
        return case_store.update(
            case_id,
            payload.name,
            payload.description,
            payload.deterministic_parameters.model_dump(),
            payload.simulation_profile_id,
        )
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: str):
    try:
        status = deterministic_jobs.status(case_id)
        monte_carlo_status = monte_carlo_jobs.status(case_id)
        if (
            status.get("state") in deterministic_jobs.ACTIVE_STATES
            or monte_carlo_status.get("state") in monte_carlo_jobs.ACTIVE_STATES
        ):
            raise HTTPException(
                status_code=409,
                detail="No se puede eliminar un caso mientras una simulación está en ejecución",
            )
        case_store.delete(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return list_cases()


@app.get("/api/cases-trash")
def list_case_trash():
    return {"cases": list(case_store.list_trash())}


@app.post("/api/cases/{case_id}/trash")
def trash_case(case_id: str):
    try:
        if (deterministic_jobs.status(case_id).get("state") in deterministic_jobs.ACTIVE_STATES
                or monte_carlo_jobs.status(case_id).get("state") in monte_carlo_jobs.ACTIVE_STATES):
            raise HTTPException(status_code=409, detail="Detén la simulación antes de enviar el caso a la papelera")
        case_store.trash(case_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return list_cases()


@app.post("/api/cases-trash/{case_id}/restore")
def restore_case(case_id: str):
    try:
        return case_store.restore(case_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/cases/{case_id}/inputs")
async def upload_case_input(
    case_id: str,
    step: Optional[UploadFile] = File(default=None),
    stl: Optional[UploadFile] = File(default=None),
    materials_json: Optional[str] = Form(default=None),
):
    if step is None and stl is None:
        raise HTTPException(status_code=422, detail="Selecciona un archivo STEP o STL")
    if step is not None:
        _validate_step(step)
    if stl is not None:
        _validate_stl(stl)
    try:
        case_root = case_store.root(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    case = case_store.get(case_id)
    if case.get("workflow") == ROUTE_WORKFLOW and (case.get("step_input") or stl is not None):
        raise HTTPException(status_code=409, detail="La carga STEP de este caso de recorrido queda fijada al crearlo")
    if step is not None:
        destination = case_root / "input" / "load.step"
        candidate = case_root / "input" / "load.candidate.step"
        size = await _save_upload(step, candidate)
        try:
            manifest = cad_preview.derive_component_meshes(candidate)
            material_tags = sorted({part["material"] for part in manifest["parts"]})
            if materials_json is None:
                raise ValueError("Debes configurar los materiales detectados en el STEP")
            raw_materials = json.loads(materials_json)
            materials = [StepMaterialMutation.model_validate(item).model_dump() for item in raw_materials]
            configured_tags = {item["name"] for item in materials}
            if configured_tags != set(material_tags):
                missing = sorted(set(material_tags) - configured_tags)
                extra = sorted(configured_tags - set(material_tags))
                raise ValueError(
                    "Los materiales no coinciden con el STEP. Faltan: %s. Sobran: %s."
                    % (", ".join(missing) or "ninguno", ", ".join(extra) or "ninguno")
                )
            candidate.replace(destination)
            case = case_store.set_step_input(
                case_id,
                step.filename or "load.step",
                size,
                materials,
                material_tags,
            )
        except (json.JSONDecodeError, ValueError) as error:
            candidate.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(error)) from error
    if stl is not None:
        destination = case_root / "input" / "world_load.stl"
        size = await _save_upload(stl, destination)
        case = case_store.set_stl_input(case_id, stl.filename or "world_load.stl", size)
    return {
        "case_id": case_id,
        "saved": {"step": case.get("step_input"), "stl": case.get("stl_input")},
        "case": case,
    }


def _require_route_workflow():
    if definition(current_irradiator.get()).get("transport") != ROUTE_WORKFLOW:
        raise HTTPException(status_code=404, detail="Este irradiador no tiene el flujo de literas suspendidas")


def _route_configuration():
    path = settings.config_root / "route_parameters.json"
    return RouteConfiguration.model_validate_json(path.read_text()) if path.is_file() else RouteConfiguration()


def _validate_route_variance(payload: RouteConfiguration):
    allowed = {"analog", "phits_importance", "phits_windows"} if payload.dose_tally == "phits" else {"analog", "magic", "source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene"}
    if payload.variance_reduction not in allowed:
        raise HTTPException(status_code=422, detail="La técnica de reducción de varianza no corresponde al motor seleccionado")


@app.get("/api/route/configuration")
def get_route_configuration():
    _require_route_workflow()
    return _route_configuration()


@app.post("/api/route/calibration/air-reference")
def calculate_air_reference(payload: AirReferenceRequest):
    from sim_gamma.physics.deterministic.air_reference import line_source_fluence
    from sim_gamma.physics.parameters import load_physical_parameters, load_photon_yield

    _require_route_workflow()
    reference_date = payload.irradiation_date or date.today()
    radionuclide = _route_configuration().radionuclide
    source_file = settings.config_root / "sources.json"
    try:
        inventory = source_inventory.sources(reference_date, radionuclide)
        sources = inventory["sources"]
        if not sources:
            raise ValueError("Configura al menos una fuente para calcular la referencia.")
        if any(reference_date < date.fromisoformat(source["initial_reading_date"]) for source in sources):
            raise ValueError("Usa una fecha igual o posterior a todas las lecturas iniciales.")
        decays_cm2_s = line_source_fluence(payload.point_mm, sources, SOURCE_DIAMETER_MM / 2)
        _, dose_factor = load_physical_parameters(radionuclide, "air", source_file, settings.config_root / "materials.json")
        return {
            "irradiation_date": reference_date, "radionuclide": radionuclide, "source_count": len(sources),
            "photon_fluence_cm2_s": decays_cm2_s * load_photon_yield(radionuclide, source_file),
            "dose_rate_alanine_kgy_h": decays_cm2_s * dose_factor,
        }
    except (KeyError, ValueError, FileNotFoundError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


air_calibration_lock = Lock()


@app.get("/api/route/calibration/air-load")
def get_air_calibration_load(x: float = 500, y: float = 400, z: float = 1000):
    from sim_gamma.application.services.route_examples import example_step

    _require_route_workflow()
    try:
        step = example_step(settings.cache_root / "route-examples-v3", "airload", (x, y, z))
        return _component_preview_manifest(step)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/route/calibration/air-study", status_code=201)
def create_air_calibration_study(payload: AirRouteCalibrationRequest):
    from sim_gamma.application.services.route_examples import example_step

    _require_route_workflow()
    route = _route_configuration().model_dump(mode="json")
    if not route["positions"]:
        raise HTTPException(status_code=409, detail="Configura las posiciones del recorrido primero.")
    if not air_calibration_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una calibración en curso.")
    case_id = None
    try:
        mesh = air_calibration_mesh(payload.dimensions_mm, payload.cell_size_cm, len(route["positions"]))
        route.update(calculation_positions=None, occupancy="isolated", dose_tally="flux",
                     variance_reduction="analog", cell_size_cm=payload.cell_size_cm)
        step = example_step(settings.cache_root / "route-examples-v3", "airload", payload.dimensions_mm)
        case = case_store.create(
            "Calibración determinista en aire",
            "Carga llena de aire seco. Respuesta de alanina en campo libre.",
            route_configuration=route, analysis_method=AIR_CALIBRATION_METHOD,
        )
        case_id = case["case_id"]
        root = case_store.root(case_id)
        materials = json.loads((root / "input" / "materials.json").read_text(encoding="utf-8"))["materials"]
        air = next((material for material in materials if material["name"] == "airload"), None)
        if air is None:
            raise ValueError("El material de carga de aire seco no está configurado.")
        shutil.copyfile(step, root / "input" / "load.step")
        case_store.set_step_input(case_id, step.name, step.stat().st_size, [air], ["airload"])
        preview = cad_preview.derive_component_meshes(step)
        stl = cad_preview.component_mesh_path(preview["digest"], preview["parts"][0]["file"])
        shutil.copyfile(stl, root / "input" / "world_load.stl")
        case_store.set_stl_input(case_id, step.with_suffix(".stl").name, stl.stat().st_size)
        return write_air_calibration(root, case_id, route, mesh, payload.irradiation_date or date.today())
    except (KeyError, ValueError, FileNotFoundError) as error:
        if case_id:
            case_store.delete(case_id)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        if case_id:
            case_store.delete(case_id)
        raise
    finally:
        air_calibration_lock.release()


@app.post("/api/route/calibration/{case_id}/openmc", status_code=202)
def start_air_calibration_comparison(case_id: str, payload: AirCalibrationComparisonRequest):
    root = _route_case_root(case_id)
    if case_store.get(case_id).get("analysis_method") != AIR_CALIBRATION_METHOD:
        raise HTTPException(status_code=409, detail="Selecciona una calibración determinista guardada.")
    reference = get_route_results(case_id)
    route = json.loads((root / "input" / "route.json").read_text(encoding="utf-8"))
    with air_calibration_lock:
        comparison = next((item for item in case_store.list()
                           if item.get("calibration_case_id") == case_id), None)
        created = comparison is None
        if created:
            comparison = case_store.create(
                "Calibración en aire · OpenMC rápido",
                "Comparación con la referencia determinista mediante flujo y conversión a dosis en alanina.",
                route_configuration=route, analysis_method=AIR_COMPARISON_METHOD,
            )
        comparison_id = comparison["case_id"]
        try:
            if created:
                target = case_store.root(comparison_id)
                shutil.copytree(root / "input", target / "input", dirs_exist_ok=True)
                # Preserve the exact CAD, material coefficients and inventory of the reference.
                CaseStore._write_json(target / "input" / "route.json", {
                    **route, "flux_response_material": "air", "variance_reduction": "source_box",
                    "analysis_method": AIR_COMPARISON_METHOD,
                })
                snapshot = case_store.get(case_id)
                for key in ("input_snapshot", "step_input", "stl_input", "step_material_tags", "step_materials"):
                    comparison[key] = snapshot[key]
                comparison["calibration_case_id"] = case_id
                CaseStore._write_json(target / "manifest.json", comparison)
            return start_route_study(comparison_id, RouteStudyRequest(
                start=0, end=len(reference["positions"]) - 1,
                irradiation_date=date.fromisoformat(reference["activity_date"]),
                particles=payload.particles, batches=min(route["batches"], payload.particles),
                cell_size_cm=route["cell_size_cm"], occupancy="isolated", variance_reduction="source_box",
                source_mixture_fraction=.9, time_limit_seconds=60 * payload.time_limit_minutes,
                extended_time=payload.time_limit_minutes > 15,
            ))
        except Exception:
            if created:
                case_store.delete(comparison_id)
            raise


@app.put("/api/route/configuration")
def save_route_configuration(payload: RouteConfiguration):
    _require_route_workflow()
    _validate_route_variance(payload)
    if payload.radionuclide not in source_inventory.radionuclides():
        raise HTTPException(status_code=422, detail="Radionúclido no configurado")
    if payload.particles < payload.batches or payload.cpu_cores == 0:
        raise HTTPException(status_code=422, detail="Revisa el número de partículas, lotes y núcleos")
    path = settings.config_root / "route_parameters.json"
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


@app.get("/api/route/profiles")
def get_route_profiles():
    _require_route_workflow()
    return {"profiles": configuration_store.simulation_profiles("route_profiles.json")}


@app.post("/api/route/profiles", status_code=201)
def save_route_profile(payload: RouteProfileMutation):
    _require_route_workflow()
    _validate_route_variance(payload.configuration)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Escribe un nombre para el perfil")
    with route_profile_lock:
        return configuration_store.save_simulation_profile(
            name, payload.configuration.model_dump(mode="json"), "route_profiles.json",
        )


@app.delete("/api/route/profiles/{profile_id}")
def delete_route_profile(profile_id: str):
    _require_route_workflow()
    try:
        with route_profile_lock:
            configuration_store.delete_simulation_profile(profile_id, "route_profiles.json")
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return get_route_profiles()


@app.get("/api/route/examples")
def list_route_examples():
    from sim_gamma.application.services.route_examples import EXAMPLES

    _require_route_workflow()
    materials = {item["name"]: item for item in json.loads((settings.config_root / "materials.json").read_text())["materials"]}
    return {"examples": [
        {"id": name, "name": example.name, "dose_tally": example.method,
         "description": example.description, "materials": [materials[tag] for tag in example.materials]}
        for name, example in EXAMPLES.items() if all(tag in materials for tag in example.materials)
    ]}


@app.get("/api/route/examples/{name}")
def get_route_example(name: str, x: float = 500, y: float = 400, z: float = 1000):
    from sim_gamma.application.services.route_examples import example_step

    _require_route_workflow()
    try:
        path = example_step(settings.cache_root / "route-examples-v3", name, (x, y, z))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return FileResponse(path, media_type="application/step", filename=path.name)


@app.post("/api/route/cases", status_code=201)
async def create_route_case(
    name: str = Form(min_length=1, max_length=120),
    description: str = Form(default="", max_length=2000),
    step: UploadFile = File(),
    materials_json: str = Form(),
    dose_tally: Optional[Literal["flux", "heating", "alanine", "phits"]] = Form(default=None),
    occupancy: Optional[Literal["isolated", "occupied"]] = Form(default=None),
    variance_reduction: Optional[Literal["analog", "magic", "source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene", "phits_importance", "phits_windows"]] = Form(default=None),
    start_position: Optional[int] = Form(default=None),
    end_position: Optional[int] = Form(default=None),
):
    _require_route_workflow()
    _validate_step(step)
    route = _route_configuration()
    overrides = {key: value for key, value in {
        "dose_tally": dose_tally, "occupancy": occupancy, "variance_reduction": variance_reduction
    }.items() if value is not None}
    route = RouteConfiguration.model_validate(route.model_dump() | overrides)
    _validate_route_variance(route)
    if not route.positions:
        raise HTTPException(status_code=409, detail="Configura las posiciones del recorrido primero")
    start = 0 if start_position is None else start_position
    end = len(route.positions) - 1 if end_position is None else end_position
    if not 0 <= start <= end < len(route.positions):
        raise HTTPException(status_code=422, detail="El intervalo de cálculo debe estar dentro del recorrido")
    if not name.strip():
        raise HTTPException(status_code=422, detail="El nombre del caso es obligatorio")
    snapshot = route.model_dump(mode="json")
    if start_position is not None or end_position is not None:
        snapshot["score_interval"] = [start, end]
    case = case_store.create(name.strip(), description.strip(), route_configuration=snapshot)
    try:
        uploaded = await upload_case_input(case["case_id"], step=step, stl=None, materials_json=materials_json)
    except BaseException:
        case_store.delete(case["case_id"])
        raise
    return uploaded["case"]


def _route_case_root(case_id: str):
    _require_route_workflow()
    try:
        root = case_store.root(case_id)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not (root / "input" / "route.json").is_file():
        raise HTTPException(status_code=404, detail="El caso no pertenece al flujo de recorrido")
    return root


def _route_minimum_date(root: Path):
    inventory = json.loads((root / "input" / "sources.json").read_text(encoding="utf-8"))
    return max((date.fromisoformat(source["initial_reading_date"]) for source in inventory["sources"]), default=None)


@app.get("/api/route/cases/{case_id}/study")
def get_route_study(case_id: str):
    from sim_gamma.domain.route_study import EXTENDED_STUDY_SECONDS, STANDARD_STUDY_SECONDS
    from sim_gamma.workers.study_guard import available_memory_mb
    root = _route_case_root(case_id)
    route = RouteConfiguration.model_validate_json((root / "input" / "route.json").read_text(encoding="utf-8"))
    defaults = _route_configuration()
    inventory = SourceInventory(root / "input" / "sources.json", diameter_mm=SOURCE_DIAMETER_MM)
    return {
        "positions": route.positions, "calculation_positions": route.calculation_positions,
        "today": date.today(), "minimum_date": _route_minimum_date(root),
        "position_reference": route.position_reference,
        "cad_offset_degrees": route.cad_offset_degrees,
        "sources": inventory.sources(radionuclide=route.radionuclide)["sources"],
        "dose_tally": route.dose_tally, "occupancy": route.occupancy,
        "particles": defaults.particles, "batches": route.batches, "cell_size_cm": route.cell_size_cm,
        "variance_reduction": route.variance_reduction,
        "relative_error_target": min(route.relative_error_target, .10),
        "accumulated_error_target": defaults.accumulated_error_target,
        "interval_max_particles": route.interval_max_particles,
        "source_mixture_fraction": route.source_mixture_fraction,
        "memory_recommended_mb": int(min(4096, max(512, available_memory_mb() * .75))),
        "time_limit_seconds": route.interval_time_limit_seconds, "extended_time_limit_seconds": EXTENDED_STUDY_SECONDS,
        "adaptive_statistics": settings.statistical_refinement_enabled,
        "statistical_refinement_enabled": settings.statistical_refinement_enabled,
        "fixed_particles": defaults.particles,
    }


@app.post("/api/route/cases/{case_id}/study", status_code=202)
def start_route_study(case_id: str, payload: RouteStudyRequest):
    root = _route_case_root(case_id)
    if case_store.get(case_id).get("analysis_method") == AIR_CALIBRATION_METHOD:
        raise HTTPException(status_code=409, detail="Este caso contiene la calibración determinista en aire.")
    if not settings.statistical_refinement_enabled and (payload.adaptive_statistics or payload.accumulated_error_target is not None):
        raise HTTPException(status_code=422, detail="El refinamiento automático está deshabilitado temporalmente. Usa un presupuesto fijo de historias por posición.")
    route = json.loads((root / "input" / "route.json").read_text(encoding="utf-8"))
    if not settings.statistical_refinement_enabled and payload.particles is None:
        # Existing case snapshots may contain an old refinement ceiling.
        # Use the current default for a new run, preserving the snapshot.
        payload = payload.model_copy(update={"particles": _route_configuration().particles})
    if payload.adaptive_statistics and payload.particles is None and route.get("dose_tally") != "phits":
        payload = payload.model_copy(update={"particles": 10**12})
    effective = RouteConfiguration.model_validate(route | {
        key: value for key, value in payload.model_dump().items() if value is not None and key in RouteConfiguration.model_fields
    })
    _validate_route_variance(effective)
    if payload.accumulated_error_target is not None and effective.dose_tally == "phits":
        raise HTTPException(status_code=422, detail="El control del error acumulado está disponible para OpenMC.")
    if effective.particles < effective.batches:
        raise HTTPException(status_code=422, detail="El presupuesto de historias debe ser al menos el número de lotes")
    from sim_gamma.application.services.route_resources import estimate_study
    try:
        plan = estimate_study(root, cad_preview, effective.cell_size_cm, payload.memory_limit_mb,
                              geometry_instances=len(route["positions"]) if effective.occupancy == "occupied" else 1)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not plan["feasible"]:
        raise HTTPException(status_code=422, detail="La geometría y la malla superan el presupuesto estimado de RAM. Revisa la ocupación, la complejidad de la carga, el tamaño de celda o el límite de memoria.")
    from sim_gamma.domain.route_study import selected_position_indices
    try:
        indices = selected_position_indices(route["positions"], payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if payload.position_names is not None:
        payload = payload.model_copy(update={
            "start": indices[0], "end": indices[-1],
            "position_names": [route["positions"][index]["name"] for index in indices],
        })
    chosen_date = payload.irradiation_date or date.today()
    minimum_date = _route_minimum_date(root)
    if minimum_date and chosen_date < minimum_date:
        raise HTTPException(status_code=422, detail=f"Selecciona una fecha desde {minimum_date}, la última fecha de referencia de las fuentes del caso")
    _require_phits_if_selected(case_store.get(case_id).get("simulation_parameters") or {})
    study = payload.model_copy(update={"irradiation_date": chosen_date}).model_dump(mode="json")
    try:
        return monte_carlo_jobs.submit(case_id, route_study=study)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/route/cases/{case_id}/estimate")
def estimate_route_study(case_id: str, payload: RouteStudyRequest):
    from sim_gamma.application.services.route_resources import estimate_study
    root = _route_case_root(case_id)
    configuration = json.loads((root / "input" / "route.json").read_text())
    try:
        from sim_gamma.domain.interval_forecast import estimate_interval
        from sim_gamma.domain.route_study import selected_position_indices
        indices = selected_position_indices(configuration["positions"], payload.model_dump())
        effective = configuration | {key: value for key, value in payload.model_dump(mode="json").items() if value is not None}
        plan = estimate_study(root, cad_preview, payload.cell_size_cm or configuration["cell_size_cm"], payload.memory_limit_mb,
                              geometry_instances=len(configuration["positions"]) if effective["occupancy"] == "occupied" else 1)
        plan["position_count"] = len(indices)
        plan["precision_estimate"] = estimate_interval(root, effective, payload.model_dump(mode="json"))
        return plan
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _route_study_paths(root: Path):
    results = root / "results"
    latest = results / "route_rates.json"
    return sorted(
        (results / "studies").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
    ) + ([latest] if latest.is_file() else [])


@app.get("/api/route/cases/{case_id}/studies")
def list_route_studies(case_id: str):
    root = _route_case_root(case_id)
    studies = {}
    for path in _route_study_paths(root):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if path.name == "route_rates.json":
                continue
            raise
        if result["run_id"] in studies:
            continue
        positions = result["positions"]
        studies.setdefault(result["run_id"], {
            "run_id": result["run_id"], "activity_date": result["activity_date"],
            "first": positions[0]["name"], "last": positions[-1]["name"], "count": len(positions),
            "quick": result.get("quick", False),
            "adaptive_statistics": result.get("adaptive_statistics", False),
            "precision": _route_precision(root, result).get("precision"),
            "cell_size_cm": result.get("cell_size_cm"), "variance_reduction": result.get("variance_reduction"),
            "occupancy": result.get("occupancy"),
            "source_mixture_fraction": result.get("source_mixture_fraction"),
            "complete": result.get("complete", True), "requested_position_count": result.get("requested_position_count", len(positions)),
            "stop_reason": result.get("stop_reason"),
            "accumulated_error_target": result.get("accumulated_error_target"),
            "interval_precision": result.get("interval_precision"),
            "completed_calculations": result.get("completed_calculations"),
        })
    return {"studies": list(studies.values())}


def _route_precision(root: Path, result: dict):
    if result.get("analysis_method") == AIR_CALIBRATION_METHOD:
        return result
    from sim_gamma.domain.route_study import precision_status, study_precision

    for position in result["positions"]:
        if "precision" in position:
            continue
        summary_path = root / position.get("summary", "")
        if not summary_path.is_file():
            position["precision"] = precision_status({})
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        scored = summary.get("visualization") or summary
        position["precision"] = precision_status(scored)
        position["relative_error_maximum"] = scored.get("relative_error_maximum")
    result["precision"] = study_precision(result["positions"], result.get("complete", True))
    return result


@app.get("/api/route/cases/{case_id}/results")
def get_route_results(case_id: str, run_id: str | None = None):
    root = _route_case_root(case_id)
    path = root / "results" / "route_rates.json"
    if run_id is not None:
        if not run_id or len(run_id) > 80 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in run_id):
            raise HTTPException(status_code=404, detail="Estudio no encontrado")
        archived = path.parent / "studies" / f"{run_id}.json"
        if archived.is_file():
            return _route_precision(root, json.loads(archived.read_text(encoding="utf-8")))
        if path.is_file():
            result = json.loads(path.read_text(encoding="utf-8"))
            if result["run_id"] == run_id:
                return _route_precision(root, result)
        raise HTTPException(status_code=404, detail="Estudio no encontrado")
    if path.is_file() and case_store.get(case_id).get("analysis_method") == AIR_CALIBRATION_METHOD:
        return json.loads(path.read_text(encoding="utf-8"))
    status = monte_carlo_jobs.status(case_id)
    if status.get("state") not in {"completed", "paused", "interrupted", "time-limited", "memory-limited", "disk-limited", "cancelled"}:
        raise HTTPException(status_code=409, detail="Completa el cálculo de tasas primero")
    archived = path.parent / "studies" / f"{status.get('run_id', '')}.json"
    if archived.is_file():
        path = archived
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Completa el cálculo de tasas primero")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result["run_id"] != status.get("run_id"):
        raise HTTPException(status_code=409, detail="El cálculo de tasas se está actualizando")
    return _route_precision(root, result)


@app.get("/api/route/cases/{case_id}/studies/{run_id}/maps/{index}")
def get_route_rate_map(case_id: str, run_id: str, index: int):
    result = get_route_results(case_id, run_id)
    if not 0 <= index < len(result["positions"]):
        raise HTTPException(status_code=404, detail="Posición no encontrada en el estudio")
    position = result["positions"][index]
    path = (_route_case_root(case_id) / position["summary"]).parent / "dose_distribution.vti"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Mapa de tasas no disponible")
    return FileResponse(path, filename="tasa_dosis.vti", media_type="application/octet-stream")


@app.get("/api/route/cases/{case_id}/studies/{run_id}/scenarios")
def list_route_scenarios(case_id: str, run_id: str):
    from sim_gamma.domain.route_map import route_map_alignment
    from sim_gamma.application.services.route_analysis import map_error_statistics

    get_route_results(case_id, run_id)
    root = _route_case_root(case_id) / "results" / "dose_scenarios"
    scenarios = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["run_id"] == run_id:
            dose_map = saved.get("dose_map")
            if dose_map and "alignment" not in dose_map:
                dose_map["alignment"] = route_map_alignment(saved.get("positions"))
            if dose_map and dose_map.get("precision") and ("relative_error_p95" not in dose_map or "relative_error_p98" not in dose_map):
                volume = path.with_suffix(".vti")
                if volume.is_file():
                    dose_map.update(map_error_statistics(volume, volume.stat().st_mtime_ns))
            scenarios.append({**saved, "scenario_id": path.stem})
    return {"scenarios": scenarios}


def _route_position_analysis(case_id: str, run_id: str, index: int):
    from sim_gamma.application.services.route_analysis import position_analysis

    result = get_route_results(case_id, run_id)
    if not 0 <= index < len(result["positions"]):
        raise HTTPException(status_code=404, detail="Posición no encontrada en el estudio")
    return position_analysis(_route_case_root(case_id), result["positions"][index])


@app.get("/api/route/cases/{case_id}/studies/{run_id}/positions/{index}/analysis")
def get_route_position_analysis(case_id: str, run_id: str, index: int):
    return _route_position_analysis(case_id, run_id, index)[0]


@app.get("/api/route/cases/{case_id}/studies/{run_id}/positions/{index}/analysis/vtk")
def get_route_position_volume(case_id: str, run_id: str, index: int):
    return get_route_rate_map(case_id, run_id, index)


@app.get("/api/route/cases/{case_id}/studies/{run_id}/positions/{index}/analysis/cut/{plane}")
def get_route_position_cut(case_id: str, run_id: str, index: int, plane: Literal["xy", "yz"]):
    _, root = _route_position_analysis(case_id, run_id, index)
    path = root / f"isodose_{plane}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Corte no disponible")
    return FileResponse(path, media_type="image/png")


@app.get("/api/route/cases/{case_id}/scenarios/{scenario_id}/analysis")
def get_route_accumulated_analysis(case_id: str, scenario_id: str):
    from sim_gamma.application.services.route_analysis import accumulated_analysis

    response = get_route_dose_map(case_id, scenario_id)
    return accumulated_analysis(Path(response.path))[0]


@app.get("/api/route/cases/{case_id}/scenarios/{scenario_id}/analysis/vtk")
def get_route_accumulated_volume(case_id: str, scenario_id: str):
    return get_route_dose_map(case_id, scenario_id)


@app.get("/api/route/cases/{case_id}/scenarios/{scenario_id}/analysis/cut/{plane}")
def get_route_accumulated_cut(case_id: str, scenario_id: str, plane: Literal["xy", "yz"]):
    from sim_gamma.application.services.route_analysis import accumulated_analysis

    response = get_route_dose_map(case_id, scenario_id)
    _, root = accumulated_analysis(Path(response.path))
    return FileResponse(root / f"isodose_{plane}.png", media_type="image/png")


@app.post("/api/route/cases/{case_id}/dose")
def calculate_route_dose(case_id: str, query: RouteDoseQuery):
    try:
        result = get_route_results(case_id, query.run_id)
    except HTTPException as error:
        if query.run_id is not None and error.status_code == 404:
            raise HTTPException(status_code=409, detail="Las tasas de ese estudio no están disponibles. Selecciona un estudio guardado") from error
        raise
    if result.get("accumulated_error_target") and result.get("complete") is False:
        job = monte_carlo_jobs.status(case_id)
        if job.get("run_id") == result["run_id"] and job.get("state") in monte_carlo_jobs.ACTIVE_STATES:
            raise HTTPException(status_code=409, detail="El estudio está ajustando las historias del intervalo. Espera a que termine la verificación.")
    if not query.start <= query.end < len(result["positions"]):
        raise HTTPException(status_code=422, detail="Selecciona un intervalo válido en el orden del recorrido")
    positions = result["positions"][query.start:query.end + 1]
    sum_rates = query.mode == "rate_sum"
    deterministic = result.get("analysis_method") == AIR_CALIBRATION_METHOD
    try:
        rates = [position["mean_kgy_h"] for position in positions]
        target_rate = None
        if query.mode == "target_dose" and query.target_metric != "mean":
            from tempfile import TemporaryDirectory
            from sim_gamma.domain.route_map import write_route_dose_map

            # Extremes must come from the aligned voxel sum: summing each
            # position's minimum/maximum would combine different load points.
            with TemporaryDirectory(prefix="target-dose-", dir=_route_case_root(case_id) / "results") as temporary:
                reference = write_route_dose_map(
                    _route_case_root(case_id), positions, [60] * len(positions),
                    Path(temporary) / "rates.vti", sum_rates=True, deterministic=deterministic,
                )
            target_rate = reference[f"{query.target_metric}_kgy_h"] / len(positions)
        scenario = {"summed_rate_kgy_h": sum(rates), "quantity": "rate_sum"} if sum_rates else integrate_dose(
            rates, query.mode, query.value, query.times_minutes, target_rate_kgy_h=target_rate,
        )
        if query.mode == "target_dose":
            scenario.update(target_dose_kgy=query.value, target_metric=query.target_metric)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    output = {
        **scenario, "positions": positions, "run_id": result["run_id"],
    }
    if not deterministic:
        output["precision"] = {
            "meets_initial": all(point["precision"]["meets_initial"] for point in positions),
            "meets_recommended": all(point["precision"]["meets_recommended"] for point in positions),
            "basis": "source-rate-maps",
        }
    if query.save or query.dose_map or sum_rates:
        case_root = _route_case_root(case_id)
        root = case_root / "results" / "dose_scenarios"
        root.mkdir(exist_ok=True)
        scenario_id = uuid4().hex
        if query.dose_map or sum_rates:
            from sim_gamma.domain.route_map import write_route_dose_map

            target = root / f"{scenario_id}.vti"
            try:
                times = [60] * len(positions) if sum_rates else scenario["times_minutes"]
                output["dose_map"] = write_route_dose_map(
                    case_root, positions, times, target, sum_rates=sum_rates,
                    accumulated_error_target=_route_configuration().accumulated_error_target,
                    deterministic=deterministic,
                )
                if not deterministic:
                    output["precision"] = output["dose_map"]["precision"]
            except ValueError as error:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail=str(error)) from error
        path = root / f"{scenario_id}.json"
        CaseStore._write_json(path, {**output, "request": query.model_dump(mode="json")})
        output["scenario_id"] = scenario_id
    return output


@app.get("/api/route/cases/{case_id}/dose-maps/{scenario_id}")
def get_route_dose_map(case_id: str, scenario_id: str):
    root = _route_case_root(case_id) / "results" / "dose_scenarios"
    if len(scenario_id) != 32 or any(character not in "0123456789abcdef" for character in scenario_id):
        raise HTTPException(status_code=404, detail="Mapa de dosis no encontrado")
    path = root / f"{scenario_id}.vti"
    if not path.is_file() or not path.with_suffix(".json").is_file():
        raise HTTPException(status_code=404, detail="Mapa de dosis no encontrado")
    return FileResponse(path, filename="dosis_acumulada.vti", media_type="application/octet-stream")


def _require_legacy_case(case_id: str):
    if (case_store.root(case_id) / "input" / "route.json").is_file():
        raise HTTPException(status_code=409, detail="Este caso calcula tasas por posición y no exporta movimientos")


@app.put("/api/cases/{case_id}/inputs/materials")
def update_case_step_materials(case_id: str, payload: List[StepMaterialMutation]):
    """Asocia composiciones al STEP ya guardado, sin exigir volver a subirlo."""
    try:
        _require_legacy_case(case_id)
        step_path = case_store.root(case_id) / "input" / "load.step"
        if not step_path.is_file():
            raise FileNotFoundError("El caso no contiene un archivo STEP")
        manifest = cad_preview.derive_component_meshes(step_path)
        material_tags = sorted({part["material"] for part in manifest["parts"]})
        return case_store.set_step_materials(
            case_id,
            [material.model_dump() for material in payload],
            material_tags,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/cases/{case_id}/inputs/step")
def get_case_step(case_id: str):
    try:
        case = case_store.get(case_id)
        path = case_store.root(case_id) / "input" / "load.step"
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El caso no tiene un archivo STEP")
    metadata = case.get("step_input") or {}
    return FileResponse(
        path,
        media_type="application/step",
        filename=metadata.get("original_name") or "load.step",
    )


@app.get("/api/route/cases/{case_id}/cad-preview")
def get_saved_route_cad_preview(case_id: str):
    path = _route_case_root(case_id) / "input" / "load.step"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El caso no tiene un archivo STEP")
    try:
        return _component_preview_manifest(path)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/cases/{case_id}/inputs/stl")
def get_case_stl(case_id: str):
    try:
        case = case_store.get(case_id)
        path = case_store.root(case_id) / "input" / "world_load.stl"
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El caso no tiene un archivo STL determinista")
    metadata = case.get("stl_input") or {}
    return FileResponse(
        path,
        media_type="model/stl",
        filename=metadata.get("original_name") or "world_load.stl",
    )


@app.post("/api/cases/{case_id}/deterministic", status_code=202)
def start_deterministic(case_id: str, payload: DeterministicRunCreate):
    try:
        _require_legacy_case(case_id)
        return deterministic_jobs.submit(case_id, payload.model_dump())
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/cases/{case_id}/deterministic/cancel")
def cancel_deterministic(case_id: str):
    try:
        return deterministic_jobs.cancel(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/deterministic/cancel-all")
def cancel_all_deterministic():
    return {"cancelled": deterministic_jobs.cancel_all()}


@app.get("/api/cases/{case_id}/monte-carlo")
def get_monte_carlo_status(case_id: str):
    try:
        status = monte_carlo_jobs.status(case_id)
        resources = case_store.root(case_id) / "monte_carlo" / "resources.json"
        if resources.is_file():
            status["resources"] = json.loads(resources.read_text())
        return status
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/cases/{case_id}/monte-carlo", status_code=202)
def start_monte_carlo(case_id: str):
    try:
        case = case_store.get(case_id)
        _require_phits_if_selected(case.get("simulation_parameters") or {})
        return monte_carlo_jobs.submit(case_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/cases/{case_id}/monte-carlo/cancel")
def cancel_monte_carlo(case_id: str):
    try:
        return monte_carlo_jobs.cancel(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/cases/{case_id}/monte-carlo/pause", status_code=202)
def pause_monte_carlo(case_id: str):
    try:
        return monte_carlo_jobs.pause(case_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/cases/{case_id}/monte-carlo/resume", status_code=202)
def resume_monte_carlo(case_id: str, payload: RouteResumeRequest):
    try:
        return monte_carlo_jobs.resume(case_id, payload.run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/simulations/cancel-all")
def cancel_all_simulations():
    if definition(current_irradiator.get()).get("transport") == ROUTE_WORKFLOW:
        # Older IV browser tabs send this beacon when they close. Studies are
        # independent of the tab and are cancelled explicitly by case.
        return {"deterministic": [], "monte_carlo": []}
    return {
        "deterministic": deterministic_jobs.cancel_all(),
        "monte_carlo": monte_carlo_jobs.cancel_all(),
    }


@app.get("/api/cases/{case_id}/deterministic")
def get_deterministic_status(case_id: str):
    try:
        return deterministic_jobs.status(case_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/cases/{case_id}/deterministic/result")
def get_deterministic_result(case_id: str):
    try:
        path = case_store.root(case_id) / "deterministic" / "result.json"
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=409, detail="El resultado determinista aún no está listo")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.post("/api/cases/{case_id}/results/movements/from-deterministic")
def publish_deterministic_movements(case_id: str):
    try:
        _require_legacy_case(case_id)
        case_root = case_store.root(case_id)
        document = build_from_deterministic_case(case_root)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    write_movement_document(
        case_root / "results" / "movements_parameters_optimized.json",
        document,
    )
    write_movement_document(
        settings.movement_output,
        document,
    )
    return document


@app.post("/api/cases/{case_id}/results/save")
def save_case_results(case_id: str):
    """Consolida los resultados terminados y publica la trayectoria del caso."""
    try:
        _require_legacy_case(case_id)
        case_root = case_store.root(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    missing_methods = [
        label
        for label, path in (
            ("determinista", case_root / "deterministic" / "result.json"),
            ("Monte Carlo", case_root / "monte_carlo" / "result.json"),
        )
        if not path.is_file()
    ]
    if missing_methods:
        raise HTTPException(
            status_code=409,
            detail=(
                "No se pueden guardar los resultados: falta completar la simulación "
                + " y ".join(missing_methods)
            ),
        )

    try:
        document = build_from_deterministic_case(case_root)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    case_path = case_root / "results" / "movements_parameters_optimized.json"
    write_movement_document(case_path, document)
    # Esta copia conserva el contrato consumido por el sistema externo. La
    # interfaz de resultados lee siempre la copia propia del caso seleccionado.
    write_movement_document(
        settings.movement_output,
        document,
    )
    return {
        "case_id": case_id,
        "saved": True,
        "movement": document,
    }


@app.post("/api/cases/{case_id}/results/movements")
def publish_movements(case_id: str, payload: MovementPublication):
    try:
        manifest = case_store.get(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    document = payload.model_dump()
    document["work_section_position"] = list(document["work_section_position"])
    document["description"] = manifest["description"]
    validate_movement_document(document)
    case_path = case_store.root(case_id) / "results" / "movements_parameters_optimized.json"
    write_movement_document(case_path, document)
    write_movement_document(settings.movement_output, document)
    return document


@app.get("/api/results/latest/movements")
def get_latest_movement_parameters():
    path = settings.movement_output
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Aún no existe una trayectoria publicada")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/api/results/cases/{case_id}/movements")
def get_case_movement_parameters(case_id: str):
    try:
        path = (
            case_store.root(case_id)
            / "results"
            / "movements_parameters_optimized.json"
        )
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Este caso aún no tiene resultados guardados",
        )
    return FileResponse(
        path,
        media_type="application/json",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/results/cases/{case_id}")
def get_case_result_catalog(case_id: str):
    try:
        _require_legacy_case(case_id)
        case_root = case_store.root(case_id)
        manifest = case_store.get(case_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    deterministic_root = case_root / "deterministic"
    monte_carlo_root = case_root / "monte_carlo"
    deterministic_result = _read_optional_json(deterministic_root / "result.json")
    monte_carlo_result = _read_optional_json(monte_carlo_root / "result.json")
    result_materials = (monte_carlo_result or {}).get("materials") or (
        (monte_carlo_result or {}).get("visualization", {}).get("materials")
    )
    if not result_materials:
        names = sorted(
            material["name"]
            for material in manifest.get("step_materials", [])
            if material.get("name") != "air"
        )
        result_materials = [
            {"id": index, "name": name}
            for index, name in enumerate(names, start=1)
        ]
    deterministic_status = deterministic_jobs.status(case_id)
    monte_carlo_status = monte_carlo_jobs.status(case_id)
    return {
        "case_id": case_id,
        "irradiator_type": manifest.get("irradiator_type", "IV"),
        "name": manifest["name"],
        "description": manifest.get("description", ""),
        "monte_carlo_dose_tally": (
            (monte_carlo_result or {}).get("dose_tally")
            or manifest.get("simulation_parameters", {})
            .get("monte_carlo", {})
            .get("dose_tally", "flux")
        ),
        "load_materials": result_materials,
        "methods": {
            "deterministic": _method_catalog(
                deterministic_root,
                deterministic_status,
                deterministic_result,
            ),
            "monte_carlo": _method_catalog(
                monte_carlo_root,
                monte_carlo_status,
                monte_carlo_result,
            ),
        },
    }


@app.get("/api/results/cases/{case_id}/{method}/vtk")
def get_case_vtk(case_id: str, method: str):
    root = _result_method_root(case_id, method)
    candidates = (
        ("dose_distribution.vtk",)
        if method == "deterministic"
        else (
            "dose_distribution.vti",
            "dose_distribution.vtk",
            "mesh_mc_absorbed_dose_volume.vtk",
            "result.vtk",
        )
    )
    path = _first_existing(root, candidates)
    if path is None:
        raise HTTPException(status_code=404, detail="El archivo VTK aún no está disponible")
    media_type = "application/vnd.vtk.vti+xml" if path.suffix == ".vti" else "application/vnd.vtk"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/results/cases/{case_id}/{method}/cut/{plane}")
def get_case_isodose_cut(case_id: str, method: str, plane: str):
    if plane not in ("xy", "yz"):
        raise HTTPException(status_code=422, detail="El plano debe ser XY o YZ")
    root = _result_method_root(case_id, method)
    path = root / ("isodose_%s.png" % plane)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El corte 2D aún no está disponible")
    return FileResponse(
        path,
        media_type="image/png",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/simulation/backends")
def simulation_backends():
    phits = _phits_capability()
    return {
        "monte_carlo": backend_registry.capabilities(),
        "active_backend": "openmc-0.15.3-dagmc",
        "phits": phits.as_dict(),
    }


def _source_payload(payload: SourceMutation):
    source = {
        "identifier": payload.identifier.strip(),
        "lower_point": list(payload.lower_point),
        "upper_point": list(payload.upper_point),
        "intensity": payload.initial_activity_kci * 3.7e13,
        "initial_reading_date": payload.initial_reading_date.isoformat(),
    }
    if definition(current_irradiator.get()).get("transport") == ROUTE_WORKFLOW:
        source_endpoints(source)
        source["diameter_mm"] = SOURCE_DIAMETER_MM
    return source


def _component_preview_manifest(step_path: Path):
    manifest = cad_preview.derive_component_meshes(step_path)
    dimensions = None
    if definition(current_irradiator.get()).get("transport") == ROUTE_WORKFLOW:
        import numpy as np
        import trimesh

        bounds = [
            trimesh.load_mesh(cad_preview.component_mesh_path(manifest["digest"], part["file"]), process=False).bounds
            for part in manifest["parts"] if part["material"] != "air"
        ]
        if bounds:
            dimensions = (np.max([item[1] for item in bounds], axis=0) - np.min([item[0] for item in bounds], axis=0)).tolist()
    return {
        "digest": manifest["digest"],
        "dimensions_mm": dimensions,
        "material_tags": sorted({part["material"] for part in manifest["parts"]}),
        "parts": [
            {
                "name": part["name"],
                "material": part["material"],
                "transparent": part["transparent"],
                "url": "/api/cad/preview/components/%s/%s"
                % (manifest["digest"], part["file"]),
            }
            for part in manifest["parts"]
        ]
    }


def _result_method_root(case_id: str, method: str) -> Path:
    if method not in ("deterministic", "monte_carlo"):
        raise HTTPException(status_code=422, detail="Método de simulación inválido")
    try:
        return case_store.root(case_id) / method
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _read_optional_json(path: Path):
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _first_existing(root: Path, candidates):
    return next((root / name for name in candidates if (root / name).is_file()), None)


def _method_catalog(root: Path, status, summary):
    vtk_candidates = (
        "dose_distribution.vti",
        "dose_distribution.vtk",
        "mesh_mc_absorbed_dose_volume.vtk",
        "result.vtk",
    )
    vtk_path = _first_existing(root, vtk_candidates)
    artifact_revision = str(
        (summary or {}).get("run_id")
        or status.get("run_id")
        or max(
            (
                path.stat().st_mtime_ns
                for path in (
                    vtk_path,
                    root / "isodose_xy.png",
                    root / "isodose_yz.png",
                )
                if path is not None and path.is_file()
            ),
            default=0,
        )
    )
    return {
        "state": status.get("state", "not-started"),
        "progress": status.get("progress", 0),
        "stage": status.get("stage"),
        "error": status.get("error"),
        "summary": summary,
        "artifact_revision": artifact_revision,
        "artifacts": {
            "vtk": vtk_path is not None,
            "vtk_format": vtk_path.suffix.lstrip(".") if vtk_path is not None else None,
            "cut_xy": (root / "isodose_xy.png").is_file(),
            "cut_yz": (root / "isodose_yz.png").is_file(),
        },
    }


def _validate_step(upload: UploadFile) -> None:
    if Path(upload.filename or "").suffix.lower() not in (".step", ".stp"):
        raise HTTPException(status_code=422, detail="Solo se admiten archivos STEP o STP")


def _validate_stl(upload: UploadFile) -> None:
    if Path(upload.filename or "").suffix.lower() != ".stl":
        raise HTTPException(status_code=422, detail="Solo se admiten archivos STL")


async def _save_upload(upload: UploadFile, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".upload")
    total = 0
    with temporary.open("wb") as stream:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 500 * 1024 * 1024:
                temporary.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Archivo mayor a 500 MB")
            stream.write(chunk)
    temporary.replace(destination)
    return total


class UserPreferences(BaseModel):
    theme: Literal["light", "dark"]


class LearningState(BaseModel):
    dismissed: bool = False
    version: int = Field(default=1, ge=1, le=1)


class LanguagePreference(BaseModel):
    language: Literal['en','es']


@app.get('/api/language')
def get_language():
    path=settings.workspace_root / 'Configuracion/idioma.json'
    return json.loads(path.read_text()) if path.is_file() else {'language':'en'}


@app.put('/api/language')
def set_language(payload: LanguagePreference):
    path=settings.workspace_root / 'Configuracion/idioma.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.language-'+uuid4().hex)
    temporary.write_text(payload.model_dump_json(indent=2))
    temporary.replace(path)
    return payload


@app.get('/api/onboarding')
def get_learning_state():
    path = settings.workspace_root / 'Configuracion/aprendizaje.json'
    return json.loads(path.read_text()) if path.is_file() else {'dismissed': False, 'version': 1}


@app.put('/api/onboarding')
def save_learning_state(payload: LearningState):
    path = settings.workspace_root / 'Configuracion/aprendizaje.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.learning-' + uuid4().hex)
    temporary.write_text(payload.model_dump_json(indent=2))
    temporary.replace(path)
    return payload


class TeachingLoadRequest(BaseModel):
    variant: Literal['water','medical_kit']='water'


@app.post('/api/learning/example', status_code=201)
def create_learning_example(payload: TeachingLoadRequest):
    from sim_gamma.application.services.training_example import create_training_case
    _require_route_workflow()
    return create_training_case(settings.output_root, settings.project_root / 'examples/commercial_svst', payload.variant)


@app.get('/api/learning/guide')
def quick_start_guide(language: Literal['en','es']='en'):
    guide = settings.project_root.parent / ('docs/quickstart/IRRADIUM-quick-start-en.pdf' if language=='en' else 'docs/quickstart/IRRADIUM-guia-rapida-es.pdf')
    if not guide.is_file():
        raise HTTPException(status_code=404, detail='La guía PDF no está incluida en esta copia de desarrollo.')
    return FileResponse(guide, media_type='application/pdf', filename=guide.name)


def _preferences_path() -> Path:
    return settings.workspace_root / "Configuracion/preferencias.json"


@app.get("/api/preferences")
def get_preferences():
    path = _preferences_path()
    if not path.is_file():
        return {"theme": None}
    return json.loads(path.read_text(encoding="utf-8"))


@app.put("/api/preferences")
def save_preferences(payload: UserPreferences):
    path = _preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".preferences-{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload.model_dump()) + "\n", encoding="utf-8")
    temporary.replace(path)
    return payload.model_dump()
