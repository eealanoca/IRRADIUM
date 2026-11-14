"""Registra dos casos de demostración para la carga alimentaria de ejemplo.

El script solamente prepara configuraciones y casos. Las etapas determinista y
Monte Carlo se ejecutan después mediante los mismos workers de la aplicación.
"""

from __future__ import annotations

import argparse
import json
import shutil
from copy import deepcopy
from pathlib import Path

from sim_gamma.application.services.cad_preview import CadPreviewService
from sim_gamma.application.services.case_store import CaseStore


PROFILE_IDS = {
    "heating": "ejemplo-alimentos-intensivo-10m",
    "phits": "ejemplo-alimentos-phits-10m",
}


def write_json(path: Path, document) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parameters(method: str) -> dict:
    return {
        "radionuclide": "Co-60",
        "movement": {
            "default_work_section": "A",
            "machine_angle_start_degrees": 0,
            "machine_angle_stop_degrees": 360,
            "machine_angle_step_degrees": 10,
            "angular_positions_range": [1, 13],
            "cad_rotation_degrees": 0.0,
            "length_complete_lower_range_mm": [-800, 200],
            "length_complete_upper_range_mm": [200, 700],
            "resolution_length_mm": 500,
        },
        "deterministic": {"num_points": 5000},
        "compute": {"cpu_cores": -1},
        "monte_carlo": {
            # Esta es una prueba funcional de 10 millones, no el perfil de
            # producción. La malla de 5 cm conserva un número útil de historias
            # por celda en una carga de aproximadamente 1.2 x 1.5 x 1.1 m.
            "cell_size_cm": 5.0,
            "particles": 10_000_000,
            "batches": 100 if method == "heating" else 20,
            "relative_error_target": 0.03,
            "dose_tally": method,
        },
    }


def ensure_profiles(config_root: Path) -> None:
    path = config_root / "simulation_profiles.json"
    document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"profiles": []}
    profiles = document.setdefault("profiles", [])
    by_id = {item["profile_id"]: item for item in profiles}
    for method, profile_id in PROFILE_IDS.items():
        profile = {
            "profile_id": profile_id,
            "name": (
                "Ejemplo alimentos · cálculo intensivo · 10 millones"
                if method == "heating"
                else "Ejemplo alimentos · PHITS · 10 millones"
            ),
            "parameters": parameters(method),
        }
        if profile_id in by_id:
            by_id[profile_id].clear()
            by_id[profile_id].update(profile)
        else:
            profiles.append(profile)
    write_json(path, document)


def create_case(
    store: CaseStore,
    preview: CadPreviewService,
    example_root: Path,
    method: str,
) -> dict:
    name = (
        "Demostración alimentos · OpenMC intensivo · 10M"
        if method == "heating"
        else "Demostración alimentos · PHITS · 10M"
    )
    description = (
        "Prueba funcional de un pallet heterogéneo de alimentos frescos. "
        "Presupuesto total: 10 000 000 de historias; no constituye una corrida dosimétrica de producción."
    )
    manifest = store.create(name, description, PROFILE_IDS[method])
    case_id = manifest["case_id"]
    case_root = store.root(case_id)
    step_source = example_root / "load.step"
    stl_source = example_root / "world_load.stl"
    step_destination = case_root / "input" / "load.step"
    stl_destination = case_root / "input" / "world_load.stl"
    shutil.copy2(step_source, step_destination)
    shutil.copy2(stl_source, stl_destination)
    materials = json.loads((example_root / "materials.json").read_text(encoding="utf-8"))["materials"]
    component_manifest = preview.derive_component_meshes(step_source)
    tags = sorted({part["material"] for part in component_manifest["parts"]})
    manifest = store.set_step_input(
        case_id,
        step_source.name,
        step_destination.stat().st_size,
        deepcopy(materials),
        tags,
    )
    manifest = store.set_stl_input(case_id, stl_source.name, stl_destination.stat().st_size)
    manifest["example_source"] = str(example_root)
    manifest["functional_test"] = {
        "particles": 10_000_000,
        "method": method,
        "scope": "end-to-end functional validation",
        "production_dosimetry": False,
    }
    write_json(case_root / "manifest.json", manifest)
    return {"case_id": case_id, "case_root": str(case_root), "method": method}


def prepare(project_root: Path) -> dict:
    config_root = project_root / "data" / "config"
    output_root = project_root / "output"
    example_root = project_root / "examples" / "irradiation_loads" / "01_food_pallet"
    ensure_profiles(config_root)
    store = CaseStore(
        output_root,
        config_root,
        project_root / "data" / "examples" / "load.step",
        project_root / "legacy" / "assets" / "world_load.stl",
    )
    preview = CadPreviewService(output_root / "cache" / "step_previews")
    cases = [create_case(store, preview, example_root, method) for method in ("heating", "phits")]
    document = {
        "example": str(example_root),
        "profiles": PROFILE_IDS,
        "cases": cases,
    }
    write_json(example_root / "functional_cases.json", document)
    return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(prepare(args.project_root.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
