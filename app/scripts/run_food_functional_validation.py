"""Ejecuta de extremo a extremo los dos casos alimentarios preparados."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sim_gamma.application.services.movement_export import (
    build_from_deterministic_case,
    write_movement_document,
)


def write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_worker(project_root: Path, module: str, request_path: Path, log_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(project_root / "src"), str(project_root), environment.get("PYTHONPATH", ""))
    ).rstrip(os.pathsep)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as stream:
        completed = subprocess.run(
            [sys.executable, "-m", module, str(request_path)],
            cwd=str(request_path.parent),
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode:
        excerpt = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
        raise RuntimeError(f"Falló {module}; revisar {log_path}:\n{excerpt}")


def stage_completed(case: dict, stage: str) -> bool:
    root = Path(case["case_root"]) / stage
    status_path = root / "status.json"
    result_path = root / "result.json"
    if not status_path.is_file() or not result_path.is_file():
        return False
    status = json.loads(status_path.read_text(encoding="utf-8"))
    return status.get("state") == "completed"


def sync_example_inputs(example_root: Path, case: dict) -> None:
    """Actualiza el caso preparado con la última geometría reproducible."""

    case_root = Path(case["case_root"])
    input_root = case_root / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    step_source = example_root / "load.step"
    stl_source = example_root / "world_load.stl"
    step_target = input_root / "load.step"
    stl_target = input_root / "world_load.stl"
    shutil.copy2(step_source, step_target)
    shutil.copy2(stl_source, stl_target)

    manifest_path = case_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["step_input"]["size_bytes"] = step_target.stat().st_size
    manifest["stl_input"]["size_bytes"] = stl_target.stat().st_size
    manifest["state"] = "not-started"
    manifest["current_step"] = "deterministic"
    manifest["results_saved"] = False
    write_json(manifest_path, manifest)


def deterministic_request(project_root: Path, case: dict) -> Path:
    case_root = Path(case["case_root"])
    manifest = json.loads((case_root / "manifest.json").read_text(encoding="utf-8"))
    parameters = dict(manifest["deterministic_parameters"])
    parameters["angle_degrees"] = 0.0
    request = {
        "case_id": case["case_id"],
        "case_root": str(case_root),
        "project_root": str(project_root),
        "run_id": "food-deterministic-" + uuid4().hex,
        "parameters": parameters,
    }
    path = case_root / "deterministic" / "request.json"
    write_json(path, request)
    return path


def share_deterministic_result(source_case: dict, destination_case: dict) -> None:
    source = Path(source_case["case_root"]) / "deterministic"
    destination = Path(destination_case["case_root"]) / "deterministic"
    for path in source.iterdir():
        if path.name in {"request.json", "worker.json", "submit.lock"}:
            continue
        target = destination / path.name
        if path.is_dir():
            shutil.copytree(path, target, dirs_exist_ok=True)
        else:
            shutil.copy2(path, target)
    source_request = json.loads((source / "request.json").read_text(encoding="utf-8"))
    source_request["case_id"] = destination_case["case_id"]
    source_request["case_root"] = destination_case["case_root"]
    source_request["run_id"] = "shared-identical-deterministic-input"
    write_json(destination / "request.json", source_request)
    status_path = destination / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["case_id"] = destination_case["case_id"]
    status["stage"] = "review-trajectory"
    status["shared_identical_deterministic_input"] = True
    write_json(status_path, status)


def monte_carlo_request(project_root: Path, case: dict) -> Path:
    case_root = Path(case["case_root"])
    request = {
        "case_id": case["case_id"],
        "case_root": str(case_root),
        "project_root": str(project_root),
        "run_id": "food-" + case["method"] + "-" + uuid4().hex,
    }
    path = case_root / "monte_carlo" / "request.json"
    write_json(path, request)
    return path


def publish_results(project_root: Path, case: dict) -> None:
    case_root = Path(case["case_root"])
    movement = build_from_deterministic_case(case_root)
    write_movement_document(
        case_root / "results" / "movements_parameters_optimized.json",
        movement,
    )
    write_movement_document(
        project_root / "output" / "movements_parameters_optimized.json",
        movement,
    )
    manifest_path = case_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["state"] = "completed"
    manifest["current_step"] = "results"
    manifest["results_saved"] = True
    write_json(manifest_path, manifest)


def result_summary(case: dict) -> dict:
    case_root = Path(case["case_root"])
    deterministic = json.loads((case_root / "deterministic" / "result.json").read_text(encoding="utf-8"))
    monte_carlo = json.loads((case_root / "monte_carlo" / "result.json").read_text(encoding="utf-8"))
    return {
        "case_id": case["case_id"],
        "method": case["method"],
        "particles": monte_carlo.get("simulated_particles", monte_carlo.get("particles")),
        "cell_size_cm": monte_carlo.get("cell_size_cm"),
        "active_cells": monte_carlo.get("active_cells"),
        "relative_error_median": monte_carlo.get("relative_error_median"),
        "relative_error_p90": monte_carlo.get("relative_error_p90"),
        "relative_error_maximum": monte_carlo.get("relative_error_maximum"),
        "minimum_dose": monte_carlo.get("minimum_dose"),
        "maximum_dose": monte_carlo.get("maximum_dose"),
        "dur_raw": monte_carlo.get("dose_uniformity_ratio"),
        "dur_p90_p10": monte_carlo.get("operational_dose_uniformity_ratio"),
        "dur_p95_p05": monte_carlo.get("robust_dose_uniformity_ratio"),
        "dur_p98_p02": monte_carlo.get("d98_d2_uniformity_ratio"),
        "preparation_wall_seconds": monte_carlo.get("preparation_wall_seconds"),
        "transport_wall_seconds": monte_carlo.get("transport_wall_seconds"),
        "postprocess_wall_seconds": monte_carlo.get("postprocess_wall_seconds"),
        "total_wall_seconds": monte_carlo.get("total_wall_seconds"),
        "trajectory_angles": deterministic.get("step_angle_degrees_best_real"),
        "trajectory_positions_mm": deterministic.get("best_length_points_mm"),
        "movement_file": str(case_root / "results" / "movements_parameters_optimized.json"),
    }


def run(project_root: Path) -> dict:
    example_root = project_root / "examples" / "irradiation_loads" / "01_food_pallet"
    prepared = json.loads((example_root / "functional_cases.json").read_text(encoding="utf-8"))
    by_method = {case["method"]: case for case in prepared["cases"]}
    heating = by_method["heating"]
    phits = by_method["phits"]

    for case in (heating, phits):
        sync_example_inputs(example_root, case)

    if not stage_completed(heating, "deterministic"):
        deterministic_path = deterministic_request(project_root, heating)
        run_worker(
            project_root,
            "sim_gamma.workers.deterministic_cli",
            deterministic_path,
            Path(heating["case_root"]) / "logs" / "deterministic.log",
        )
    share_deterministic_result(heating, phits)

    for case in (heating, phits):
        if not stage_completed(case, "monte_carlo"):
            request_path = monte_carlo_request(project_root, case)
            run_worker(
                project_root,
                "sim_gamma.workers.monte_carlo_cli",
                request_path,
                Path(case["case_root"]) / "logs" / "monte_carlo.log",
            )
        publish_results(project_root, case)

    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "food-load-openmc-intensive-vs-phits-functional-validation",
        "results": [result_summary(heating), result_summary(phits)],
    }
    write_json(example_root / "functional_test_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(run(args.project_root.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
