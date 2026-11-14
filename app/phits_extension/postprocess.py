from __future__ import annotations

from pathlib import Path
import re

import numpy as np

from .model import PhitsModel


def _read_xy_tally(path: Path, mesh) -> np.ndarray:
    """Read PHITS 3.34 XY pages in x-fastest order, excluding plot legends."""
    nx, ny, nz = mesh.dimension
    count = nx * ny
    pages = []
    text = path.read_text(encoding="ascii", errors="replace")
    for page in re.split(r"(?m)^# \( \( data\(x,y\), x = 1, nx \), y = ny, 1, -1 \)\s*$", text)[1:]:
        _, marker, table = page.partition("hc:")
        if not marker:
            raise ValueError(f"Falta una tabla XY en {path.name}")
        values = []
        for line in table.splitlines()[1:]:
            if len(values) >= count:
                break
            values.extend(float(value.replace("D", "E")) for value in line.split())
        if len(values) != count:
            raise ValueError(f"Tabla XY incompleta en {path.name}")
        pages.append(np.asarray(values).reshape(ny, nx)[::-1])
    if len(pages) != nz:
        raise ValueError(f"{path.name} contiene {len(pages)} cortes XY, se esperaban {nz}")
    result = np.asarray(pages).reshape(-1)
    if not np.isfinite(result).all():
        raise ValueError(f"{path.name} contiene valores no finitos")
    return result


def read_deposit_pair(work: Path, index: str, mesh, *, numeric=False):
    vtk_path = work / f"deposit-{index}.vtk"
    if numeric or not vtk_path.is_file():
        dose = _read_xy_tally(work / f"deposit-{index}.out", mesh)
        errors = _read_xy_tally(work / f"deposit-{index}_err.out", mesh)
        return dose, errors, None
    arrays = _vtk_arrays(vtk_path, mesh.cell_count)
    values = _select_tally_array(arrays)
    error_path = work / f"deposit-{index}_err.vtk"
    errors = (
        _select_tally_array(_vtk_arrays(error_path, mesh.cell_count))
        if error_path.is_file() else _select_error_array(arrays, values)
    )
    return values, errors, _select_material_array(arrays)


def _vtk_arrays(path: Path, expected_cells: int):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    reader = vtk.vtkDataSetReader()
    reader.SetFileName(str(path))
    reader.ReadAllScalarsOn()
    reader.Update()
    dataset = reader.GetOutput()
    arrays = {}
    for attributes in (dataset.GetCellData(), dataset.GetPointData()):
        for index in range(attributes.GetNumberOfArrays()):
            array = attributes.GetArray(index)
            if array is None:
                continue
            values = np.asarray(vtk_to_numpy(array)).reshape(-1)
            if values.size == expected_cells:
                arrays[(array.GetName() or f"array_{index}").strip().lower()] = values
    if not arrays:
        raise ValueError(f"El VTK PHITS {path.name} no contiene {expected_cells} celdas")
    return arrays


def _select_tally_array(arrays: dict[str, np.ndarray]) -> np.ndarray:
    excluded = ("material", "region", "cell", "error", "r.err", "relative")
    candidates = [
        (name, values)
        for name, values in arrays.items()
        if not any(token in name for token in excluded)
    ]
    if not candidates:
        raise ValueError("El VTK PHITS no contiene el campo T-Deposit")
    preferred = [item for item in candidates if "dose" in item[0] or "deposit" in item[0] or "all" == item[0]]
    return np.asarray((preferred or candidates)[0][1], dtype=np.float64)


def _select_error_array(arrays: dict[str, np.ndarray], values: np.ndarray):
    for name, array in arrays.items():
        if "error" in name or "r.err" in name or "relative" in name:
            return np.asarray(array, dtype=np.float64)
    return np.full(values.size, np.nan, dtype=np.float64)


def _select_material_array(arrays: dict[str, np.ndarray]):
    for name, array in arrays.items():
        if "material" in name or name == "mat":
            return np.asarray(array, dtype=np.int64)
    return None


