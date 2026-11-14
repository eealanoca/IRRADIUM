import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def write_json(path: Path, data: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
    temporary.replace(path)


def as_builtin(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, tuple):
        return [as_builtin(item) for item in value]
    if isinstance(value, list):
        return [as_builtin(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def run(request_path: Path) -> None:
    with request_path.open("r", encoding="utf-8") as stream:
        request = json.load(stream)

    case_root = Path(request["case_root"])
    project_root = Path(request["project_root"])
    input_root = case_root / "input"
    output_root = case_root / "deterministic"
    status_path = output_root / "status.json"
    worker_path = output_root / "worker.json"
    parameters = request["parameters"]
    run_id = request["run_id"]
    simulation_path = input_root / "simulation_parameters.json"
    if simulation_path.is_file():
        with simulation_path.open("r", encoding="utf-8") as stream:
            simulation_parameters = json.load(stream)
    else:
        simulation_parameters = {}
    movement_parameters = simulation_parameters.get("movement", {})
    deterministic_parameters = simulation_parameters.get("deterministic", {})
    compute_parameters = simulation_parameters.get("compute", {})
    monte_carlo_parameters = simulation_parameters.get("monte_carlo", {})
    radionuclide = simulation_parameters.get("radionuclide", "Co-60")

    write_json(
        status_path,
        {
            "case_id": request["case_id"],
            "run_id": run_id,
            "state": "running",
            "stage": "sampling-and-optimization",
            "progress": 10,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "monte_carlo_started": False,
        },
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
        },
    )

    cores = int(
        compute_parameters.get(
            "cpu_cores",
            monte_carlo_parameters.get("cpu_cores", parameters.get("cores", -1)),
        )
    )
    optimizer_jobs = cores if cores > 0 else -1
    if cores > 0:
        os.environ["LOKY_MAX_CPU_COUNT"] = str(cores)
    matplotlib_cache = output_root / ".matplotlib"
    matplotlib_cache.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))

    from sim_gamma.physics.parameters import load_physical_parameters
    from sim_gamma.physics.deterministic.optimizer import (
        generate_optimized_flux_ratio_from_stl,
        generate_step_angles,
        process_sources_and_calculate_flux_accumulated_real_world,
    )
    from sim_gamma.results import write_dose_artifacts

    world_load_stl = input_root / "world_load.stl"
    if not world_load_stl.is_file():
        source_stl = project_root / "legacy" / "assets" / "world_load.stl"
        if not source_stl.is_file():
            raise FileNotFoundError("No se encontró world_load.stl para el método determinista")
        shutil.copy2(source_stl, world_load_stl)

    decay_factor, dose_factor = load_physical_parameters(
        radionuclide=radionuclide,
        material_name="air",
        source_file=input_root / "sources.json",
        materials_file=input_root / "materials.json",
    )
    step_angles_machine = generate_step_angles(
        int(movement_parameters.get("machine_angle_start_degrees", 0)),
        int(movement_parameters.get("machine_angle_stop_degrees", 360)),
        int(movement_parameters.get("machine_angle_step_degrees", 10)),
    )
    if movement_parameters:
        resolution = int(movement_parameters["resolution_length_mm"])
        lower = movement_parameters["length_complete_lower_range_mm"]
        upper = movement_parameters["length_complete_upper_range_mm"]
        longitudinal_positions = sorted(
            set(range(int(lower[0]), int(lower[1]) + 1, resolution)).union(
                range(int(upper[0]), int(upper[1]) + 1, resolution)
            )
        )
    else:
        longitudinal_positions = sorted(
            set(int(value) for value in parameters.get(
                "longitudinal_positions_mm",
                [-800, -300, 200, 700],
            ))
        )
    if len(longitudinal_positions) < 2:
        raise ValueError("Se requieren al menos dos posiciones longitudinales")
    result = generate_optimized_flux_ratio_from_stl(
        str(world_load_stl),
        step_angles_machine,
        work_section=parameters.get("work_section", "A"),
        num_points=int(deterministic_parameters.get("num_points", parameters.get("num_points", 10000))),
        angle_degrees=float(movement_parameters.get("cad_rotation_degrees", parameters.get("angle_degrees", 0))),
        num_steps_angle_range=tuple(movement_parameters.get("angular_positions_range", (1, 13))),
        length_complete_lower_range=tuple(
            movement_parameters.get("length_complete_lower_range_mm", (-800, 200))
        ),
        length_complete_upper_range=tuple(
            movement_parameters.get("length_complete_upper_range_mm", (200, 700))
        ),
        resolution_length=int(movement_parameters.get("resolution_length_mm", 500)),
        decay_factor=decay_factor,
        work_sections_file=str(input_root / "work_sections.json"),
        sources_file=str(input_root / "sources.json"),
        length_complete_values=longitudinal_positions,
        n_jobs=optimizer_jobs,
    )
    (
        best_flux_ratio,
        machine_angles,
        ideal_angles,
        real_angles,
        geometric_center,
        points_inside,
        vertices,
        best_params,
    ) = result

    running_status = {
        "case_id": request["case_id"],
        "run_id": run_id,
        "state": "running",
        "stage": "theoretical-dose-distribution",
        "progress": 72,
        "monte_carlo_started": False,
    }
    write_json(status_path, running_status)

    best_levels = list(best_params[3])
    moved_points, accumulated_fluxes, real_flux_ratio = (
        process_sources_and_calculate_flux_accumulated_real_world(
            geometric_center,
            points_inside,
            vertices,
            step_angle_degrees_range=real_angles,
            dose_coeff=dose_factor,
            step_length_range=best_levels,
            filename=str(input_root / "sources.json"),
            decay_factor=decay_factor,
        )
    )
    dose_artifacts = write_dose_artifacts(
        moved_points / 10,
        accumulated_fluxes,
        output_root,
        title="Método determinista",
    )

    length_points = longitudinal_positions
    document = {
        "run_id": run_id,
        "best_flux_ratio": as_builtin(best_flux_ratio),
        "real_flux_ratio": as_builtin(real_flux_ratio),
        "step_angles_machine": as_builtin(machine_angles),
        "step_angle_ideal": as_builtin(ideal_angles),
        "step_angle_degrees_best_real": as_builtin(real_angles),
        "geometric_center_cm": as_builtin(geometric_center),
        "work_section": parameters.get("work_section", "A"),
        "best_parameters": as_builtin(best_params),
        "length_points_mm": length_points,
        "best_length_points_mm": as_builtin(best_levels),
        "average_dose": float(accumulated_fluxes.mean()),
        "load_geometry_stl": str(world_load_stl.relative_to(case_root)),
        **dose_artifacts,
        "note": "Resultado determinista pendiente de la etapa Monte Carlo.",
    }
    document["irradiator_type"] = request.get("irradiator_type", "IV")
    result_path = output_root / "result.json"
    write_json(result_path, document)
    write_json(
        status_path,
        {
            "case_id": request["case_id"],
            "run_id": run_id,
            "state": "completed",
            "stage": "review-trajectory",
            "progress": 100,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_file": result_path.name,
            "monte_carlo_started": False,
        },
    )
    worker_path.unlink(missing_ok=True)


def main() -> int:
    request_path = Path(sys.argv[1])
    try:
        run(request_path)
        return 0
    except Exception as error:
        with request_path.open("r", encoding="utf-8") as stream:
            request = json.load(stream)
        status_path = Path(request["case_root"]) / "deterministic" / "status.json"
        write_json(
            status_path,
            {
                "case_id": request["case_id"],
                "run_id": request.get("run_id"),
                "state": "failed",
                "stage": "deterministic-error",
                "progress": 0,
                "error": str(error),
                "monte_carlo_started": False,
            },
        )
        (Path(request["case_root"]) / "deterministic" / "worker.json").unlink(
            missing_ok=True
        )
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
