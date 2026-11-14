"""Allocate histories to a combined interval using its per-voxel variances."""
import json
import math
from pathlib import Path

import numpy as np

INITIAL_INTERVAL_HISTORIES = 4_000_000
INTERVAL_BATCHES = 100
MAX_INTERVAL_PASSES = 16
INTERVAL_PLANNING_MARGIN = .97


def reference_budgets(case_root, study, positions, target, maximum):
    """Reuse variance estimates only; convergence is always measured anew."""
    run_id = study.get("budget_reference_run_id") if study else None
    if not run_id:
        return None
    path = case_root / "results" / "studies" / f"{run_id}.json"
    if not path.is_file():
        return None
    reference = json.loads(path.read_text())
    from sim_gamma.simulators.openmc.geometry_contract import TRANSPORT_GEOMETRY_REVISION
    if reference.get("transport_geometry_revision") != TRANSPORT_GEOMETRY_REVISION:
        return None
    if not reference.get("complete") or reference.get("quick"):
        return None
    by_name = {position["name"]: position for position in reference["positions"]}
    selected = []
    for position in positions:
        previous = by_name.get(position["name"])
        if previous is None or any(previous.get(key) != position.get(key) for key in ("center_mm", "yaw_degrees")):
            return None
        selected.append(previous)
    # Allow a new physical configuration: this is a cost estimate, not reused
    # samples or a convergence claim. Every position is simulated and checked.
    plan = plan_interval_histories(case_root, selected, target, maximum, initial_histories=INITIAL_INTERVAL_HISTORIES)
    return [min(maximum, max(INITIAL_INTERVAL_HISTORIES, count)) for count in plan]


def evaluate_interval(case_root: Path, positions, destination: Path, target: float, passes: int) -> dict:
    from sim_gamma.domain.route_map import write_route_dose_map

    combined = write_route_dose_map(case_root, positions, [60] * len(positions), destination, sum_rates=True)
    maximum = combined.get("relative_error_maximum")
    precision = combined["precision"]
    covered = precision["active_cells"] > 0 and precision["cells_with_error_estimate"] == precision["active_cells"]
    return {
        **combined, "target": target,
        "meets_target": bool(covered and maximum is not None and maximum <= target),
        "passes": passes, "map": str(destination.relative_to(case_root)),
    }


def plan_interval_histories(case_root: Path, positions, target: float, maximum: int, *, initial_histories=None, return_projection=False):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    rates, deviations, counts, costs = [], [], [], []
    active = None
    geometry = None
    for position in positions:
        path = case_root / position["summary"]
        reader = vtk.vtkXMLImageDataReader()
        reader.SetFileName(str(path.with_name("dose_distribution.vti")))
        reader.Update()
        image = reader.GetOutput()
        cells = image.GetCellData()
        grid = (image.GetDimensions(), image.GetOrigin(), image.GetSpacing())
        ghosts = cells.GetArray("vtkGhostType")
        mask = np.ones(image.GetNumberOfCells(), dtype=bool) if ghosts is None else (vtk_to_numpy(ghosts) & 32) == 0
        if active is not None and (geometry != grid or not np.array_equal(mask, active)):
            raise ValueError("Las mallas del intervalo deben coincidir para planificar sus historias.")
        active, geometry = mask, grid
        rates.append(vtk_to_numpy(cells.GetScalars())[mask].astype(float))
        absolute = cells.GetArray("absolute_std_dev")
        if absolute is not None:
            deviations.append(vtk_to_numpy(absolute)[mask].astype(float))
        else:
            array = cells.GetArray("relative_error")
            relative = np.full(mask.sum(), np.nan) if array is None else vtk_to_numpy(array)[mask].astype(float)
            deviations.append(np.where(np.isfinite(relative) & (relative >= 0) & (rates[-1] > 0),
                                       rates[-1] * relative, np.nan))
        summary = json.loads(path.read_text())
        count = int(position["simulated_particles"])
        counts.append(count)
        costs.append(max(float(summary.get("transport_wall_seconds", 1)) / count, 1e-12))
    rates, deviations = np.asarray(rates), np.asarray(deviations)
    sample_counts = np.asarray(counts, dtype=np.int64)
    budgets = sample_counts.copy() if initial_histories is None else np.full(len(counts), min(maximum, initial_histories), dtype=np.int64)
    incomplete = ~np.isfinite(deviations) | (deviations < 0) | ~np.isfinite(rates) | (rates < 0)
    if incomplete.any():
        missing_plan = [min(maximum, 2 * count) if missing.any() else count
                        for count, missing in zip(budgets.tolist(), incomplete, strict=True)]
        return {"histories": missing_plan, "projected_relative_error_maximum": None} if return_projection else missing_plan
    # a[i,v] / N[i] is position i's contribution to relative variance at voxel v.
    total = rates.sum(axis=0)
    if np.any(total <= 0):
        plan = [min(maximum, 2 * count) for count in budgets.tolist()]
        return {"histories": plan, "projected_relative_error_maximum": None} if return_projection else plan
    coefficients = (deviations / total) ** 2 * sample_counts[:, None]
    variance = np.sum(coefficients / budgets[:, None], axis=0)
    # Continuation makes an additional correction cheap; retain a small
    # planning margin and let the measured maximum decide when to stop.
    target_squared = (target * INTERVAL_PLANNING_MARGIN) ** 2
    for _ in range(10_000):
        voxel = int(np.argmax(variance))
        if variance[voxel] <= target_squared:
            break
        available = budgets < maximum
        if not np.any(available):
            break
        gain_per_history = coefficients[:, voxel] / (budgets * budgets.astype(float))
        marginal = np.where(available, gain_per_history / costs, -1)
        index = int(np.argmax(marginal))
        previous = budgets[index]
        proposed = min(maximum, int(math.ceil(previous * 1.15 / 100_000)) * 100_000)
        if proposed <= previous:
            break
        budgets[index] = proposed
        variance -= coefficients[index] * (1 / previous - 1 / proposed)
    return {"histories": budgets.tolist(), "projected_relative_error_maximum": float(np.sqrt(np.max(variance)))} if return_projection else budgets.tolist()
