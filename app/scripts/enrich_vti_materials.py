"""Añade material_id a un VTI existente sin regenerar los cortes XY/YZ."""

import argparse
import json
from pathlib import Path

import numpy as np

from sim_gamma.simulators.openmc.structured_postprocess import (
    RegularMeshSpec,
    build_material_ids,
    write_compressed_vti,
)
from sim_gamma.workers.monte_carlo_cli import (
    combined_material_surfaces,
    step_components,
)


def enrich(case_root: Path):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    case_root = case_root.resolve()
    output_root = case_root / "monte_carlo"
    vti_path = output_root / "dose_distribution.vti"
    step_path = case_root / "input" / "load.step"
    if not vti_path.is_file() or not step_path.is_file():
        raise FileNotFoundError("El caso debe contener load.step y dose_distribution.vti")

    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(str(vti_path))
    reader.Update()
    image = reader.GetOutput()
    dimensions = tuple(int(value - 1) for value in image.GetDimensions())
    origin = tuple(float(value) for value in image.GetOrigin())
    spacing = tuple(float(value) for value in image.GetSpacing())
    upper = tuple(
        low + step * cells
        for low, step, cells in zip(origin, spacing, dimensions)
    )
    mesh = RegularMeshSpec(dimensions, origin, upper)
    scalars = image.GetCellData().GetScalars()
    if scalars is None:
        raise ValueError("El VTI no contiene la tasa de dosis")
    dose = np.array(vtk_to_numpy(scalars), dtype=np.float32, copy=True)

    components, _ = step_components(
        step_path,
        output_root / "cache" / "step-components",
    )
    surfaces = combined_material_surfaces(
        components,
        output_root / "work" / "step_material_surfaces",
    )
    material_ids, labels = build_material_ids(
        surfaces,
        mesh,
        output_root / "cache" / "material-masks",
        length_scale_to_cm=0.1,
    )

    temporary = output_root / "dose_distribution.materials.tmp.vti"
    write_compressed_vti(
        dose,
        mesh,
        temporary,
        inside_mask=material_ids != 0,
        material_ids=material_ids,
        scalar_name=scalars.GetName() or "absorbed_dose_rate_kgy_h",
    )
    temporary.replace(vti_path)

    for filename in ("visualization.json", "result.json"):
        path = output_root / filename
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data["materials"] = labels
        if isinstance(data.get("visualization"), dict):
            data["visualization"]["materials"] = labels
        pending = path.with_suffix(path.suffix + ".tmp")
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(path)
    return labels, int(np.count_nonzero(material_ids))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case_root", type=Path)
    arguments = parser.parse_args()
    labels, classified = enrich(arguments.case_root)
    print(json.dumps({"materials": labels, "classified_cells": classified}))


if __name__ == "__main__":
    main()
