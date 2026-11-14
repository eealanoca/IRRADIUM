"""Proceso aislado para ejecutar un caso OpenMC/DAGMC."""

import json
import os
import math
import sys
import time
import traceback
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np

from sim_gamma.compute_resources import resolve_cpu_threads


STATISTICAL_EVALUATION_CELL_SIZE_CM = 3.0


def write_json(path: Path, data: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
    temporary.replace(path)


def write_status(path: Path, request: dict, stage: str, progress: int, **extra) -> None:
    document = {
        "case_id": request["case_id"],
        "run_id": request["run_id"],
        "state": "running",
        "stage": stage,
        "progress": progress,
        "execution_enabled": True,
        **extra,
    }
    if "route_study" in request:
        document["route_study"] = request["route_study"]
    write_json(path, document)
    if "route_progress" in request:
        route_path, index, count, name = request["route_progress"]
        write_json(route_path, {
            **document,
            "state": "running",
            "progress": min(99, (index * 100 + progress) // count),
            "position_name": name,
            "position_number": index + 1,
            "position_count": count,
        })


def clear_previous_transport_outputs(work_root: Path) -> None:
    """Elimina únicamente resultados OpenMC pertenecientes a otra ejecución.

    La geometría DAGMC se conserva porque es una entrada costosa de generar,
    pero los statepoints y archivos de control deben pertenecer a una sola
    ejecución. Esto es esencial cuando un trigger termina antes que la corrida
    anterior: de otro modo el posprocesado podría seleccionar el statepoint
    viejo con el número de batch más alto.
    """

    for pattern in (
        "statepoint.*.h5",
        "source.*.h5",
        "surface_source.*.h5",
    ):
        for path in work_root.glob(pattern):
            path.unlink(missing_ok=True)
    for filename in (
        "summary.h5",
        "weight_windows.h5",
        "response_material_calibration.json",
    ):
        (work_root / filename).unlink(missing_ok=True)


def tally_converged(summary: dict, expected_cells: int, target: float) -> bool:
    """Exige incertidumbre válida y bajo el umbral en cada celda material."""

    maximum = summary.get("relative_error_maximum")
    measured_cells = int(summary.get("cells_with_error_estimate", 0) or 0)
    return bool(
        int(expected_cells) > 0
        and measured_cells == int(expected_cells)
        and maximum is not None
        and np.isfinite(maximum)
        and float(maximum) <= float(target)
    )


def calibration_materials(materials_path: Path, required_tags) -> tuple:
    """Crea cada material DAGMC a partir de la composición guardada con el STEP."""

    import openmc

    with materials_path.open("r", encoding="utf-8") as stream:
        configured = json.load(stream).get("materials", [])
    by_name = {item["name"].lower(): item for item in configured}
    missing = sorted(set(required_tags) - set(by_name))
    if missing:
        raise ValueError(
            "Falta configurar la composición de estos materiales STEP: %s"
            % ", ".join(missing)
        )

    materials = openmc.Materials()
    for tag in sorted(set(required_tags)):
        source = by_name[tag]
        material = openmc.Material(name=tag)
        fraction_type = str(
            source.get("fraction_type", "weight")
        ).strip().lower()
        if fraction_type not in {"atomic", "weight"}:
            raise ValueError("Base de fracciones inválida para el material %s" % tag)
        percent_type = "ao" if fraction_type == "atomic" else "wo"
        for element, fraction in source["elements"].items():
            material.add_element(element, fraction, percent_type=percent_type)
        material.set_density("g/cm3", source["density"])
        materials.append(material)
    return materials, []


def step_components(step_path: Path, cache_root: Path):
    from sim_gamma.application.services.cad_preview import CadPreviewService

    service = CadPreviewService(cache_root)
    manifest = service.derive_component_meshes(step_path)
    components = [
        {
            **part,
            "path": service.component_mesh_path(manifest["digest"], part["file"]),
        }
        for part in manifest["parts"]
    ]
    tags = {
        part["material"]
        for part in components
        if part["name"].strip()
    }
    return components, tags


def combined_load_surface(components, destination: Path) -> Path:
    """Une internamente los componentes de carga, excluyendo el volumen air."""

    import trimesh

    load_parts = [
        trimesh.load_mesh(part["path"], force="mesh", process=False)
        for part in components
        if part["material"] != "air"
    ]
    if not load_parts:
        raise ValueError("El STEP no contiene un componente de carga distinto de air")
    surface = trimesh.util.concatenate(load_parts)
    from sim_gamma.workers.mesh_storage import detach_surface_before_write
    detach_surface_before_write(destination)
    surface.export(destination)
    return destination


def combined_material_surfaces(components, destination_root: Path):
    """Une componentes con el mismo tag y excluye el volumen exterior air."""
    import trimesh

    grouped = {}
    for part in components:
        if part["material"] == "air":
            continue
        grouped.setdefault(part["material"], []).append(
            trimesh.load_mesh(part["path"], force="mesh", process=False)
        )
    if not grouped:
        raise ValueError("El STEP no contiene materiales dentro de la carga")
    destination_root.mkdir(parents=True, exist_ok=True)
    surfaces = {}
    for material, meshes in grouped.items():
        destination = destination_root / (material + ".stl")
        from sim_gamma.workers.mesh_storage import detach_surface_before_write
        detach_surface_before_write(destination)
        trimesh.util.concatenate(meshes).export(destination)
        surfaces[material] = destination
    return surfaces


def load_surface_bounds(surface_path: Path, length_scale_to_cm: float = 0.1):
    """Devuelve la envolvente de la carga, expresada en centímetros para OpenMC."""
    import trimesh

    surface = trimesh.load_mesh(surface_path, force="mesh", process=False)
    return (surface.bounds * float(length_scale_to_cm)).tolist()


def translation_to_target_center(bounds, target_center):
    """Calcula la traslación que sitúa la carga en la sección seleccionada."""

    if len(bounds) != 2 or any(len(values) != 3 for values in bounds):
        raise ValueError("Los límites de la carga no son tridimensionales")
    if len(target_center) != 3:
        raise ValueError("El centro de la sección debe contener X, Y y Z")
    native_center = [
        (float(bounds[0][axis]) + float(bounds[1][axis])) / 2.0
        for axis in range(3)
    ]
    return [float(target_center[axis]) - native_center[axis] for axis in range(3)]


def translate_surface(surface_path: Path, translation_cm, length_scale_to_cm: float = 0.1):
    """Traslada una superficie triangulada conservando sus unidades CAD originales."""

    if len(translation_cm) != 3 or length_scale_to_cm <= 0:
        raise ValueError("La traslación de la superficie no es válida")
    if not any(translation_cm) and surface_path.is_file():
        return

    import trimesh
    surface = trimesh.load_mesh(surface_path, force="mesh", process=False)
    surface.apply_translation(
        [float(value) / float(length_scale_to_cm) for value in translation_cm]
    )
    from sim_gamma.workers.mesh_storage import detach_surface_before_write
    detach_surface_before_write(surface_path)
    surface.export(surface_path)


def run(request_path: Path, *, route_context=None) -> None:
    workflow_started = time.perf_counter()
    with request_path.open("r", encoding="utf-8") as stream:
        request = json.load(stream)

    case_root = Path(request["case_root"])
    input_root = case_root / "input"
    run_root = case_root / "monte_carlo"
    route_path = input_root / "route.json"
    if route_context is None and route_path.is_file():
        return run_route(request_path, request, route_path)

    output_root = run_root
    if route_context is not None:
        index, count, position, activity_date, route = route_context
        from sim_gamma.domain.study_recovery import position_output_name
        output_root = run_root / "positions" / request["run_id"] / position_output_name(index, route)
        request["route_progress"] = (run_root / "status.json", index, count, position["name"])
    work_root = output_root / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    status_path = output_root / "status.json"
    worker_path = output_root / "worker.json"
    run_id = request["run_id"]
    step_path = input_root / "load.step"
    deterministic_result_path = case_root / "deterministic" / "result.json"
    simulation_path = input_root / "simulation_parameters.json"
    required_paths = [step_path, simulation_path]
    if route_context is None:
        required_paths.append(deterministic_result_path)
    for required in required_paths:
        if not required.is_file():
            raise FileNotFoundError("Entrada Monte Carlo faltante: %s" % required)

    deterministic = {}
    if route_context is None:
        with deterministic_result_path.open("r", encoding="utf-8") as stream:
            deterministic = json.load(stream)
    with simulation_path.open("r", encoding="utf-8") as stream:
        simulation = json.load(stream)
    if route_context is not None:
        from sim_gamma.domain.route_study import study_simulation

        simulation = study_simulation(simulation, route)
    monte_carlo = simulation.get("monte_carlo", {})
    compute = simulation.get("compute", {})
    particles = int(monte_carlo.get("particles", 100_000_000))
    requested_batches = int(monte_carlo.get("batches", 100))
    cell_size = float(monte_carlo.get("cell_size_cm", 2.0))
    volume_samples = int(monte_carlo.get("material_volume_samples_per_axis", 5 if cell_size <= 1 else 3))
    dose_tally = str(monte_carlo.get("dose_tally", "flux")).strip().lower()
    if dose_tally == "phits":
        from phits_extension.worker_cli import run as run_phits

        if route_context is None:
            return run_phits(request_path)
        return run_phits(request_path, route_context=route_context)
    if dose_tally not in {"flux", "heating", "alanine"}:
        raise ValueError("El método Monte Carlo debe usar flux, heating, alanine o phits")
    calculation_mode = {
        "flux": "fast",
        "heating": "intensive",
        "alanine": "alternative",
    }[dose_tally]
    reuse_fine_evaluation = bool(
        route_context is not None and route.get("accumulated_error_target") and dose_tally == "heating"
        and route.get("variance_reduction") in {"source_box", "source_mixture", "source_scene", "analog"}
    )
    evaluation_cell_size = cell_size if reuse_fine_evaluation else STATISTICAL_EVALUATION_CELL_SIZE_CM
    intensive_estimator = "direct" if dose_tally == "heating" else None
    adaptive_statistics = bool(monte_carlo.get("adaptive_statistics", True))
    relative_error_target = float(
        monte_carlo.get("relative_error_target", 0.03)
    )
    use_survival_biasing = bool(monte_carlo.get("survival_biasing", False))
    weight_window_method = str(
        monte_carlo.get(
            "weight_window_method",
            "none",
        )
    ).strip().lower()
    weight_window_value = str(
        monte_carlo.get("weight_window_value", "mean")
    ).strip().lower()
    weight_window_threshold = float(monte_carlo.get("weight_window_threshold", 0.7))
    weight_window_ratio = float(monte_carlo.get("weight_window_ratio", 5.0))
    directional_source_biasing = bool(
        monte_carlo.get("directional_source_biasing", True)
    )
    if route_context is not None:
        directional_source_biasing = False
    event_based = bool(monte_carlo.get("event_based", False))
    electron_treatment = monte_carlo.get(
        "electron_treatment",
        "ttb" if dose_tally == "heating" else None,
    )
    random_seed = monte_carlo.get("random_seed")
    if route_context is not None:
        random_seed = 421 + route["study_position_indices"][index]
    tally_name = {
        "flux": "Flux",
        "heating": "DirectHeating",
        "alanine": "AlanineDose",
    }[dose_tally]
    evaluation_tally_name = tally_name + "Evaluation"
    cpu_cores = int(compute.get("cpu_cores", -1))
    cpu_threads_used = resolve_cpu_threads(cpu_cores)
    radionuclide = simulation.get("radionuclide", "Co-60")
    if particles < 1:
        raise ValueError("El número de partículas debe ser un entero mayor que cero")
    if requested_batches < 2:
        raise ValueError("El número de batches debe ser mayor o igual que dos")

    write_status(
        status_path,
        request,
        "preparing-step-and-dagmc",
        5,
        started_at=datetime.now(timezone.utc).isoformat(),
        particles=particles,
        dose_tally=dose_tally,
        cpu_cores=cpu_cores,
        cpu_threads_used=cpu_threads_used,
    )
    write_json(
        worker_path,
        {
            "case_id": request["case_id"],
            "run_id": run_id,
            "pid": os.getpid(),
            "process_group_id": os.getpgrp(),
            "request_path": str(request_path.resolve()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            **(
                {"systemd_unit": request["systemd_unit"]}
                if request.get("systemd_unit")
                else {}
            ),
        },
    )

    from sim_gamma.physics.parameters import load_photon_yield, load_physical_parameters
    from sim_gamma.simulators.openmc.engine import (
        configure_simulation,
        create_geometry_tally,
        create_sparse_direct_heating_tallies,
        create_sparse_material_response_tallies,
        define_and_export_model,
        generate_openmc_files,
    )
    from sim_gamma.simulators.openmc.structured_postprocess import (
        alanine_energy_response,
        build_material_ids,
        build_material_volume_fractions,
        mesh_spec_from_openmc,
        process_sparse_material_tallies_to_artifacts,
        process_sparse_response_tallies_to_artifacts,
        sparse_mesh_material_bins,
        summarize_sparse_material_tallies,
        summarize_sparse_response_tallies,
    )
    import openmc

    # Stable IDs allow an identical position to continue its saved fixed-source tally.
    openmc.reset_auto_ids()
    components, material_tags = step_components(
        step_path,
        case_root.parent.parent / "cache" / "monte-carlo-step-components",
    )
    load_surface = combined_load_surface(components, work_root / "step_load_surface.stl")
    native_load_bounds = load_surface_bounds(load_surface)
    target_center = np.mean(native_load_bounds, axis=0) if route_context is not None else deterministic["geometric_center_cm"]
    geometry_translation_cm = translation_to_target_center(native_load_bounds, target_center)
    translate_surface(load_surface, geometry_translation_cm)
    material_surfaces = combined_material_surfaces(
        components,
        work_root / "step_material_surfaces",
    )
    for material_surface in material_surfaces.values():
        translate_surface(material_surface, geometry_translation_cm)
    if route_context is not None:
        from sim_gamma.workers.mesh_storage import share_surface_files
        share_surface_files([load_surface, *material_surfaces.values()], case_root / "monte_carlo/shared_surfaces")
    mesh_bounds = load_surface_bounds(load_surface)
    from sim_gamma.simulators.openmc.analytic_box import rectangular_surface_bounds
    analytic_box = bool(
        route_context is not None and route.get("occupancy", "isolated") == "isolated"
        and len(components) == 1 and len(material_tags) == 1
        and rectangular_surface_bounds(load_surface) is not None
    )
    world_root = output_root.parent / "geometry" if route_context is not None else work_root
    world_root.mkdir(parents=True, exist_ok=True)
    world_step = world_root / "world.step"
    if route_context is None or not world_step.is_file():
        world_step.write_bytes(step_path.read_bytes())

    route_sources = None
    transport_bounds = None
    load_instances = None
    transport_name = "world"
    transport_tags = [part["material"] for part in components]
    transport_translation = geometry_translation_cm
    transport_rotation = 0
    if route_context is not None:
        from sim_gamma.domain.route_geometry import occupied_placements
        from sim_gamma.simulators.openmc.route_sources import sources_at_position

        inventory = json.loads((input_root / "sources.json").read_text(encoding="utf-8"))
        route_sources, source_bounds = sources_at_position(
            inventory, radionuclide, activity_date, position, target_center,
            native_load_bounds[1][2] - native_load_bounds[0][2], route.get("position_reference", "center"),
        )
        if analytic_box and not material_tags.intersection({"air", "airload"}):
            from sim_gamma.simulators.openmc.analytic_box import validate_source_clearance
            validate_source_clearance(route_sources, mesh_bounds)
        instances = occupied_placements(route, position, native_load_bounds)
        if route.get("variance_reduction") == "source_scene":
            from sim_gamma.simulators.openmc.scene_source import scene_mixture
            boxes = [mesh_bounds] + [np.array([item["low"], item["high"]]) for item in instances if item["name"] != position["name"]]
            route_sources = scene_mixture(route_sources, boxes, route.get("source_mixture_fraction", .9))
        elif route.get("variance_reduction") in {"source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene"}:
            from sim_gamma.simulators.openmc.mixture_source import focus_mixture

            route_sources = focus_mixture(route_sources, mesh_bounds, route.get("source_mixture_fraction", .9),
                                          target_box=route.get("variance_reduction") in {"source_box", "source_box_windows", "source_box_magic", "source_scene"})
        if route.get("occupancy") == "occupied":
            load_instances = instances
            if len(components) <= 8 and len(material_tags) == 1 and "airload" not in material_tags:
                from sim_gamma.application.services.route_cad import write_occupied_step

                material = next(iter(material_tags))
                transport_name = "occupied"
                from sim_gamma.simulators.openmc.geometry_contract import occupied_geometry_key
                world_root = world_root / ("occupied-" + occupied_geometry_key(step_path, route))
                world_root.mkdir(parents=True, exist_ok=True)
                occupied_step = world_root / "occupied.step"
                count = write_occupied_step(step_path, occupied_step, route, native_load_bounds, material)
                transport_tags = [material] * count
                transport_translation = instances[0]["translation"]
                transport_rotation = instances[0]["angle"]
                load_instances = None
        transport_bounds = [
            np.min([source_bounds[0], *(instance["low"] for instance in instances)], axis=0) - 50,
            np.max([source_bounds[1], *(instance["high"] for instance in instances)], axis=0) + 50,
        ]

    previous_directory = Path.cwd()
    os.chdir(work_root)
    try:
        (work_root / "spectral_heating_response.json").unlink(missing_ok=True)
        h5m_path = world_root / f"{transport_name}.h5m"
        generate_openmc_files(
            str(world_root), transport_name, material_tags=transport_tags,
            **({"implicit_air": True} if route_context is not None else {}),
        )
        if not h5m_path.is_file():
            raise FileNotFoundError("CAD_to_OpenMC no generó world.h5m")
        analytic_exterior = None
        if analytic_box:
            from sim_gamma.simulators.openmc.analytic_box import homogeneous_box_exterior
            analytic_box, analytic_exterior = homogeneous_box_exterior(h5m_path, next(iter(material_tags)))

        write_status(
            status_path,
            request,
            "building-openmc-model",
            18,
            particles=particles,
            cpu_cores=cpu_cores,
            cpu_threads_used=cpu_threads_used,
        )
        materials, aliases = calibration_materials(
            input_root / "materials.json",
            material_tags | {"air"} if route_context is not None else material_tags,
        )
        heating_materials = [
            material
            for material in materials
            if str(material.name).strip().lower() != "air"
        ]
        materials.export_to_xml(work_root / "materials.xml")
        geometry, tallies, mesh, evaluation_mesh = create_geometry_tally(
            h5m_filename=str(h5m_path),
            cell_size=cell_size,
            mesh_bounds=mesh_bounds,
            geometry_translation=transport_translation,
            geometry_rotation_degrees=transport_rotation,
            evaluation_cell_size=evaluation_cell_size,
            return_evaluation_mesh=True,
            # For the two response-based modes this call constructs the exact
            # geometry and meshes; their production tallies are installed
            # below after the material occupancy has been resolved.
            tally_score=(
                "heating" if dose_tally == "heating" else "flux"
            ),
            evaluation_relative_error_target=(
                relative_error_target
                if adaptive_statistics and dose_tally == "flux"
                else None
            ),
            heating_materials=(
                heating_materials
                if dose_tally == "heating"
                else None
            ),
            include_direct_heating_diagnostic=False,
            analytic_load_material=next(material for material in materials if material.name in material_tags) if analytic_box else None,
            exact_mesh_bounds=bool(route_context is not None and route.get("analysis_method") == "monte_carlo_air"),
            **({
                "transport_bounds": transport_bounds,
                "load_instances": load_instances,
                "surrounding_material": (None if analytic_box and analytic_exterior is None
                                         else next(material for material in materials if material.name == "air")),
            } if route_context is not None else {}),
        )
        reference_tag = "airload" if route_context is not None and "airload" in material_tags and dose_tally == "flux" else "air"
        if route_context is not None and dose_tally == "flux":
            reference_tag = route.get("flux_response_material", reference_tag)
        decay_factor, dose_factor = load_physical_parameters(
            radionuclide=radionuclide,
            material_name=reference_tag,
            source_file=input_root / "sources.json",
            materials_file=input_root / "materials.json",
        )
        photon_yield = load_photon_yield(
            radionuclide=radionuclide,
            source_file=input_root / "sources.json",
        )

        mesh_spec = mesh_spec_from_openmc(mesh)
        evaluation_mesh_spec = mesh_spec_from_openmc(evaluation_mesh)
        material_fractions = None
        evaluation_material_fractions = None
        if dose_tally in {"heating", "alanine", "flux"}:
            body_surfaces = {}
            for part in components:
                if part["material"] != "air":
                    body_surfaces.setdefault(part["material"], []).append(part["path"])
            preparation_memory = float(os.environ.get("IRRADIUM_STUDY_MEMORY_LIMIT_MB",
                request.get("route_study", {}).get("memory_limit_mb", 4096)))

            def preparation_progress(grid):
                def report(details):
                    write_status(status_path, request, "preparing-material-occupancy", 18,
                                 particles=particles, cpu_cores=cpu_cores, cpu_threads_used=cpu_threads_used,
                                 preparation={**details, "grid": grid})
                    print("Material occupancy: " + json.dumps({**details, "grid": grid}), flush=True)
                return report

            preparation_started = time.perf_counter()
            material_fractions, material_labels = build_material_volume_fractions(
                material_surfaces,
                mesh_spec,
                case_root.parent.parent / "cache" / "monte-carlo-material-volume-fractions",
                length_scale_to_cm=0.1,
                samples_per_axis=volume_samples,
                split_disjoint=route_context is not None,
                component_surface_files=body_surfaces,
                component_translation_cm=geometry_translation_cm,
                cpu_threads=cpu_threads_used, memory_limit_mb=preparation_memory,
                progress_callback=preparation_progress("dose"),
            )
            coverage = np.sum(material_fractions, axis=0, dtype=np.float32)
            material_ids = np.zeros(mesh_spec.cell_count, dtype=np.uint16)
            occupied = coverage > 0
            material_ids[occupied] = (
                np.argmax(material_fractions[:, occupied], axis=0) + 1
            )
            # A majority-occupied rule avoids presenting arbitrarily thin CAD
            # slivers as full dose voxels and remains consistent across meshes.
            mask = coverage >= 0.5
            if evaluation_mesh_spec == mesh_spec:
                evaluation_material_fractions, evaluation_labels = material_fractions, material_labels
            else:
                evaluation_material_fractions, evaluation_labels = build_material_volume_fractions(
                    material_surfaces,
                    evaluation_mesh_spec,
                    case_root.parent.parent / "cache" / "monte-carlo-material-volume-fractions",
                    length_scale_to_cm=0.1,
                    samples_per_axis=volume_samples,
                    component_surface_files=body_surfaces,
                    component_translation_cm=geometry_translation_cm,
                    cpu_threads=cpu_threads_used, memory_limit_mb=preparation_memory,
                    progress_callback=preparation_progress("evaluation"),
                )
            material_preparation_seconds = time.perf_counter() - preparation_started
            print("Material occupancy completed in %.3f s" % material_preparation_seconds, flush=True)
            evaluation_coverage = np.sum(
                evaluation_material_fractions, axis=0, dtype=np.float32
            )
            evaluation_material_ids = np.zeros(
                evaluation_mesh_spec.cell_count, dtype=np.uint16
            )
            evaluation_occupied = evaluation_coverage > 0
            evaluation_material_ids[evaluation_occupied] = (
                np.argmax(
                    evaluation_material_fractions[:, evaluation_occupied], axis=0
                )
                + 1
            )
            evaluation_mask = evaluation_coverage >= 0.5
        else:
            material_ids, material_labels = build_material_ids(
                material_surfaces,
                mesh_spec,
                case_root.parent.parent / "cache" / "monte-carlo-material-masks",
                length_scale_to_cm=0.1,
            )
            mask = material_ids != 0
            evaluation_material_ids, evaluation_labels = build_material_ids(
                material_surfaces,
                evaluation_mesh_spec,
                case_root.parent.parent / "cache" / "monte-carlo-material-masks",
                length_scale_to_cm=0.1,
            )
            evaluation_mask = evaluation_material_ids != 0

        with (input_root / "materials.json").open("r", encoding="utf-8") as stream:
            configured_materials = json.load(stream).get("materials", [])
        reference_material = next(
            (
                material
                for material in configured_materials
                if str(material.get("name", "")).strip().lower() == reference_tag
            ),
            {},
        )
        quick_reference_medium = str(
            reference_material.get("dosimeter_medium")
            or reference_material.get("transport_medium")
            or reference_material.get("name")
            or "configured-reference-medium"
        ).strip().lower()
        openmc_materials_by_name = {
            str(material.name).strip().lower(): material
            for material in heating_materials
        }
        fine_tally_names = None
        evaluation_tally_names = None
        material_response_errors = None
        pilot_particles = 0
        pilot_wall_seconds = 0.0

        fine_bins = sparse_mesh_material_bins(
            material_fractions,
            material_labels,
            openmc_materials_by_name,
        )
        evaluation_bins = sparse_mesh_material_bins(
            evaluation_material_fractions,
            evaluation_labels,
            openmc_materials_by_name,
        )
        if route_context is not None and adaptive_statistics:
            from sim_gamma.domain.route_study import active_material_bins
            fine_bins = active_material_bins(fine_bins, mask)
            evaluation_bins = active_material_bins(evaluation_bins, evaluation_mask)

        if dose_tally == "heating":
            tallies, fine_tally_names = create_sparse_direct_heating_tallies(
                mesh,
                fine_bins,
                relative_error_target=(
                    relative_error_target if adaptive_statistics else None
                ),
                name_prefix="DirectHeating",
            )
            if reuse_fine_evaluation:
                # The interval is checked on this very mesh. Reuse its tally
                # rather than score another mesh at every photon collision.
                evaluation_tally_names = fine_tally_names
                evaluation_tally_name = tally_name
            else:
                evaluation_tallies, evaluation_tally_names = create_sparse_direct_heating_tallies(
                    evaluation_mesh,
                    evaluation_bins,
                    relative_error_target=(
                        relative_error_target if adaptive_statistics else None
                    ),
                    name_prefix="DirectHeatingEvaluation",
                )
                for evaluation_tally in evaluation_tallies:
                    tallies.append(evaluation_tally)
        else:
            # Rápido: track-length flux sin ponderación energética, convertido
            # con el coeficiente discreto original del radionúclido.
            # Alternativo: respuesta dosimétrica propia de alanina, integrada
            # directamente durante el transporte. Ninguno usa heating como
            # calibración, de modo que ambos permanecen independientes.
            if dose_tally == "alanine":
                response_energy_ev, response_values = alanine_energy_response()
            else:
                response_energy_ev, response_values = None, None
            tallies, fine_tally_names, evaluation_tally_names = (
                create_sparse_material_response_tallies(
                    mesh,
                    evaluation_mesh,
                    fine_bins,
                    evaluation_bins,
                    relative_error_target=(relative_error_target if adaptive_statistics else None),
                    energy_ev=response_energy_ev,
                    response=response_values,
                    name_prefix={
                        "flux": "FastDose",
                        "alanine": "AlanineDose",
                    }[dose_tally],
                )
            )

        if route_context is not None and adaptive_statistics:
            for tally in tallies:
                for trigger in tally.triggers:
                    trigger.ignore_zeros = False
                    trigger.threshold *= .99

        window_mesh = evaluation_mesh
        if route_context is not None and weight_window_method == "magic":
            window_mesh = openmc.RegularMesh()
            window_mesh.lower_left, window_mesh.upper_right = transport_bounds
            window_mesh.dimension = np.maximum(1, np.ceil((transport_bounds[1] - transport_bounds[0]) / 20)).astype(int)
        settings = configure_simulation(
            deterministic.get("step_angle_degrees_best_real"),
            [value / 10.0 for value in deterministic.get("best_length_points_mm", [])],
            target_center,
            str(input_root / "sources.json"),
            particles=particles,
            decay_factor=decay_factor,
            radionuclide=radionuclide,
            verbose=False,
            variance_reduction_mesh=mesh,
            variance_reduction_batches=requested_batches,
            adaptive_max_batches=requested_batches,
            adaptive_statistics=adaptive_statistics,
            adaptive_relative_error_target=relative_error_target,
            electron_treatment=electron_treatment,
            use_survival_biasing=use_survival_biasing,
            # Si el usuario habilita MAGIC explícitamente, su guía usa la malla
            # auxiliar de 3 cm. La convergencia final siempre se comprueba
            # también sobre la resolución seleccionada por el usuario.
            weight_window_mesh=window_mesh,
            weight_window_method=weight_window_method,
            weight_window_value=weight_window_value,
            weight_window_threshold=weight_window_threshold,
            weight_window_ratio=weight_window_ratio,
            directional_source_bounds=(mesh_bounds if directional_source_biasing and route_context is None else None),
            random_seed=random_seed,
            **({"source_distributions": route_sources} if route_context is not None else {}),
        )
        if route_context is not None:
            settings.photon_transport = True
        if route_context is not None and route.get("variance_reduction") == "source_box_windows":
            local_window = openmc.RegularMesh()
            local_window.lower_left, local_window.upper_right = mesh_bounds
            local_window.dimension = (1, 1, 1)
            settings.weight_windows = [openmc.WeightWindows(
                local_window, lower_ww_bounds=[.002], upper_bound_ratio=5,
                energy_bounds=[0, 2.e6], particle_type="photon", max_split=8, max_lower_bound_ratio=5)]
            settings.weight_windows_on = True
            settings.weight_window_checkpoints = {"surface": True, "collision": True}
            settings.max_history_splits = 128
        clear_previous_transport_outputs(work_root)
        environment_openmc = Path(sys.executable).resolve().parent / "openmc"
        openmc_exec = str(environment_openmc) if environment_openmc.is_file() else "openmc"
        magic_enabled = len(settings.weight_window_generators) > 0
        biased_window_scale = None
        if route_context is not None and magic_enabled:
            started_pilot = time.perf_counter()
            pilot_root = work_root / "magic-pilot"
            pilot_root.mkdir(exist_ok=True)
            clear_previous_transport_outputs(pilot_root)
            pilot_settings = deepcopy(settings)
            pilot_settings.seed = int(settings.seed) + 1_000_001
            pilot_settings.batches = max(2, min(12, requested_batches))
            pilot_settings.particles = max(100, min(100_000, particles // (pilot_settings.batches * 10)))
            pilot_settings.trigger_active = False
            pilot_settings.weight_window_generators[0].max_realizations = pilot_settings.batches
            if route.get("variance_reduction") == "source_box_magic":
                # Learn the physical fluence before applying windows rescaled
                # for the weighted directional source in the production run.
                pilot_settings.weight_window_generators[0].on_the_fly = False
            pilot_particles = pilot_settings.batches * pilot_settings.particles
            openmc.Model(geometry=geometry, materials=materials, settings=pilot_settings).export_to_xml(pilot_root)
            write_status(status_path, request, "training-openmc-photon-weight-windows", 24, particles=pilot_particles)
            openmc.run(threads=cpu_threads_used, openmc_exec=openmc_exec, cwd=pilot_root)
            window_file = pilot_root / "weight_windows.h5"
            if not window_file.is_file():
                raise RuntimeError("OpenMC no generó las ventanas de peso solicitadas")
            settings.weight_window_generators = []
            settings.weight_windows_file = str(window_file)
            settings.weight_windows_on = True
            if route.get("variance_reduction") == "source_box_magic":
                biased_window_scale = float(np.clip(np.median([
                    getattr(source, "_irradium_window_scale", 1.) for source in route_sources]), 1e-6, 1.))
                windows = openmc.hdf5_to_wws(window_file)
                for window in windows:
                    window.lower_ww_bounds = window.lower_ww_bounds * biased_window_scale
                    window.upper_ww_bounds = window.upper_ww_bounds * biased_window_scale
                    window.max_split = 8
                    window.max_lower_bound_ratio = 5
                settings.weight_windows_file = None
                settings.weight_windows = windows
                settings.weight_window_checkpoints = {"surface": True, "collision": True}
                settings.max_history_splits = 128
            pilot_wall_seconds = time.perf_counter() - started_pilot
        restart_candidate = None
        if route_context is not None and route.get("restart_summary") and route.get("variance_reduction") in {"source_box", "source_mixture", "analog", "source_scene"}:
            from sim_gamma.workers.interval_restart import configure_restart
            restart_candidate = configure_restart(settings, case_root / route["restart_summary"], particles)
        checkpointed = route_context is not None and bool(os.environ.get("IRRADIUM_STUDY_DEADLINE"))
        if checkpointed:
            from sim_gamma.workers.transport_budget import configure_checkpoints
            configure_checkpoints(settings)
        settings.export_to_xml(work_root / "settings.xml")
        define_and_export_model(geometry, materials, settings, tallies)
        from sim_gamma.workers.interval_restart import compatible_restart
        restart = compatible_restart(restart_candidate, work_root / "model.xml")
        reused_histories = restart["histories"] if restart else 0

        survival_biasing = bool(settings.survival_biasing)
        cutoff = dict(settings.cutoff or {})
        variance_reduction = {
            "enabled": survival_biasing or magic_enabled or directional_source_biasing
                       or (route_context is not None and route.get("variance_reduction") in {"source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene"}),
            "method": (
                "scene-conditioned-area-mixture"
                if route_context is not None and route.get("variance_reduction") == "source_scene"
                else
                "box-conditioned-source-and-magic-photon-windows"
                if route_context is not None and route.get("variance_reduction") == "source_box_magic"
                else
                "box-conditioned-angular-mixture-and-local-splitting"
                if route_context is not None and route.get("variance_reduction") == "source_box_windows"
                else "box-conditioned-angular-mixture"
                if route_context is not None and route.get("variance_reduction") == "source_box"
                else
                "full-support-angular-mixture"
                if route_context is not None and route.get("variance_reduction") in {"source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene"}
                else
                "directional-source-biasing-and-magic-weight-windows"
                if magic_enabled and directional_source_biasing
                else "magic-photon-weight-windows"
                if magic_enabled
                else (
                    "directional-source-biasing-and-implicit-survival"
                    if survival_biasing and directional_source_biasing
                    else "implicit-survival"
                    if survival_biasing
                    else "directional-source-biasing"
                    if directional_source_biasing
                    else "analog"
                )
            ),
            "scope": "load-volume",
            "work_section": deterministic.get("work_section", "A"),
            "batches": int(settings.batches),
            "requested_batches": requested_batches,
            "particles_per_batch": int(settings.particles),
            "weight_cutoff": cutoff.get("weight"),
            "survival_weight": cutoff.get("weight_avg"),
            "particle_splitting": magic_enabled or (route_context is not None and route.get("variance_reduction") == "source_box_windows"),
            "biased_window_scale": biased_window_scale,
            "checkpointed_batching": checkpointed,
            "trigger_safety_factor": .99 if route_context is not None and adaptive_statistics else None,
            "weight_window_value": weight_window_value if magic_enabled else None,
            "weight_window_threshold": weight_window_threshold if magic_enabled else None,
            "weight_window_ratio": weight_window_ratio if magic_enabled else None,
            "directional_source_biasing": directional_source_biasing,
            "source_solid_angle_fraction_minimum": min(
                (
                    item["solid_angle_fraction"]
                    for item in settings._sim_gamma_source_biasing
                ),
                default=1.0,
            ),
            "source_solid_angle_fraction_maximum": max(
                (
                    item["solid_angle_fraction"]
                    for item in settings._sim_gamma_source_biasing
                ),
                default=1.0,
            ),
            "electron_treatment": settings.electron_treatment,
            "event_based": event_based,
            "adaptive_statistics": bool(settings.trigger_active),
            "relative_error_target": relative_error_target if settings.trigger_active else None,
            "minimum_batches": int(settings.batches),
            "maximum_batches": (
                int(settings.trigger_max_batches)
                if settings.trigger_active
                else int(settings.batches)
            ),
        }

        dose_scale = None
        evaluation_dose_scale = None
        heating_estimator = dose_tally == "heating"
        if dose_tally == "flux":
            dose_scale = dose_factor
            evaluation_dose_scale = dose_factor
        elif dose_tally == "alanine":
            dose_scale = 1.602176634e-19 * 3600.0 * photon_yield
            evaluation_dose_scale = dose_scale
        write_status(
            status_path,
            request,
            "running-openmc-transport",
            30,
            particles=particles,
            dose_tally=dose_tally,
            cpu_cores=cpu_cores,
            cpu_threads_used=cpu_threads_used,
            mesh_cells=mesh_spec.cell_count,
            active_load_cells=int(mask.sum()),
            cell_size_cm=cell_size,
            statistical_evaluation_cell_size_cm=evaluation_cell_size,
            variance_reduction=variance_reduction,
        )

        preparation_wall_seconds = (
            time.perf_counter() - workflow_started - pilot_wall_seconds
        )
        transport_started = time.perf_counter()
        transport_limited = False
        selected_checkpoint = None
        if checkpointed:
            from sim_gamma.workers.transport_budget import run_checkpointed_transport
            transport_limited, selected_checkpoint = run_checkpointed_transport(
                openmc_exec, work_root, cpu_threads_used, event_based, float(os.environ["IRRADIUM_STUDY_DEADLINE"]),
                reserve_seconds=10 if mesh_spec.cell_count <= 250_000 else 30,
                restart_file=restart["statepoint"] if restart else None)
        else:
            openmc.run(
                threads=cpu_threads_used,
                event_based=event_based,
                openmc_exec=openmc_exec,
                restart_file=restart["statepoint"] if restart else None,
            )
        main_transport_wall_seconds = time.perf_counter() - transport_started
        transport_wall_seconds = pilot_wall_seconds + main_transport_wall_seconds + (restart["transport_seconds"] if restart else 0)
        statepoints = sorted(
            work_root.glob("statepoint.*.h5"),
            key=lambda path: int(path.stem.rsplit(".", 1)[-1]),
        )
        if selected_checkpoint is not None:
            statepoints = [selected_checkpoint]
        if not statepoints:
            raise FileNotFoundError("OpenMC finalizó sin producir un statepoint")

        write_status(
            status_path,
            request,
            "postprocessing-statepoint",
            86,
            particles=particles,
            cpu_cores=cpu_cores,
            cpu_threads_used=cpu_threads_used,
            cell_size_cm=cell_size,
            variance_reduction=variance_reduction,
        )
        postprocess_started = time.perf_counter()
        if heating_estimator:
            result = process_sparse_material_tallies_to_artifacts(
                statepoints[-1],
                output_root,
                fine_tally_names,
                mesh_spec,
                material_fractions,
                material_ids,
                material_labels,
                configured_materials,
                photon_yield,
                mask,
                material_response_errors=material_response_errors,
                tally_name=tally_name,
            )
            statistical_evaluation = summarize_sparse_material_tallies(
                statepoints[-1],
                evaluation_tally_names,
                evaluation_mesh_spec,
                evaluation_material_fractions,
                evaluation_labels,
                configured_materials,
                photon_yield,
                evaluation_mask,
                material_response_errors=material_response_errors,
                tally_name=evaluation_tally_name,
            )
        else:
            occupied_fraction = np.sum(
                material_fractions, axis=0, dtype=np.float32
            )
            evaluation_occupied_fraction = np.sum(
                evaluation_material_fractions, axis=0, dtype=np.float32
            )
            result = process_sparse_response_tallies_to_artifacts(
                statepoints[-1],
                output_root,
                fine_tally_names,
                mesh_spec,
                dose_scale=dose_scale,
                inside_mask=mask,
                material_ids=material_ids,
                material_labels=material_labels,
                occupied_fraction=occupied_fraction,
                material_response_errors=None,
                material_calibration_curves=None,
                tally_name=tally_name,
            )
            statistical_evaluation = summarize_sparse_response_tallies(
                statepoints[-1],
                evaluation_tally_names,
                evaluation_mesh_spec,
                dose_scale=evaluation_dose_scale,
                inside_mask=evaluation_mask,
                occupied_fraction=evaluation_occupied_fraction,
                material_response_errors=None,
                material_calibration_curves=None,
                tally_name=evaluation_tally_name,
            )
        material_statistical_evaluation = []
        for label in evaluation_labels:
            material_mask = (
                (evaluation_material_ids == int(label["id"]))
                & evaluation_mask
            )
            if not np.any(material_mask):
                continue
            if heating_estimator:
                material_summary = summarize_sparse_material_tallies(
                    statepoints[-1],
                    evaluation_tally_names,
                    evaluation_mesh_spec,
                    evaluation_material_fractions,
                    evaluation_labels,
                    configured_materials,
                    photon_yield,
                    material_mask,
                    material_response_errors=material_response_errors,
                    tally_name=evaluation_tally_name,
                )
            else:
                material_summary = summarize_sparse_response_tallies(
                    statepoints[-1],
                    evaluation_tally_names,
                    evaluation_mesh_spec,
                    dose_scale=evaluation_dose_scale,
                    inside_mask=material_mask,
                    occupied_fraction=evaluation_occupied_fraction,
                    material_response_errors=None,
                    material_calibration_curves=None,
                    tally_name=evaluation_tally_name,
                )
            material_statistical_evaluation.append(
                {
                    "id": int(label["id"]),
                    "name": label["name"],
                    **{
                        key: material_summary.get(key)
                        for key in (
                            "minimum_dose",
                            "maximum_dose",
                            "average_dose",
                            "dose_p10",
                            "dose_p90",
                            "dose_p95",
                            "dose_p98",
                            "dose_uniformity_ratio",
                            "robust_dose_uniformity_ratio",
                            "operational_dose_uniformity_ratio",
                            "relative_error_median",
                            "relative_error_p90",
                            "relative_error_maximum",
                            "cells_relative_error_below_1_percent",
                            "cells_relative_error_below_5_percent",
                            "cells_with_error_estimate",
                        )
                    },
                }
            )
        statistical_evaluation["materials"] = material_statistical_evaluation
        evaluation_active_cells = int(evaluation_mask.sum())
        statistical_evaluation["expected_active_cells"] = evaluation_active_cells
        statistical_evaluation["all_cells_below_5_percent"] = tally_converged(
            statistical_evaluation,
            evaluation_active_cells,
            0.05,
        )
        actual_batches = int(statistical_evaluation["num_realizations"])
        simulated_particles = actual_batches * int(settings.particles)
        statistical_evaluation["material_coverage_threshold"] = 0.5
        result["raw_fine_statistics"] = {
            key: result.get(key)
            for key in (
                "minimum_dose",
                "maximum_dose",
                "average_dose",
                "dose_uniformity_ratio",
                "relative_error_median",
                "relative_error_p90",
                "relative_error_maximum",
            )
        }
        fine_mesh_convergence = {
            key: result.get(key)
            for key in (
                "relative_error_median",
                "relative_error_p90",
                "relative_error_maximum",
                "cells_relative_error_below_1_percent",
                "cells_relative_error_below_5_percent",
                "cells_relative_error_below_10_percent",
                "cells_with_error_estimate",
            )
        }
        fine_active_cells = int(mask.sum())
        fine_mesh_convergence["expected_active_cells"] = fine_active_cells
        fine_material_statistics = []
        for label in material_labels:
            material_mask = (material_ids == int(label["id"])) & mask
            if not np.any(material_mask):
                continue
            if heating_estimator:
                material_summary = summarize_sparse_material_tallies(
                    statepoints[-1],
                    fine_tally_names,
                    mesh_spec,
                    material_fractions,
                    material_labels,
                    configured_materials,
                    photon_yield,
                    material_mask,
                    material_response_errors=material_response_errors,
                    tally_name=tally_name,
                )
            else:
                material_summary = summarize_sparse_response_tallies(
                    statepoints[-1],
                    fine_tally_names,
                    mesh_spec,
                    dose_scale=dose_scale,
                    inside_mask=material_mask,
                    occupied_fraction=occupied_fraction,
                    material_response_errors=None,
                    material_calibration_curves=None,
                    tally_name=tally_name,
                )
            fine_material_statistics.append(
                {
                    "id": int(label["id"]),
                    "name": label["name"],
                    **{
                        key: material_summary.get(key)
                        for key in (
                            "minimum_dose",
                            "maximum_dose",
                            "average_dose",
                            "dose_p10",
                            "dose_p90",
                            "dose_p95",
                            "dose_p98",
                            "dose_uniformity_ratio",
                            "robust_dose_uniformity_ratio",
                            "operational_dose_uniformity_ratio",
                            "relative_error_median",
                            "relative_error_p90",
                            "relative_error_maximum",
                            "cells_relative_error_below_1_percent",
                            "cells_relative_error_below_5_percent",
                            "cells_with_error_estimate",
                        )
                    },
                }
            )
        fine_mesh_convergence["materials"] = fine_material_statistics
        result["fine_mesh_convergence"] = fine_mesh_convergence
        result["statistical_evaluation"] = statistical_evaluation
        if dose_tally == "alanine":
            result["virtual_alanine_dosimeter"] = {
                "medium": "pure-alanine",
                "response_quantity": "mass-energy-absorption-coefficient",
                "response_source": "NIST X-Ray Mass Attenuation Coefficients, Table 4: Alanine",
                "energy_range_mev": [0.001, 20.0],
                "tabulated_points": 36,
                "interpolation": "log-log",
                "model": "independent-energy-resolved-fluence-to-alanine-dose",
                "explicit_pellet_geometry": False,
                "reported_quantity": "absorbed-dose-rate-to-alanine",
            }
        for key in (
            "minimum_dose",
            "maximum_dose",
            "average_dose",
            "dose_uniformity_ratio",
            "relative_error_median",
            "relative_error_p90",
            "relative_error_maximum",
            "dose_p02",
            "dose_p05",
            "dose_p10",
            "dose_median",
            "dose_p90",
            "dose_p95",
            "dose_p98",
            "operational_dose_uniformity_ratio",
            "robust_dose_uniformity_ratio",
            "d98_d2_uniformity_ratio",
            "cells_relative_error_below_1_percent",
            "cells_relative_error_below_5_percent",
            "cells_relative_error_below_10_percent",
            "cells_with_error_estimate",
        ):
            result[key] = statistical_evaluation.get(key)
        postprocess_wall_seconds = time.perf_counter() - postprocess_started
        adaptive_converged = bool(
            settings.trigger_active
            and tally_converged(
                fine_mesh_convergence,
                fine_active_cells,
                relative_error_target,
            )
            and tally_converged(
                statistical_evaluation,
                evaluation_active_cells,
                relative_error_target,
            )
        )
        statistical_evaluation["all_cells_below_target"] = adaptive_converged
        fine_mesh_convergence["all_cells_below_target"] = adaptive_converged
        # Las tarjetas de convergencia deben mostrar la incertidumbre de la
        # malla VTI fina; la malla de 3 cm se conserva para los estadísticos de
        # dosis robustos y como comprobación adicional.
        for key in (
            "relative_error_median",
            "relative_error_p90",
            "relative_error_maximum",
            "cells_relative_error_below_1_percent",
            "cells_relative_error_below_5_percent",
            "cells_relative_error_below_10_percent",
            "cells_with_error_estimate",
        ):
            result[key] = fine_mesh_convergence.get(key)
        result.update(
            {
                "run_id": run_id,
                "backend": "openmc-0.15.3-csg" if analytic_box else "openmc-0.15.3-dagmc",
                "geometry_backend": "analytic-box" if analytic_box else "dagmc",
                "material_volume_samples_per_axis": volume_samples,
                "material_volume_method": "orthogonal-exact/triangle-aabb-stratified-v1",
                "material_preparation_seconds": material_preparation_seconds,
                "transport_geometry_revision": 2,
                "exterior_medium": analytic_exterior or "void" if analytic_box else "dagmc-defined",
                "dose_tally": dose_tally,
                "calculation_mode": calculation_mode,
                "dose_normalization": {
                    "flux": "historical-source-spectrum-flux-coefficient",
                    "heating": "direct-material-heating-per-mixture-mass",
                    "alanine": "independent-energy-resolved-alanine-response",
                }[dose_tally],
                "reported_dose_medium": (
                    "alanine"
                    if dose_tally == "alanine"
                    else (
                        "load-material-mixture"
                        if dose_tally == "heating"
                        else quick_reference_medium
                    )
                ),
                "independent_estimator": True,
                "uses_cross_mode_calibration": False,
                "charged_particle_equilibrium_assumed": dose_tally in {"flux", "alanine"},
                "intensive_estimator": (
                    intensive_estimator if dose_tally == "heating" else None
                ),
                "photon_yield_per_decay": photon_yield,
                "particles": simulated_particles,
                "requested_max_particles": particles,
                "simulated_particles": simulated_particles,
                "reused_histories": reused_histories,
                "restart_from": str(restart["statepoint"].relative_to(case_root)) if restart else None,
                "actual_batches": actual_batches,
                "requested_batches": requested_batches,
                "cell_size_cm": cell_size,
                "cpu_cores": cpu_cores,
                "cpu_threads_used": cpu_threads_used,
                "transport_wall_seconds": transport_wall_seconds,
                "pilot_particles": pilot_particles,
                "pilot_transport_wall_seconds": pilot_wall_seconds,
                "main_transport_wall_seconds": main_transport_wall_seconds,
                "preparation_wall_seconds": preparation_wall_seconds,
                "postprocess_wall_seconds": postprocess_wall_seconds,
                "total_wall_seconds": time.perf_counter() - workflow_started,
                "adaptive_converged": adaptive_converged,
                "mesh_spacing_cm": list(mesh_spec.spacing),
                "evaluation_mesh_spacing_cm": list(evaluation_mesh_spec.spacing),
                "statistics_basis": "selected-resolution-fine-mesh-tally",
                "transport_time_limited": transport_limited,
                "geometry_translation_cm": geometry_translation_cm,
                "work_section": deterministic.get("work_section", "A"),
                "variance_reduction": variance_reduction,
                "geometry_step": str(step_path.relative_to(case_root)),
                "statepoint": str(statepoints[-1].relative_to(case_root)),
                "calibration_medium": (
                    quick_reference_medium if dose_tally == "flux" else None
                ),
                "material_aliases": aliases,
            }
        )
        result["irradiator_type"] = request.get("irradiator_type", "IV")
        if checkpointed:
            from sim_gamma.workers.checkpoint_storage import compress_checkpoint
            result["checkpoint_compressed_bytes_saved"] = compress_checkpoint(statepoints[-1])
        write_json(output_root / "result.json", result)
        if checkpointed:
            from sim_gamma.workers.transport_budget import prune_completed_checkpoints
            prune_completed_checkpoints(work_root, statepoints[-1])
        write_status(
            status_path,
            request,
            "completed",
            100,
            state="completed",
            particles=simulated_particles,
            requested_max_particles=particles,
            simulated_particles=simulated_particles,
            actual_batches=actual_batches,
            dose_tally=dose_tally,
            cpu_cores=cpu_cores,
            cpu_threads_used=cpu_threads_used,
            transport_wall_seconds=transport_wall_seconds,
            cell_size_cm=cell_size,
            mesh_cells=mesh_spec.cell_count,
            active_load_cells=int(mask.sum()),
            variance_reduction=variance_reduction,
            result_file="result.json",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        os.chdir(previous_directory)
    worker_path.unlink(missing_ok=True)


def run_position(request_path: Path, *, route_context) -> None:
    """Release native CAD/ray allocations by ending each position process."""
    import subprocess
    from sim_gamma.domain.study_recovery import position_output_name

    index, count, position, activity_date, route = route_context
    request = json.loads(request_path.read_text())
    output = Path(request['case_root']) / 'monte_carlo' / 'positions' / request['run_id'] / position_output_name(index, route)
    output.mkdir(parents=True, exist_ok=True)
    context = output / 'position-context.json'
    write_json(context, {'index': index, 'count': count, 'position': position,
                         'activity_date': activity_date.isoformat(), 'route': route})
    error_file = output / 'position-error.json'
    error_file.unlink(missing_ok=True)
    # Inherit the guarded process group: pause/stop and RAM/time monitoring
    # continue to cover the entire position including its OpenMC children.
    child = subprocess.run([sys.executable, '-m', 'sim_gamma.workers.route_position_cli',
                            str(request_path), str(context)])
    if child.returncode:
        detail = json.loads(error_file.read_text()) if error_file.is_file() else {}
        error_type = TimeoutError if detail.get('timeout') else RuntimeError
        raise error_type(detail.get('error', f'La posición terminó con código {child.returncode}'))


def run_route(request_path: Path, request: dict, route_path: Path) -> None:
    started = time.perf_counter()
    from sim_gamma.domain.route_study import precision_status, selected_position_indices, study_precision
    from sim_gamma.domain.study_recovery import pause_requested, position_output_name, recover_study

    route = json.loads(route_path.read_text(encoding="utf-8"))
    study = request.get("route_study")
    if study:
        route.update({key: study[key] for key in (
            "particles", "batches", "cell_size_cm", "relative_error_target", "variance_reduction",
            "source_mixture_fraction", "adaptive_statistics", "occupancy", "cpu_cores",
        ) if study.get(key) is not None})
    positions = [
        {**position, "yaw_degrees": position["yaw_degrees"] + route.get("cad_offset_degrees", 0)}
        for position in route["positions"]
    ]
    route["positions"] = positions
    start, end = (study["start"], study["end"]) if study else route.get("score_interval", (0, len(positions) - 1))
    selection = study or {"start": start, "end": end, "position_names": route.get("calculation_positions")}
    indices = selected_position_indices(positions, selection)
    start, end = indices[0], indices[-1]
    route["study_position_indices"] = indices
    quick = bool(study and study.get("quick"))
    if quick:
        configured = route.get("cpu_cores", -1)
        route["quick_parameters"] = {
            "monte_carlo": {"particles": min(route["particles"], 60_000), "batches": min(route["batches"], 12)},
            "compute": {"cpu_cores": 4 if configured == -1 else min(configured, 4)},
        }
    positions = [positions[index] for index in indices]
    root = Path(request["case_root"])
    run_root = root / "monte_carlo"
    route["continuation"] = request.get("continuation", 0)
    activity_date = date.fromisoformat(study["irradiation_date"]) if study else date.today()
    saved = recover_study(root, request) if request.get("resume") else {}
    rates = list(saved.get("positions", []))
    previous_wall = saved.get("wall_seconds", 0)
    results_root = root / "results"
    archive = results_root / "studies"
    archive.mkdir(parents=True, exist_ok=True)
    latest = results_root / "route_rates.json"
    if latest.is_file():
        previous = json.loads(latest.read_text(encoding="utf-8"))
        previous_path = archive / f"{previous['run_id']}.json"
        if not previous_path.is_file():
            write_json(previous_path, previous)
    from sim_gamma.simulators.openmc.geometry_contract import TRANSPORT_GEOMETRY_REVISION
    result = {
        "transport_geometry_revision": TRANSPORT_GEOMETRY_REVISION if route.get("dose_tally", "heating") != "phits" else None,
        "exterior_medium": "air" if route.get("dose_tally", "heating") != "phits" else None,
        "case_id": request["case_id"], "run_id": request["run_id"],
        **({"analysis_method": route["analysis_method"]} if route.get("analysis_method") else {}),
        "irradiator_type": request["irradiator_type"], "activity_date": activity_date.isoformat(),
        "occupancy": route.get("occupancy", "isolated"),
        "dose_tally": route.get("dose_tally", "heating"),
        "score_interval": [start, end], "route_position_count": len(route["positions"]),
        **({"selected_position_names": [point["name"] for point in positions]} if selection.get("position_names") is not None else {}),
        "cell_size_cm": route.get("cell_size_cm"), "quick": quick,
        "adaptive_statistics": bool(route.get("adaptive_statistics", False)),
        "extended_time": bool(study and study.get("extended_time")),
        "cpu_cores": route.get("cpu_cores", -1),
        "requested_particles_per_position": min(route["particles"], 60_000) if quick else route.get("particles"),
        "variance_reduction": route.get("variance_reduction", "analog"),
        "source_mixture_fraction": route.get("source_mixture_fraction", .9) if route.get("variance_reduction") in {"source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene"} else None,
        "relative_error_target": route.get("relative_error_target", 0.05),
        "precision": study_precision(rates, False),
        "wall_seconds": time.perf_counter() - started,
        "unit": "kGy/h", "positions": rates,
        "requested_position_count": len(positions), "complete": False,
    }
    if saved:
        result = {**saved, "positions": rates, "continuation": request.get("continuation", 0)}
        result.pop("stop_reason", None)
    from sim_gamma.domain.interval_budget import (
        INITIAL_INTERVAL_HISTORIES, INTERVAL_BATCHES, MAX_INTERVAL_PASSES, evaluate_interval, plan_interval_histories, reference_budgets,
    )
    accumulated_target = study.get("accumulated_error_target") if study else None
    route["accumulated_error_target"] = accumulated_target
    maximum_particles = min(route["particles"], 60_000) if quick else route["particles"]
    budgets = [min(maximum_particles, INITIAL_INTERVAL_HISTORIES) if accumulated_target else maximum_particles] * len(positions)
    guided_plan = reference_budgets(root, study, positions, accumulated_target, maximum_particles) if accumulated_target else None
    if guided_plan:
        budgets = guided_plan
    result["budget_reference_run_id"] = study.get("budget_reference_run_id") if guided_plan else None
    result["accumulated_error_target"] = accumulated_target
    result.setdefault("completed_calculations", len(rates))
    result.setdefault("total_simulated_histories", sum(rate["simulated_particles"] for rate in rates))
    calculations = {index: int(rate["simulated_particles"]) for index, rate in enumerate(rates)}
    if route.get("adaptive_statistics") and not accumulated_target:
        for index, rate in enumerate(rates):
            summary = json.loads((root / rate["summary"]).read_text())
            if summary.get("adaptive_converged"):
                calculations[index] = budgets[index]
    paused = False
    for refinement in range(MAX_INTERVAL_PASSES if accumulated_target else 1):
        route["refinement_pass"] = refinement
        order = range(len(positions)) if not refinement else sorted(
            range(len(positions)), key=lambda index: budgets[index] - calculations[index], reverse=True,
        )
        for index in order:
            position = positions[index]
            if index in calculations and calculations[index] >= budgets[index]:
                continue
            if pause_requested(run_root, request["run_id"]):
                paused = True
                break
            if accumulated_target:
                route.update(particles=budgets[index], batches=min(INTERVAL_BATCHES, budgets[index]), adaptive_statistics=False)
            route["restart_summary"] = rates[index]["summary"] if accumulated_target and index in calculations else None
            run_position(request_path, route_context=(index, len(positions), position, activity_date, route))
            output_root = run_root / "positions" / request["run_id"] / position_output_name(index, route)
            summary = json.loads((output_root / "result.json").read_text(encoding="utf-8"))
            scored = summary.get("visualization") or summary
            mean = scored.get("average_dose")
            if mean is None or not math.isfinite(mean) or mean < 0:
                raise ValueError(f"No se obtuvo una tasa válida para {position['name']}")
            rate = {
                **position, "mean_kgy_h": mean, "minimum_kgy_h": scored["minimum_dose"],
                "maximum_kgy_h": scored["maximum_dose"], "relative_error_p90": scored.get("relative_error_p90"),
                "simulated_particles": summary.get("simulated_particles") or summary.get("particles"),
                "relative_error_maximum": scored.get("relative_error_maximum"),
                "precision": precision_status(scored),
                "materials": scored.get("materials", []),
                "material_statistics": (summary.get("fine_mesh_convergence") or {}).get("materials", []),
                "summary": str((output_root / "result.json").relative_to(root)),
            }
            if index < len(rates):
                rates[index] = rate
            else:
                rates.append(rate)
            result.pop("interval_precision", None)
            calculations[index] = int(rate["simulated_particles"])
            result["completed_calculations"] += 1
            result["total_simulated_histories"] += calculations[index] - int(summary.get("reused_histories", 0))
            result.update({
                "wall_seconds": previous_wall + time.perf_counter() - started,
                "complete": len(rates) == len(positions) and not accumulated_target,
                "precision": study_precision(rates, len(rates) == len(positions)),
            })
            if accumulated_target and refinement:
                # A conservative plan need not be spent in full. Stop as soon
                # as the actual combined map meets the requested maximum.
                combined_path = run_root / "intervals" / request["run_id"] / "summed_rates.vti"
                result["interval_precision"] = evaluate_interval(
                    root, rates, combined_path, accumulated_target, refinement + 1,
                )
            write_json(archive / f"{request['run_id']}.json", result)
            write_json(latest, result)
            if summary.get("transport_time_limited"):
                result["stop_reason"] = "time-limited"
                break
            if result.get("interval_precision", {}).get("meets_target"):
                break
        if paused or result.get("stop_reason") or not accumulated_target:
            break
        if result.get("interval_precision", {}).get("meets_target"):
            break
        combined_path = run_root / "intervals" / request["run_id"] / "summed_rates.vti"
        result["interval_precision"] = evaluate_interval(root, rates, combined_path, accumulated_target, refinement + 1)
        if result["interval_precision"]["meets_target"]:
            break
        planned = plan_interval_histories(root, rates, accumulated_target, maximum_particles)
        if planned == budgets or refinement == MAX_INTERVAL_PASSES - 1:
            result["stop_reason"] = "precision-limited"
            break
        # Bound a refinement so one huge extrapolation cannot consume the
        # entire deadline before the other positions are improved and checked.
        budgets = [min(desired, 8 * calculations[index]) for index, desired in enumerate(planned)]
    if accumulated_target and not paused and len(rates) == len(positions) and "interval_precision" not in result:
        combined_path = run_root / "intervals" / request["run_id"] / "summed_rates.vti"
        result["interval_precision"] = evaluate_interval(root, rates, combined_path, accumulated_target, refinement + 1)
    if paused:
        result["stop_reason"] = "paused"
    result.update({"wall_seconds": previous_wall + time.perf_counter() - started,
                   "complete": len(rates) == len(positions),
                   "precision": study_precision(rates, len(rates) == len(positions))})
    if rates:
        write_json(archive / f"{request['run_id']}.json", result)
        write_json(latest, result)
    if route.get("analysis_method") == "monte_carlo_air" and result["complete"] and not result.get("stop_reason"):
        from sim_gamma.domain.route_map import write_route_dose_map

        scenarios = results_root / "dose_scenarios"
        scenario_path = scenarios / f"{request['run_id']}.json"
        combined = write_route_dose_map(root, rates, [60] * len(rates), scenario_path.with_suffix(".vti"),
                                       sum_rates=True, accumulated_error_target=route["accumulated_error_target"])
        write_json(scenario_path, {
            "run_id": request["run_id"], "quantity": "rate_sum", "positions": rates, "dose_map": combined,
            "summed_rate_kgy_h": sum(rate["mean_kgy_h"] for rate in rates),
            "request": {"start": 0, "end": len(rates) - 1, "mode": "rate_sum"},
        })
    state = "paused" if paused else "time-limited" if result.get("stop_reason") == "time-limited" else "completed"
    guarded = os.environ.get("IRRADIUM_STUDY_CHILD") == "1"
    write_status(run_root / "status.json", request, "finalizing-study" if guarded else state,
                 min(99, len(rates) * 100 // len(positions)) if paused else 100,
                 state="running" if guarded else state, final_state=state, position_count=len(rates))
    (run_root / "worker.json").unlink(missing_ok=True)


def main() -> int:
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text())
    if (Path(request["case_root"]) / "input" / "route.json").is_file() and os.environ.get("IRRADIUM_STUDY_CHILD") != "1":
        from sim_gamma.workers.study_guard import run_guarded

        return run_guarded(request_path)
    try:
        run(request_path)
        return 0
    except Exception as error:
        with request_path.open("r", encoding="utf-8") as stream:
            request = json.load(stream)
        output_root = Path(request["case_root"]) / "monte_carlo"
        timed_out = isinstance(error, TimeoutError)
        final_state = "time-limited" if timed_out else "failed"
        guarded = os.environ.get("IRRADIUM_STUDY_CHILD") == "1"
        write_json(
            output_root / "status.json",
            {
                "case_id": request["case_id"],
                "run_id": request.get("run_id"),
                "state": "running" if guarded else final_state,
                "stage": "finalizing-study" if guarded else final_state,
                "final_state": final_state,
                "progress": 0,
                "error": str(error),
                "execution_enabled": True,
            },
        )
        (output_root / "worker.json").unlink(missing_ok=True)
        traceback.print_exc()
        return 0 if timed_out else 1


if __name__ == "__main__":
    raise SystemExit(main())
