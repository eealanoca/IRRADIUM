#!/usr/bin/env python3
"""Prepare two reproducible water cases for default-parameter validation."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from uuid import uuid4


SOURCE_CASE = Path(
    "output/runs/20260906T044815Z-Prueba-5-calculo-intensivo-water-1c36153e"
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare(project_root: Path, output_root: Path) -> None:
    source = project_root / SOURCE_CASE
    if not source.is_dir():
        raise FileNotFoundError(source)

    presets = {
        "alanine": {
            "cell_size_cm": 2.0,
            "particles": 200_000_000,
            "batches": 100,
            "relative_error_target": 0.03,
            "dose_tally": "alanine",
            "adaptive_statistics": True,
            "directional_source_biasing": True,
        },
        "phits": {
            "cell_size_cm": 3.0,
            "particles": 200_000_000,
            "batches": 20,
            "relative_error_target": 0.03,
            "dose_tally": "phits",
            "photon_importance_load": 4.0,
        },
    }
    manifest = {
        "purpose": "water-defaults-relative-error-and-runtime-validation",
        "source_case": str(SOURCE_CASE),
        "acceptance": {
            "desired_wall_seconds": 1200,
            "maximum_wall_seconds": 3600,
            "relative_error_median_target": 0.03,
            "relative_error_p90_limit": 0.05,
        },
        "cases": {},
    }
    for method, monte_carlo in presets.items():
        destination = output_root / method
        input_root = destination / "input"
        deterministic_root = destination / "deterministic"
        mc_root = destination / "monte_carlo"
        input_root.mkdir(parents=True, exist_ok=True)
        deterministic_root.mkdir(parents=True, exist_ok=True)
        mc_root.mkdir(parents=True, exist_ok=True)
        for filename in (
            "load.step",
            "materials.json",
            "sources.json",
            "work_sections.json",
            "world_load.stl",
        ):
            shutil.copy2(source / "input" / filename, input_root / filename)
        shutil.copy2(
            source / "deterministic" / "result.json",
            deterministic_root / "result.json",
        )
        simulation = read_json(source / "input" / "simulation_parameters.json")
        simulation["compute"] = {"cpu_cores": -1}
        simulation["monte_carlo"] = monte_carlo
        write_json(input_root / "simulation_parameters.json", simulation)
        request = {
            "case_id": f"water-default-validation-{method}",
            "case_root": str(destination.resolve()),
            "project_root": str(project_root.resolve()),
            "run_id": f"water-default-{method}-{uuid4().hex}",
        }
        write_json(mc_root / "request.json", request)
        manifest["cases"][method] = {
            "request": str((mc_root / "request.json").resolve()),
            "parameters": monte_carlo,
        }
    write_json(output_root / "validation_manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    prepare(arguments.project_root.resolve(), arguments.output.resolve())


if __name__ == "__main__":
    main()
