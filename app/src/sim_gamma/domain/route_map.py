"""Integrate scored dose-rate grids in the fixed STEP coordinate system."""
from pathlib import Path

import numpy as np
from sim_gamma.domain.route_study import ACCUMULATED_ERROR_LIMIT


def route_map_alignment(positions):
    """Describe the physical registration of maps in the canonical load mesh."""
    if not positions or any("yaw_degrees" not in position for position in positions):
        return None
    reference = positions[0]
    turns = []
    for before, after in zip(positions, positions[1:]):
        delta = (after["yaw_degrees"] - before["yaw_degrees"] + 180) % 360 - 180
        if not np.isclose(delta, 0, atol=1e-9):
            turns.append({"from_position": before["name"], "to_position": after["name"], "yaw_degrees": float(delta)})
    return {
        "mesh_frame": "load-local",
        "reference_position": reference["name"],
        "reference_yaw_degrees": reference["yaw_degrees"],
        "turns": turns,
    }


def write_route_dose_map(case_root: Path, positions, times_minutes, destination: Path, *, sum_rates=False,
                         accumulated_error_target=ACCUMULATED_ERROR_LIMIT, deterministic=False):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy
    from sim_gamma.domain.route_study import accumulated_precision, precision_status
    from sim_gamma.simulators.openmc.structured_postprocess import RegularMeshSpec, summarize_relative_error, write_compressed_vti

    grid = None
    accumulated = None
    active = None
    materials = None
    variance = None
    estimated = None
    absolute_maps = 0
    zero_contributions = 0
    for position, minutes in zip(positions, times_minutes, strict=True):
        filename = (case_root / position["summary"]).parent / "dose_distribution.vti"
        if not filename.is_file():
            raise ValueError(f"Falta el mapa de tasas de {position['name']}")
        reader = vtk.vtkXMLImageDataReader()
        reader.SetFileName(str(filename))
        reader.Update()
        image = reader.GetOutput()
        cells = image.GetCellData()
        scalar = cells.GetScalars()
        if scalar is None:
            raise ValueError(f"El mapa de {position['name']} no contiene tasas")
        geometry = (image.GetDimensions(), image.GetOrigin(), image.GetSpacing())
        hidden = cells.GetArray(vtk.vtkDataSetAttributes.GhostArrayName())
        rates = vtk_to_numpy(scalar)
        mask = np.ones(rates.shape, dtype=bool) if hidden is None else (vtk_to_numpy(hidden) & vtk.vtkDataSetAttributes.HIDDENCELL) == 0
        if grid is None:
            grid = geometry
            accumulated = np.zeros(rates.shape, dtype=np.float64)
            variance = np.zeros(rates.shape, dtype=np.float64)
            estimated = np.ones(rates.shape, dtype=bool)
            active = mask.copy()
            tags = cells.GetArray("material_id")
            materials = None if tags is None else vtk_to_numpy(tags).copy()
        elif grid != geometry or not np.array_equal(mask, active):
            raise ValueError(f"La malla de {position['name']} difiere del sistema local del STEP")
        if not np.isfinite(rates[mask]).all():
            raise ValueError(f"El mapa de {position['name']} contiene tasas inválidas")
        weight = 1 if sum_rates else minutes / 60
        if weight == 0:
            continue
        # Transport already expresses each pose in the STEP frame by applying
        # the inverse pose to the sources and any neighbors. A cell index is
        # therefore the same physical part of the load at every position.
        contribution = rates[mask].astype(np.float64) * weight
        accumulated[mask] += contribution
        if deterministic:
            continue
        absolute_errors = cells.GetArray("absolute_std_dev")
        if absolute_errors is not None:
            absolute = vtk_to_numpy(absolute_errors)[mask]
            valid = np.isfinite(absolute) & (absolute >= 0)
            estimated[mask] &= valid
            variance[mask] += np.square(np.where(valid, absolute, 0).astype(float) * weight)
            absolute_maps += 1
            zero_contributions += int(np.count_nonzero(valid & (rates[mask] == 0)))
            continue
        errors = cells.GetArray("relative_error")
        if errors is None:
            estimated[mask] = False
        else:
            relative = vtk_to_numpy(errors)[mask]
            valid = np.isfinite(relative) & (relative >= 0) & (rates[mask] > 0)
            estimated[mask] &= valid
            variance[mask] += np.square(contribution * np.where(valid, relative, 0))

    points, low, spacing = grid
    dimension = tuple(count - 1 for count in points)
    high = tuple(origin + count * step for origin, count, step in zip(low, dimension, spacing, strict=True))
    if not np.any(active) or not np.isfinite(accumulated[active]).all():
        raise ValueError("El mapa combinado no contiene valores válidos")
    unit = "kgy_h" if sum_rates else "kgy"
    scalar_name = "summed_rate_kgy_h" if sum_rates else "accumulated_dose_kgy"
    mesh = RegularMeshSpec(dimension, low, high)
    bounds = {
        f"minimum_{unit}": float(np.min(accumulated[active])),
        f"maximum_{unit}": float(np.max(accumulated[active])),
        "alignment": route_map_alignment(positions),
    }
    if deterministic:
        write_compressed_vti(accumulated, mesh, destination, inside_mask=active,
                             material_ids=materials, scalar_name=scalar_name)
        return bounds
    valid = active & estimated & (accumulated > 0) & np.isfinite(variance)
    relative_error = np.full(accumulated.shape, np.nan, dtype=np.float32)
    relative_error[valid] = np.sqrt(variance[valid]) / accumulated[valid]
    statistics = summarize_relative_error(relative_error, active)
    scored = {**statistics, "active_cells": int(np.count_nonzero(active))}
    precision = {**precision_status(scored), **accumulated_precision(scored, accumulated_error_target)}
    write_compressed_vti(accumulated, mesh, destination,
                         inside_mask=active, material_ids=materials, relative_error=relative_error,
                         absolute_std_dev=np.where(valid, np.sqrt(variance), np.nan),
                         scalar_name=scalar_name)
    return {**bounds,
            **statistics, "precision": {**precision, "basis": "independent-position-tallies"},
            "absolute_uncertainty_maps": absolute_maps, "observed_zero_contributions": zero_contributions}
