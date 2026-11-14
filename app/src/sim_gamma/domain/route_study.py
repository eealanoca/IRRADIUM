"""Study settings and precision criteria shared by both transport engines."""
from copy import deepcopy
import math
from sim_gamma.compute_resources import available_cpu_threads


INITIAL_ERROR_LIMIT = 0.10
RECOMMENDED_ERROR_LIMIT = 0.05
ACCUMULATED_ERROR_LIMIT = 0.02
DEFAULT_STUDY_HISTORIES = 100_000_000
STANDARD_STUDY_SECONDS = 15 * 60
EXTENDED_STUDY_SECONDS = 24 * 60 * 60


def selected_position_indices(positions: list[dict], request: dict) -> list[int]:
    """Select scored positions while retaining the order of the physical route."""
    names = request.get("position_names")
    if names is not None:
        selected = set(names)
        indices = [index for index, position in enumerate(positions) if position["name"] in selected]
        if not selected or len(selected) != len(names) or len(indices) != len(selected):
            raise ValueError("Selecciona posiciones distintas del recorrido guardado.")
        return indices
    start, end = request["start"], request["end"]
    if not 0 <= start <= end < len(positions):
        raise ValueError("El intervalo de cálculo debe estar dentro del recorrido.")
    return list(range(start, end + 1))


def study_simulation(simulation: dict, route: dict) -> dict:
    parameters = deepcopy(simulation)
    monte_carlo = parameters.setdefault("monte_carlo", {})
    for key in ("particles", "batches", "cell_size_cm", "relative_error_target", "dose_tally", "adaptive_statistics"):
        if key in route:
            monte_carlo[key] = route[key]
    monte_carlo["weight_window_method"] = "magic" if route.get("variance_reduction") in {"magic", "source_box_magic"} else "none"
    cores = route.get("cpu_cores", -1)
    automatic_cores = available_cpu_threads()
    parameters.setdefault("compute", {})["cpu_cores"] = automatic_cores if cores == -1 else cores
    for section, overrides in route.get("quick_parameters", {}).items():
        parameters[section].update(overrides)
    return parameters


def active_material_bins(material_bins: dict, active_mask) -> dict:
    """Do not let unreported CAD fringes determine the stopping criterion."""
    import numpy as np
    mask = np.asarray(active_mask, dtype=bool)
    result = {}
    for name, details in material_bins.items():
        pairs = details["bins"]
        selected = pairs[mask[pairs[:, 0]]]
        if selected.size:
            result[name] = {**details, "bins": selected}
    if not result:
        raise ValueError("No hay vóxeles activos. Reduce el tamaño de celda para representar esta carga.")
    return result


def study_precision(positions: list, complete: bool = True) -> dict:
    initial = bool(positions) and all(point.get("precision", {}).get("meets_initial", False) for point in positions)
    recommended = bool(positions) and all(point.get("precision", {}).get("meets_recommended", False) for point in positions)
    return {
        "all_requested_positions_available": bool(complete),
        "meets_initial": bool(complete and initial), "meets_recommended": bool(complete and recommended),
        "available_positions_meet_initial": initial, "available_positions_meet_recommended": recommended,
    }


def accumulated_precision(summary: dict, target: float = ACCUMULATED_ERROR_LIMIT) -> dict:
    """Judge the worst active voxel, never a percentile or a mean error."""
    # Fixed-budget runs store None to indicate that automatic refinement is off.
    # Their reported precision still uses the configured default threshold.
    if target is None:
        target = ACCUMULATED_ERROR_LIMIT
    active = summary.get("active_cells", 0)
    maximum = summary.get("relative_error_maximum")
    covered = active > 0 and summary.get("cells_with_error_estimate", 0) == active
    return {
        "accumulated_error_target": target,
        "meets_accumulated_target": bool(
            covered and maximum is not None and math.isfinite(maximum) and 0 <= maximum <= target
        ),
    }


def precision_status(summary: dict) -> dict:
    active = summary.get("active_cells") or summary.get("expected_active_cells") or 0
    estimated = summary.get("cells_with_error_estimate", 0)
    maximum = summary.get("relative_error_maximum")
    covered = active > 0 and estimated == active and maximum is not None and math.isfinite(maximum)
    initial = bool(covered and maximum < INITIAL_ERROR_LIMIT)
    recommended = bool(covered and maximum < RECOMMENDED_ERROR_LIMIT)
    return {
        "state": "recommended" if recommended else "initial" if initial else "insufficient",
        "initial_limit": INITIAL_ERROR_LIMIT,
        "recommended_limit": RECOMMENDED_ERROR_LIMIT,
        "meets_initial": initial,
        "meets_recommended": recommended,
        "active_cells": active,
        "cells_with_error_estimate": estimated,
        "relative_error_maximum": maximum,
        "basis": "all-active-output-voxels",
    }
