import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable
from uuid import uuid4

from sim_gamma.domain.route_dose import WORKFLOW


CASE_DIRECTORIES = (
    "input",
    "deterministic",
    "monte_carlo",
    "results",
    "logs",
)

DEFAULT_SIMULATION_PARAMETERS = {
    "radionuclide": "Co-60",
    "movement": {
        "default_work_section": "A",
        "machine_angle_start_degrees": 0,
        "machine_angle_stop_degrees": 360,
        "machine_angle_step_degrees": 10,
        "angular_positions_range": [1, 13],
        "cad_rotation_degrees": 0,
        "length_complete_lower_range_mm": [-800, 200],
        "length_complete_upper_range_mm": [200, 700],
        "resolution_length_mm": 500,
    },
    "deterministic": {"num_points": 10000},
    "compute": {"cpu_cores": -1},
    "monte_carlo": {
        "cell_size_cm": 2,
        "particles": 100000000,
        "batches": 100,
        "relative_error_target": 0.03,
        "dose_tally": "flux",
    },
}


def _normalize_simulation_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    if "movement" in parameters:
        monte_carlo = parameters.get("monte_carlo", {})
        compute = parameters.get("compute", {})
        return {
            "radionuclide": parameters.get("radionuclide", "Co-60"),
            "movement": parameters["movement"],
            "deterministic": parameters.get("deterministic", {"num_points": 10000}),
            "compute": {
                "cpu_cores": compute.get(
                    "cpu_cores",
                    monte_carlo.get("cpu_cores", -1),
                )
            },
            "monte_carlo": {
                "cell_size_cm": monte_carlo.get("cell_size_cm", 2),
                "particles": monte_carlo.get("particles", 100000000),
                "batches": monte_carlo.get("batches", 100),
                "relative_error_target": monte_carlo.get("relative_error_target", 0.03),
                "dose_tally": monte_carlo.get("dose_tally", "flux"),
            },
        }
    legacy = parameters.get("deterministic", {})
    monte_carlo = parameters.get("monte_carlo", {})
    return {
        "radionuclide": parameters.get("radionuclide", "Co-60"),
        "movement": {
            "default_work_section": legacy.get("default_work_section", "A"),
            "machine_angle_start_degrees": legacy.get("machine_angle_start_degrees", 0),
            "machine_angle_stop_degrees": legacy.get("machine_angle_stop_degrees", 360),
            "machine_angle_step_degrees": legacy.get("machine_angle_step_degrees", 10),
            "angular_positions_range": legacy.get("num_steps_angle_range", [1, 13]),
            "cad_rotation_degrees": legacy.get("cad_rotation_degrees", 0),
            "length_complete_lower_range_mm": legacy.get("length_complete_lower_range_mm", [-800, 200]),
            "length_complete_upper_range_mm": legacy.get("length_complete_upper_range_mm", [200, 700]),
            "resolution_length_mm": legacy.get("resolution_length_mm", 500),
        },
        "deterministic": {"num_points": legacy.get("num_points", 10000)},
        "compute": {
            "cpu_cores": monte_carlo.get("cpu_cores", legacy.get("cores", -1))
        },
        "monte_carlo": {
            "cell_size_cm": monte_carlo.get("cell_size_cm", 2),
            "particles": monte_carlo.get("particles", 100000000),
            "batches": monte_carlo.get("batches", 100),
            "relative_error_target": monte_carlo.get("relative_error_target", 0.03),
            "dose_tally": monte_carlo.get("dose_tally", "flux"),
        },
    }


def _longitudinal_positions(parameters: Dict[str, Any]) -> list:
    movement = parameters["movement"]
    resolution = int(movement["resolution_length_mm"])
    lower = movement["length_complete_lower_range_mm"]
    upper = movement["length_complete_upper_range_mm"]
    return sorted(
        set(range(int(lower[0]), int(lower[1]) + 1, resolution)).union(
            range(int(upper[0]), int(upper[1]) + 1, resolution)
        )
    )


