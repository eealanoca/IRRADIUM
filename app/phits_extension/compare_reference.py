from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_vti(path: Path):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(str(path))
    reader.Update()
    image = reader.GetOutput()
    data = image.GetCellData()
    dose = np.asarray(
        vtk_to_numpy(data.GetArray("absorbed_dose_rate_kgy_h")), dtype=np.float64
    )
    error_array = data.GetArray("relative_error")
    error = (
        np.asarray(vtk_to_numpy(error_array), dtype=np.float64)
        if error_array is not None
        else np.full(dose.size, np.nan)
    )
    material_array = data.GetArray("material_id")
    material_ids = (
        np.asarray(vtk_to_numpy(material_array), dtype=np.int64)
        if material_array is not None
        else np.ones(dose.size, dtype=np.int64)
    )
    material_inside = material_ids != 0
    # ``material_id`` also labels partial boundary intersections in OpenMC.
    # The production volume mask (>= 50 % occupied) is stored as VTK hidden
    # cells, so comparisons must honor it just like ParaView and the result
    # summaries do.  Otherwise the comparison silently includes thin slivers
    # that neither method reports as active load voxels.
    ghost_array = data.GetArray(vtk.vtkDataSetAttributes.GhostArrayName())
    visible = (
        (np.asarray(vtk_to_numpy(ghost_array), dtype=np.uint8)
         & vtk.vtkDataSetAttributes.HIDDENCELL)
        == 0
        if ghost_array is not None
        else np.ones(dose.size, dtype=bool)
    )
    inside = material_inside & visible
    return image, dose, error, inside, material_ids


def _dose_summary(dose: np.ndarray, error: np.ndarray, mask: np.ndarray) -> dict:
    values = np.asarray(dose, dtype=np.float64)[mask]
    errors = np.asarray(error, dtype=np.float64)[mask]
    if values.size == 0:
        raise ValueError("No existen celdas activas para comparar")
    p02, p05, p10, median, p90, p95, p98 = np.percentile(
        values, [2, 5, 10, 50, 90, 95, 98]
    )
    finite_errors = errors[np.isfinite(errors)]
    return {
        "active_cells": int(values.size),
        "minimum_dose_kgy_h": float(np.min(values)),
        "maximum_dose_kgy_h": float(np.max(values)),
        "average_dose_kgy_h": float(np.mean(values)),
        "dose_p02_kgy_h": float(p02),
        "dose_p05_kgy_h": float(p05),
        "dose_p10_kgy_h": float(p10),
        "dose_median_kgy_h": float(median),
        "dose_p90_kgy_h": float(p90),
        "dose_p95_kgy_h": float(p95),
        "dose_p98_kgy_h": float(p98),
        "raw_dose_uniformity_ratio": float(np.max(values) / np.min(values)),
        "operational_dose_uniformity_ratio_p90_p10": float(p90 / p10),
        "robust_dose_uniformity_ratio_p95_p05": float(p95 / p05),
        "relative_error_median": (
            float(np.median(finite_errors)) if finite_errors.size else None
        ),
        "relative_error_p90": (
            float(np.percentile(finite_errors, 90)) if finite_errors.size else None
        ),
        "relative_error_maximum": (
            float(np.max(finite_errors)) if finite_errors.size else None
        ),
    }


def _mesh_signature(image) -> dict:
    return {
        "dimensions_points": list(image.GetDimensions()),
        "origin_cm": list(image.GetOrigin()),
        "spacing_cm": list(image.GetSpacing()),
        "cells": int(image.GetNumberOfCells()),
    }


def _method(case_root: Path) -> dict:
    result = _read_json(case_root / "monte_carlo" / "result.json")
    image, dose, error, inside, material_ids = _read_vti(
        case_root / "monte_carlo" / result.get("visualization", {}).get(
            "vtk", "dose_distribution.vti"
        )
    )
    indices = np.flatnonzero(inside)
    active = dose[indices]
    minimum_local = int(np.argmin(active))
    maximum_local = int(np.argmax(active))
    summary = _dose_summary(dose, error, inside)
    summary.update(
        {
            "backend": result.get("backend"),
            "dose_tally": result.get("dose_tally"),
            "particles": result.get("simulated_particles", result.get("particles")),
            "cell_size_cm": result.get("cell_size_cm"),
            "minimum_cell_index": int(indices[minimum_local]),
            "minimum_cell_relative_error": float(error[indices[minimum_local]]),
            "maximum_cell_index": int(indices[maximum_local]),
            "maximum_cell_relative_error": float(error[indices[maximum_local]]),
            "mesh": _mesh_signature(image),
        }
    )
    return {
        "root": case_root,
        "result": result,
        "image": image,
        "dose": dose,
        "error": error,
        "inside": inside,
        "material_ids": material_ids,
        "summary": summary,
    }