def combine_phits_vtk(model: PhitsModel, *, cpu_threads=1, memory_limit_mb=4096, progress_callback=None):
    total = np.zeros(model.mesh.cell_count, dtype=np.float64)
    variance = np.zeros(model.mesh.cell_count, dtype=np.float64)
    has_errors = True
    raw_materials = None
    for input_path in model.input_paths:
        index = input_path.stem.rsplit("_", 1)[-1]
        numeric = re.search(r"(?im)^\s*vtkout\s*=\s*0\b", input_path.read_text()) is not None
        values, errors, materials = read_deposit_pair(input_path.parent, index, model.mesh, numeric=numeric)
        total += values
        if np.all(np.isfinite(errors)):
            variance += np.square(values * errors)
        else:
            has_errors = False
        if raw_materials is None:
            raw_materials = materials

    relative_error = np.full(total.size, np.nan, dtype=np.float64)
    if has_errors:
        np.divide(np.sqrt(variance), np.abs(total), out=relative_error, where=total != 0)

    material_ids = np.zeros(total.size, dtype=np.uint16)
    if model.material_surfaces:
        # El VTK de 3.341 identifica incorrectamente como aire los bins de
        # borde X/Y aunque T-Deposit sí los puntúa. La máscara proviene de la
        # geometría STEP, no del metadato material de PHITS.
        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            build_material_volume_fractions,
        )

        mesh = RegularMeshSpec(model.mesh.dimension, model.mesh.lower_left, model.mesh.upper_right)
        # The cached per-body surfaces are in millimetres. A group fallback
        # must use that same frame if its component bounds overlap.
        fractions, labels = build_material_volume_fractions(
            model.material_surfaces,
            mesh,
            model.material_fraction_cache or model.input_path.parents[4] / "cache" / "phits-material-volume-fractions",
            split_disjoint=True,
            component_surface_files=model.component_surfaces,
            component_length_scale_to_cm=.1,
            component_translation_cm=model.geometry_translation_cm,
            cpu_threads=cpu_threads,
            memory_limit_mb=memory_limit_mb,
            progress_callback=progress_callback,
        )
        occupied = np.sum(fractions, axis=0) >= 0.5
        ids_by_name = {item["name"]: item["id"] for item in model.material_labels}
        best = np.argmax(fractions[:, occupied], axis=0)
        for index, label in enumerate(labels):
            material_ids[np.flatnonzero(occupied)[best == index]] = ids_by_name[label["name"]]
    elif raw_materials is not None:
        # Solo para pruebas sintéticas sin superficies STEP.
        labels_by_name = {item["name"]: item["id"] for item in model.material_labels}
        for name, phits_id in model.phits_material_ids.items():
            if name != "air":
                material_ids[raw_materials == phits_id] = labels_by_name[name]
    inside = material_ids != 0
    if not np.any(inside):
        raise ValueError("El VTK PHITS no contiene celdas dentro de la carga")
    total[~inside] = 0.0
    return total, relative_error, material_ids, inside


def extreme_summary(values, relative_error, inside):
    values_array = np.asarray(values, dtype=float)
    selected_mask = np.asarray(inside, dtype=bool) & np.isfinite(values_array)
    selected = values_array[selected_mask]
    if selected.size == 0:
        raise ValueError("No existen celdas finitas de dosis dentro de la carga PHITS")
    minimum = float(np.min(selected))
    maximum = float(np.max(selected))
    p02, p05, p10, median, p90, p95, p98 = (
        float(value)
        for value in np.percentile(selected, [2, 5, 10, 50, 90, 95, 98])
    )
    summary = {
        "minimum_dose": minimum,
        "maximum_dose": maximum,
        "average_dose": float(np.mean(selected)),
        "dose_uniformity_ratio": float(maximum / minimum) if minimum > 0 else None,
        "dose_p02": p02,
        "dose_p05": p05,
        "dose_p10": p10,
        "dose_median": median,
        "dose_p90": p90,
        "dose_p95": p95,
        "dose_p98": p98,
        "operational_dose_uniformity_ratio": p90 / p10 if p10 > 0 else None,
        "robust_dose_uniformity_ratio": p95 / p05 if p05 > 0 else None,
        "d98_d2_uniformity_ratio": p98 / p02 if p02 > 0 else None,
        "total_cells": int(values_array.size),
        "active_cells": int(selected.size),
        "statistics_basis": "all-load-cells-raw-extrema-and-percentile-dur",
    }
    errors = np.asarray(relative_error, dtype=float)[selected_mask]
    finite = errors[np.isfinite(errors)]
    if finite.size:
        summary.update(
            {
                "relative_error_median": float(np.median(finite)),
                "relative_error_p90": float(np.percentile(finite, 90)),
                "relative_error_maximum": float(np.max(finite)),
                "relative_error_minimum": float(np.min(finite)),
                "cells_relative_error_below_1_percent": int(
                    np.count_nonzero(finite < 0.01)
                ),
                "cells_relative_error_below_5_percent": int(
                    np.count_nonzero(finite <= 0.05)
                ),
                "cells_relative_error_below_10_percent": int(
                    np.count_nonzero(finite <= 0.10)
                ),
                "cells_with_error_estimate": int(finite.size),
            }
        )
    return summary