def _case_deterministic_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    movement = parameters["movement"]
    deterministic = parameters["deterministic"]
    return {
        "work_section": movement["default_work_section"],
        "num_points": deterministic["num_points"],
        "cores": parameters["compute"]["cpu_cores"],
        "longitudinal_positions_mm": _longitudinal_positions(parameters),
    }


class CaseStore:
    """Crea espacios autocontenidos para ejecuciones reproducibles."""

    def __init__(
        self,
        output_root: Path,
        config_root: Path,
        example_step: Path,
        deterministic_stl: Path = None,
        cases_directory_name: str = "runs",
        irradiator_type: str = "IV",
    ):
        self.irradiator_type = irradiator_type
        self.output_root = output_root
        self.config_root = config_root
        self.example_step = example_step
        self.deterministic_stl = deterministic_stl
        self.cases_directory_name = cases_directory_name

    @property
    def runs_root(self) -> Path:
        """Raíz persistente de casos; el nombre puede localizarse al instalar."""
        return self.output_root / self.cases_directory_name

    @staticmethod
    def _slug(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
        return normalized[:48] or "caso"

    def create(
        self,
        name: str,
        description: str = "",
        simulation_profile_id: str = None,
        *, route_configuration: Dict[str, Any] = None, analysis_method: str | None = None,
    ) -> Dict[str, Any]:
        timestamp = datetime.now(timezone.utc)
        case_id = "%s-%s-%s" % (
            timestamp.strftime("%Y%m%dT%H%M%SZ"),
            self._slug(name),
            uuid4().hex[:8],
        )
        case_root = self.runs_root / case_id
        case_root.mkdir(parents=True, exist_ok=False)

        for directory in CASE_DIRECTORIES:
            (case_root / directory).mkdir()

        snapshots = []
        step_input = None
        stl_input = None
        for filename in (
            "sources.json",
            "materials.json",
            "work_sections.json",
        ):
            if filename == "work_sections.json" and route_configuration is not None:
                continue
            origin = self.config_root / filename
            if origin.is_file():
                shutil.copy2(str(origin), str(case_root / "input" / filename))
                snapshots.append(filename)
        if route_configuration is None and self.example_step.is_file():
            stored_step = case_root / "input" / "load.step"
            shutil.copy2(str(self.example_step), str(stored_step))
            snapshots.append("load.step")
            step_input = {
                "kind": "STEP",
                "original_name": self.example_step.name,
                "stored_name": stored_step.name,
                "size_bytes": stored_step.stat().st_size,
            }
        if route_configuration is None and self.deterministic_stl and self.deterministic_stl.is_file():
            stored_stl = case_root / "input" / "world_load.stl"
            shutil.copy2(
                str(self.deterministic_stl),
                str(stored_stl),
            )
            snapshots.append("world_load.stl")
            stl_input = {
                "kind": "STL",
                "original_name": self.deterministic_stl.name,
                "stored_name": stored_stl.name,
                "size_bytes": stored_stl.stat().st_size,
            }

        if route_configuration is None:
            simulation_parameters, simulation_profile = self._resolve_simulation_profile(
                simulation_profile_id
            )
        else:
            simulation_parameters = {
                "radionuclide": route_configuration["radionuclide"],
                "compute": {"cpu_cores": route_configuration["cpu_cores"]},
                "monte_carlo": {
                    key: route_configuration[key]
                    for key in ("particles", "batches", "cell_size_cm", "relative_error_target")
                } | {
                    "dose_tally": route_configuration.get("dose_tally", "heating"),
                    "adaptive_statistics": False,
                    "weight_window_method": "magic" if route_configuration.get("variance_reduction") == "magic" else "none",
                },
            }
            simulation_profile = None
            self._write_json(case_root / "input" / "route.json", route_configuration)
            snapshots.append("route.json")
        self._write_json(
            case_root / "input" / "simulation_parameters.json",
            simulation_parameters,
        )
        snapshots.append("simulation_parameters.json")

        manifest = {
            "schema_version": 1,
            "irradiator_type": self.irradiator_type,
            "case_id": case_id,
            "name": name,
            "description": description,
            "created_at": timestamp.isoformat(),
            "state": "draft",
            "current_step": "load-and-conditions",
            "monte_carlo_execution_enabled": False,
            "input_snapshot": snapshots,
            "step_input": step_input,
            "stl_input": stl_input,
            "simulation_parameters": simulation_parameters,
            "simulation_profile": simulation_profile,
            "deterministic_parameters": (
                _case_deterministic_parameters(simulation_parameters)
                if route_configuration is None else None
            ),
        }
        if route_configuration is not None:
            manifest["workflow"] = WORKFLOW
        if analysis_method is not None:
            manifest["analysis_method"] = analysis_method
        self._write_json(case_root / "manifest.json", manifest)
        return manifest

    def _resolve_simulation_profile(self, profile_id: str = None):
        if profile_id and profile_id != "configuracion-actual":
            profiles_path = self.config_root / "simulation_profiles.json"
            if profiles_path.is_file():
                with profiles_path.open("r", encoding="utf-8") as stream:
                    profiles = json.load(stream).get("profiles", [])
                profile = next(
                    (item for item in profiles if item["profile_id"] == profile_id),
                    None,
                )
                if profile is None:
                    raise ValueError("Configuración de simulación no encontrada")
                return _normalize_simulation_parameters(profile["parameters"]), {
                    "profile_id": profile["profile_id"],
                    "name": profile["name"],
                }
        simulation_path = self.config_root / "simulation_parameters.json"
        if simulation_path.is_file():
            with simulation_path.open("r", encoding="utf-8") as stream:
                parameters = json.load(stream)
        else:
            parameters = DEFAULT_SIMULATION_PARAMETERS
        return _normalize_simulation_parameters(parameters), {
            "profile_id": "configuracion-actual",
            "name": "Configuración actual",
        }

    def get(self, case_id: str) -> Dict[str, Any]:
        case_root = self.root(case_id)
        with (case_root / "manifest.json").open("r", encoding="utf-8") as stream:
            return self._with_step_metadata(json.load(stream), case_root)

    def update(
        self,
        case_id: str,
        name: str,
        description: str,
        deterministic_parameters: Dict[str, Any] = None,
        simulation_profile_id: str = None,
    ) -> Dict[str, Any]:
        root = self.root(case_id)
        manifest = self.get(case_id)
        manifest["name"] = name
        manifest["description"] = description
        if simulation_profile_id is not None:
            simulation_parameters, simulation_profile = self._resolve_simulation_profile(
                simulation_profile_id
            )
            refreshed_parameters = _case_deterministic_parameters(
                simulation_parameters
            )
            if deterministic_parameters is not None:
                refreshed_parameters["work_section"] = deterministic_parameters.get(
                    "work_section",
                    refreshed_parameters["work_section"],
                )
            simulation_parameters["movement"]["default_work_section"] = (
                refreshed_parameters["work_section"]
            )
            self._write_json(
                root / "input" / "simulation_parameters.json",
                simulation_parameters,
            )
            manifest["simulation_parameters"] = simulation_parameters
            manifest["simulation_profile"] = simulation_profile
            manifest["deterministic_parameters"] = refreshed_parameters
        elif deterministic_parameters is not None:
            manifest["deterministic_parameters"] = deterministic_parameters
        self._write_json(root / "manifest.json", manifest)
        return manifest

    def list(self) -> Iterable[Dict[str, Any]]:
        runs_root = self.runs_root
        if not runs_root.exists():
            return []

        cases = []
        for manifest_file in sorted(runs_root.glob("*/manifest.json"), reverse=True):
            with manifest_file.open("r", encoding="utf-8") as stream:
                manifest = json.load(stream)
                if manifest.get("irradiator_type", "IV") == self.irradiator_type:
                    cases.append(self._with_step_metadata(manifest, manifest_file.parent))
        return cases

    def delete(self, case_id: str) -> None:
        """Elimina el espacio autocontenido de un caso validado."""
        case_root = self.root(case_id)
        shutil.rmtree(case_root)

    @property
    def trash_root(self) -> Path:
        return self.output_root / "Papelera"

    def trash(self, case_id: str) -> None:
        """Retira un caso sin perder sus entradas ni resultados."""
        source = self.root(case_id)
        destination = self.trash_root / case_id
        self.trash_root.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("Ya existe una copia de este caso en la papelera")
        source.rename(destination)

    def list_trash(self) -> Iterable[Dict[str, Any]]:
        cases = []
        for path in sorted(self.trash_root.glob("*/manifest.json"), reverse=True):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("irradiator_type", "IV") == self.irradiator_type:
                cases.append({key: manifest.get(key) for key in ("case_id", "name", "workflow")})
        return cases

    def restore(self, case_id: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", case_id):
            raise ValueError("Identificador de caso inválido")
        source = self.trash_root / case_id
        if not (source / "manifest.json").is_file():
            raise FileNotFoundError("Caso no encontrado en la papelera")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("irradiator_type", "IV") != self.irradiator_type:
            raise FileNotFoundError("El caso pertenece a otra categoría de irradiador")
        destination = self.runs_root / case_id
        if destination.exists():
            raise ValueError("Ya existe un caso con este identificador")
        self.runs_root.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        return self.get(case_id)

    def set_step_input(
        self,
        case_id: str,
        original_name: str,
        size_bytes: int,
        materials=None,
        material_tags=None,
    ) -> Dict[str, Any]:
        """Registra el STEP persistido como parte de la instantánea del caso."""
        case_root = self.root(case_id)
        if materials is not None or material_tags is not None:
            if materials is None or material_tags is None:
                raise ValueError("Los materiales y tags del STEP deben registrarse juntos")
            manifest = self.set_step_materials(case_id, materials, material_tags)
        else:
            manifest = self.get(case_id)
        manifest["step_input"] = {
            "kind": "STEP",
            "original_name": Path(original_name).name or "load.step",
            "stored_name": "load.step",
            "size_bytes": size_bytes,
        }
        if "load.step" not in manifest["input_snapshot"]:
            manifest["input_snapshot"].append("load.step")
        self._write_json(case_root / "manifest.json", manifest)
        return manifest

    def set_step_materials(
        self,
        case_id: str,
        materials,
        material_tags,
    ) -> Dict[str, Any]:
        """Guarda la composición exacta de cada tag de material presente en el STEP."""
        case_root = self.root(case_id)
        tags = sorted({str(tag).strip().lower() for tag in material_tags if str(tag).strip()})
        normalized = []
        seen = set()
        for item in materials:
            name = str(item.get("name", "")).strip().lower()
            if name in seen:
                raise ValueError("El material '%s' está repetido" % name)
            seen.add(name)
            fraction_type = item.get("fraction_type")
            if fraction_type is None:
                fraction_type = "weight"
            normalized.append(
                {
                    "name": name,
                    "elements": dict(item.get("elements", {})),
                    "density": float(item.get("density", 0)),
                    "fraction_type": str(fraction_type).strip().lower(),
                }
            )
            if normalized[-1]["fraction_type"] not in {"atomic", "weight"}:
                raise ValueError("La base de fracciones debe ser atomic o weight")
        if seen != set(tags):
            missing = sorted(set(tags) - seen)
            extra = sorted(seen - set(tags))
            details = []
            if missing:
                details.append("faltan: %s" % ", ".join(missing))
            if extra:
                details.append("no pertenecen al STEP: %s" % ", ".join(extra))
            raise ValueError("Los materiales deben coincidir con el STEP (%s)" % "; ".join(details))

        calibration_path = self.config_root / "materials.json"
        calibration_by_name = {}
        if calibration_path.is_file():
            with calibration_path.open("r", encoding="utf-8") as stream:
                calibration_by_name = {
                    item["name"].strip().lower(): item
                    for item in json.load(stream).get("materials", [])
                }
        snapshot = []
        for item in normalized:
            merged = dict(calibration_by_name.get(item["name"], {}))
            merged.update(item)
            snapshot.append(merged)
        if "air" not in seen and "air" in calibration_by_name:
            # El aire sigue siendo el medio de referencia dosimétrica aunque el
            # STEP suministrado no lo declare como componente transportable.
            snapshot.append(dict(calibration_by_name["air"]))
        self._write_json(case_root / "input" / "materials.json", {"materials": snapshot})

        manifest = self.get(case_id)
        manifest["step_material_tags"] = tags
        manifest["step_materials"] = normalized
        if "materials.json" not in manifest["input_snapshot"]:
            manifest["input_snapshot"].append("materials.json")
        self._write_json(case_root / "manifest.json", manifest)
        return manifest

    def set_stl_input(
        self,
        case_id: str,
        original_name: str,
        size_bytes: int,
    ) -> Dict[str, Any]:
        """Registra el STL determinista persistido en la instantánea del caso."""
        case_root = self.root(case_id)
        manifest = self.get(case_id)
        manifest["stl_input"] = {
            "kind": "STL",
            "original_name": Path(original_name).name or "world_load.stl",
            "stored_name": "world_load.stl",
            "size_bytes": size_bytes,
        }
        if "world_load.stl" not in manifest["input_snapshot"]:
            manifest["input_snapshot"].append("world_load.stl")
        self._write_json(case_root / "manifest.json", manifest)
        return manifest

    @staticmethod
    def _with_step_metadata(
        manifest: Dict[str, Any],
        case_root: Path,
    ) -> Dict[str, Any]:
        """Completa metadatos para casos creados antes de registrar el nombre original."""
        simulation_parameters = manifest.get("simulation_parameters")
        if simulation_parameters is None:
            snapshot = case_root / "input" / "simulation_parameters.json"
            if snapshot.is_file():
                with snapshot.open("r", encoding="utf-8") as stream:
                    simulation_parameters = json.load(stream)
            else:
                simulation_parameters = DEFAULT_SIMULATION_PARAMETERS
            simulation_parameters = _normalize_simulation_parameters(
                simulation_parameters
            )
            manifest["simulation_parameters"] = simulation_parameters
        else:
            simulation_parameters = _normalize_simulation_parameters(
                simulation_parameters
            )
            manifest["simulation_parameters"] = simulation_parameters
        manifest.setdefault(
            "simulation_profile",
            {"profile_id": "configuracion-anterior", "name": "Configuración anterior"},
        )
        manifest.setdefault(
            "deterministic_parameters",
            _case_deterministic_parameters(simulation_parameters),
        )
        step_path = case_root / "input" / "load.step"
        if step_path.is_file():
            metadata = manifest.get("step_input") or {}
            manifest["step_input"] = {
                "kind": "STEP",
                "original_name": metadata.get("original_name") or step_path.name,
                "stored_name": step_path.name,
                "size_bytes": step_path.stat().st_size,
            }
        else:
            manifest["step_input"] = None
        stl_path = case_root / "input" / "world_load.stl"
        if stl_path.is_file():
            metadata = manifest.get("stl_input") or {}
            manifest["stl_input"] = {
                "kind": "STL",
                "original_name": metadata.get("original_name") or stl_path.name,
                "stored_name": stl_path.name,
                "size_bytes": stl_path.stat().st_size,
            }
        else:
            manifest["stl_input"] = None
        manifest.setdefault("step_material_tags", [])
        manifest.setdefault("step_materials", [])
        return manifest

    def root(self, case_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", case_id):
            raise ValueError("Identificador de caso inválido")
        root = self.runs_root / case_id
        if not (root / "manifest.json").is_file():
            raise FileNotFoundError("Caso no encontrado: %s" % case_id)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("irradiator_type", "IV") != self.irradiator_type:
            raise FileNotFoundError("El caso pertenece a otra categoría de irradiador")
        return root

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
        temporary.replace(path)
