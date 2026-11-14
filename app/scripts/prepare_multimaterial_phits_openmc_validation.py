"""Prepara una comparación funcional OpenMC/PHITS con un STEP heterogéneo."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import cadquery as cq


AIR = {
    "name": "air",
    "transport_medium": "air",
    "dosimeter_medium": "alanine",
    "coefficient_quantity": "mass_energy_absorption",
    "coefficient_units": "cm2/g",
    "coefficient_source": "NIST X-Ray Mass Attenuation Coefficients, Table 4: Air",
    "elements": {"C": 0.000124, "N": 0.755267, "O": 0.231781, "Ar": 0.012827},
    "density": 0.00120479,
    "fraction_type": "weight",
    "mu_en_over_rho": {"0.662": 0.0293, "1.173": 0.02669, "1.332": 0.02599},
}

LOAD_MATERIALS = [
    {
        "name": "water",
        "elements": {"H": 0.111894, "O": 0.888106},
        "density": 1.0,
        "fraction_type": "weight",
    },
    {
        "name": "polyethylene",
        "elements": {"H": 0.143711, "C": 0.856289},
        "density": 0.94,
        "fraction_type": "weight",
    },
    {
        "name": "steel",
        "elements": {"Fe": 0.70, "Cr": 0.19, "Ni": 0.10, "Mn": 0.01},
        "density": 7.85,
        "fraction_type": "weight",
    },
    {
        "name": "aluminum",
        "elements": {"Al": 1.0},
        "density": 2.70,
        "fraction_type": "weight",
    },
    {
        "name": "glass",
        "elements": {"O": 0.5396, "Si": 0.3772, "B": 0.04, "Na": 0.028, "Ca": 0.0152},
        "density": 2.23,
        "fraction_type": "weight",
    },
]


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_complex_step(destination: Path) -> None:
    """Crea una carga curva y multicomponente sin volúmenes solapados."""

    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    destination.parent.mkdir(parents=True, exist_ok=True)
    cylinder = (
        cq.Workplane("XY")
        .workplane(offset=-300)
        .circle(150)
        .extrude(600)
    )
    left_half = cylinder.intersect(
        cq.Workplane("XY").box(300, 300, 600).translate((-150, 0, 0))
    ).translate((0, -1600, 0))
    right_half = cylinder.intersect(
        cq.Workplane("XY").box(300, 300, 600).translate((150, 0, 0))
    ).translate((0, -1600, 0))
    steel_top = (
        cq.Workplane("XY").workplane(offset=300).circle(150).extrude(30)
        .translate((0, -1600, 0))
    )
    steel_bottom = (
        cq.Workplane("XY").workplane(offset=-330).circle(150).extrude(30)
        .translate((0, -1600, 0))
    )
    aluminum_left = cq.Workplane("XY").box(40, 160, 480).translate((-185, -1600, 0))
    aluminum_right = cq.Workplane("XY").box(40, 160, 480).translate((185, -1600, 0))
    glass_front = (
        cq.Workplane("XY").workplane(offset=-240).circle(50).extrude(480)
        .translate((0, -1380, 0))
    )
    glass_rear = (
        cq.Workplane("XY").workplane(offset=-240).circle(50).extrude(480)
        .translate((0, -1820, 0))
    )
    air_world = cq.Workplane("XY").box(16000, 14000, 4000)

    components = (
        ("air_001", air_world),
        ("water_left_core", left_half),
        ("polyethylene_right_core", right_half),
        ("steel_top_plate", steel_top),
        ("steel_bottom_plate", steel_bottom),
        ("aluminum_left_bar", aluminum_left),
        ("aluminum_right_bar", aluminum_right),
        ("glass_front_rod", glass_front),
        ("glass_rear_rod", glass_rear),
    )
    document = TDocStd_Document(TCollection_ExtendedString("SIM Gamma validation"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    for name, shape in components:
        label = shape_tool.AddShape(shape.val().wrapped, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.Transfer(document)
    if writer.Write(str(destination)) != IFSelect_RetDone:
        raise OSError("OpenCascade no pudo exportar el STEP multicomponente")


def deterministic_result(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "best_flux_ratio": 1.0,
        "real_flux_ratio": 1.0,
        "step_angles_machine": [0],
        "step_angle_ideal": [0.0],
        "step_angle_degrees_best_real": [0],
        "geometric_center_cm": [0.0, -160.0, 0.0],
        "work_section": "A",
        "best_parameters": [1, 0, 0, [0, 0]],
        "length_points_mm": [0],
        "best_length_points_mm": [0],
        "average_dose": 0.0,
        "note": "Entrada geométrica común para validación OpenMC/PHITS.",
    }


def simulation_parameters(method: str, particles: int, cell_size_cm: float) -> dict:
    return {
        "radionuclide": "Co-60",
        "movement": {
            "default_work_section": "A",
            "machine_angle_start_degrees": 0,
            "machine_angle_stop_degrees": 0,
            "machine_angle_step_degrees": 10,
            "angular_positions_range": [1, 1],
            "cad_rotation_degrees": 0.0,
            "length_complete_lower_range_mm": [0, 0],
            "length_complete_upper_range_mm": [0, 0],
            "resolution_length_mm": 1,
        },
        "deterministic": {"num_points": 1000},
        "compute": {"cpu_cores": -1},
        "monte_carlo": {
            "cell_size_cm": cell_size_cm,
            "particles": particles,
            "dose_tally": method,
            "adaptive_statistics": False,
            "relative_error_target": 0.05,
            "survival_biasing": False,
            "weight_window_method": "none",
            "directional_source_biasing": True,
            "electron_treatment": "ttb",
            "photon_importance_load": 4.0,
            "source_activity_reference_date": "2026-09-19",
            "random_seed": 19092026,
        },
    }


def prepare(project_root: Path, output_root: Path, particles: int, cell_size_cm: float) -> None:
    if output_root.exists():
        raise FileExistsError(f"El directorio de validación ya existe: {output_root}")
    output_root.mkdir(parents=True)
    common_step = output_root / "heterogeneous_load.step"
    create_complex_step(common_step)
    sources = json.loads((project_root / "data" / "config" / "sources.json").read_text(encoding="utf-8"))
    materials = {"materials": [AIR, *LOAD_MATERIALS]}
    cases = {}
    for method in ("heating", "phits"):
        case_root = output_root / method
        input_root = case_root / "input"
        deterministic_root = case_root / "deterministic"
        monte_carlo_root = case_root / "monte_carlo"
        logs_root = case_root / "logs"
        for path in (input_root, deterministic_root, monte_carlo_root / "work", logs_root):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(common_step, input_root / "load.step")
        write_json(input_root / "materials.json", materials)
        write_json(input_root / "sources.json", sources)
        write_json(
            input_root / "simulation_parameters.json",
            simulation_parameters(method, particles, cell_size_cm),
        )
        write_json(input_root / "work_sections.json", {"positions": {"A": [0, -1600, 0]}})
        deterministic_run_id = "validation-deterministic-" + method
        write_json(deterministic_root / "result.json", deterministic_result(deterministic_run_id))
        request = {
            "case_id": "multimaterial-validation-" + method,
            "case_root": str(case_root.resolve()),
            "project_root": str(project_root.resolve()),
            "run_id": "validation-" + method + "-" + uuid4().hex,
        }
        write_json(monte_carlo_root / "request.json", request)
        cases[method] = {
            "case_root": str(case_root.resolve()),
            "request": str((monte_carlo_root / "request.json").resolve()),
        }
    write_json(
        output_root / "validation_manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "complex-multimaterial-openmc-intensive-vs-phits",
            "step": {"file": common_step.name, "sha256": sha256(common_step)},
            "materials": [item["name"] for item in LOAD_MATERIALS],
            "components": 9,
            "particles_per_method": particles,
            "cell_size_cm": cell_size_cm,
            "source_count": len(sources["sources"]),
            "angles": [0],
            "longitudinal_positions_mm": [0],
            "cases": cases,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--particles", type=int, default=20_000_000)
    parser.add_argument("--cell-size-cm", type=float, default=3.0)
    arguments = parser.parse_args()
    prepare(
        arguments.project_root.resolve(),
        arguments.output.resolve(),
        arguments.particles,
        arguments.cell_size_cm,
    )


if __name__ == "__main__":
    main()