def compare(phits_root: Path, references: list[Path]) -> dict:
    phits = _method(phits_root)
    document = {
        "comparison_basis": "identical-mesh-and-visible-load-cells-raw-and-robust-statistics",
        "phits": phits["summary"],
        "references": [],
        "input_identity": {},
    }
    for name in ("load.step", "sources.json", "materials.json", "work_sections.json"):
        phits_hash = _sha256(phits_root / "input" / name)
        document["input_identity"][name] = {
            "phits_sha256": phits_hash,
            "all_references_match": all(
                _sha256(root / "input" / name) == phits_hash for root in references
            ),
        }
    for root in references:
        reference = _method(root)
        if _mesh_signature(reference["image"]) != _mesh_signature(phits["image"]):
            raise ValueError(f"La malla de {root} no coincide exactamente con PHITS")
        common = phits["inside"] & reference["inside"]
        x = phits["dose"][common]
        y = reference["dose"][common]
        phits_indices = np.flatnonzero(phits["inside"])
        reference_indices = np.flatnonzero(reference["inside"])
        phits_max = int(phits_indices[np.argmax(phits["dose"][phits_indices])])
        reference_max = int(
            reference_indices[np.argmax(reference["dose"][reference_indices])]
        )
        relative_difference = np.abs(x - y) / np.maximum(np.abs(y), 1.0e-30)
        combined_standard_deviation = np.hypot(
            np.abs(x * phits["error"][common]),
            np.abs(y * reference["error"][common]),
        )
        finite_uncertainty = np.isfinite(combined_standard_deviation)
        materials = []
        phits_labels = {
            int(item["id"]): str(item["name"])
            for item in phits["result"].get("materials", [])
        }
        reference_labels = {
            int(item["id"]): str(item["name"])
            for item in reference["result"].get("materials", [])
        }
        for material_id, material_name in sorted(phits_labels.items()):
            reference_id = next(
                (
                    key
                    for key, name in reference_labels.items()
                    if name == material_name
                ),
                None,
            )
            if reference_id is None:
                continue
            phits_mask = phits["inside"] & (phits["material_ids"] == material_id)
            reference_mask = reference["inside"] & (
                reference["material_ids"] == reference_id
            )
            material_common = phits_mask & reference_mask
            if not np.any(material_common):
                continue
            phits_material = _dose_summary(phits["dose"], phits["error"], material_common)
            reference_material = _dose_summary(
                reference["dose"], reference["error"], material_common
            )
            materials.append(
                {
                    "name": material_name,
                    "common_active_cells": int(np.count_nonzero(material_common)),
                    "phits": phits_material,
                    "reference": reference_material,
                    "average_ratio_phits_to_reference": float(
                        phits_material["average_dose_kgy_h"]
                        / reference_material["average_dose_kgy_h"]
                    ),
                    "p95_ratio_phits_to_reference": float(
                        phits_material["dose_p95_kgy_h"]
                        / reference_material["dose_p95_kgy_h"]
                    ),
                }
            )
        document["references"].append(
            {
                "name": root.name,
                **reference["summary"],
                "common_active_cells": int(np.count_nonzero(common)),
                "mean_ratio_phits_to_reference": float(np.mean(x) / np.mean(y)),
                "mean_relative_difference": float((np.mean(x) - np.mean(y)) / np.mean(y)),
                "spatial_pearson_correlation": float(np.corrcoef(x, y)[0, 1]),
                "rmse_kgy_h": float(np.sqrt(np.mean(np.square(x - y)))),
                "normalized_rmse_to_reference_mean": float(
                    np.sqrt(np.mean(np.square(x - y))) / np.mean(y)
                ),
                "cellwise_absolute_relative_difference_median": float(
                    np.median(relative_difference)
                ),
                "cellwise_absolute_relative_difference_p90": float(
                    np.percentile(relative_difference, 90)
                ),
                "cells_within_10_percent": int(np.count_nonzero(relative_difference <= 0.10)),
                "cells_within_20_percent": int(np.count_nonzero(relative_difference <= 0.20)),
                "cells_within_combined_2sigma": (
                    int(
                        np.count_nonzero(
                            finite_uncertainty
                            & (np.abs(x - y) <= 2.0 * combined_standard_deviation)
                        )
                    )
                    if np.any(finite_uncertainty)
                    else None
                ),
                "phits_dose_at_reference_max_cell_kgy_h": float(phits["dose"][reference_max]),
                "reference_dose_at_phits_max_cell_kgy_h": float(reference["dose"][phits_max]),
                "materials": materials,
            }
        )
    return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phits", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    document = compare(arguments.phits.resolve(), [path.resolve() for path in arguments.reference])
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
