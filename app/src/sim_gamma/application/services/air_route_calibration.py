"""Publish a free-field air calibration in the shared route results format."""

from dataclasses import asdict
import math
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np

from sim_gamma.application.services.case_store import CaseStore
from sim_gamma.application.services.source_inventory import SourceInventory
from sim_gamma.domain.route_dose import SOURCE_DIAMETER_MM
from sim_gamma.domain.route_geometry import to_load_frame
from sim_gamma.domain.irradiators import current_irradiator
from sim_gamma.domain.route_map import write_route_dose_map
from sim_gamma.physics.deterministic.air_reference import line_source_fluences
from sim_gamma.physics.parameters import load_physical_parameters
from sim_gamma.simulators.openmc.structured_postprocess import (
    RegularMeshSpec, summarize_dose, write_compressed_vti,
)

AIR_CALIBRATION_METHOD = "deterministic_air"
AIR_COMPARISON_METHOD = "monte_carlo_air"


def air_calibration_mesh(dimensions_mm, cell_size_cm, position_count):
    x, y, z = (value / 10 for value in dimensions_mm)
    dimensions = tuple(math.ceil(size / cell_size_cm) for size in (x, y, z))
    cells = math.prod(dimensions)
    if cells > 1_000_000 or cells * position_count > 25_000_000:
        raise ValueError("La malla es demasiado fina para este recorrido. Aumenta el tamaño de celda.")
    return RegularMeshSpec(dimensions, (-x / 2, -y / 2, 0), (x / 2, y / 2, z))


def _intersects_bounds(endpoints, low, high):
    """Clip the source segment against the clearance envelope of the load."""
    lower, upper = endpoints
    entry, exit = 0., 1.
    for point, delta, left, right in zip(lower, upper - lower, low, high, strict=True):
        if abs(delta) < 1e-10:
            if not left <= point <= right:
                return False
            continue
        hits = sorted(((left - point) / delta, (right - point) / delta))
        entry, exit = max(entry, hits[0]), min(exit, hits[1])
        if entry > exit:
            return False
    return True


def write_air_calibration(root: Path, case_id, route, mesh, activity_date):
    started = perf_counter()
    run_id = uuid4().hex
    results = root / "results"
    radionuclide = route["radionuclide"]
    source_file = root / "input" / "sources.json"
    inventory = SourceInventory(source_file)
    sources = inventory.sources(activity_date, radionuclide)["sources"]
    if not sources:
        raise ValueError("Configura al menos una fuente para calcular la calibración.")
    if activity_date.isoformat() < max(source["initial_reading_date"] for source in sources):
        raise ValueError("Usa una fecha igual o posterior a todas las lecturas iniciales.")
    _, factor = load_physical_parameters(radionuclide, "air", source_file, root / "input" / "materials.json")
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("El factor dosimétrico de alanina debe ser positivo.")

    # The STEP is centered in XY with its bottom at Z=0. VTK cells are X-fastest.
    low = np.asarray(mesh.lower_left)
    high = np.asarray(mesh.upper_right)
    center = (low + high) / 2
    axes = [origin + (np.arange(count) + .5) * step
            for origin, count, step in zip(low, mesh.dimension, mesh.spacing, strict=True)]
    z, y, x = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
    points_mm = np.column_stack((x.ravel(), y.ravel(), z.ravel())) * 10
    endpoints_cm = np.array([[source["lower_point_mm"], source["upper_point_mm"]] for source in sources]) / 10
    materials = [{"id": 1, "name": "Aire seco"}]
    material_ids = np.ones(mesh.cell_count, dtype=np.uint16)
    radius_mm = SOURCE_DIAMETER_MM / 2
    rates = []
    for index, position in enumerate(route["positions"]):
        pose = {**position, "yaw_degrees": position["yaw_degrees"] + route["cad_offset_degrees"]}
        endpoints_mm = to_load_frame(endpoints_cm, pose, center, high[2] - low[2], route["position_reference"]) * 10
        if any(_intersects_bounds(segment, low * 10 - radius_mm, high * 10 + radius_mm)
               for segment in endpoints_mm):
            raise ValueError(f"La carga de referencia en {pose['name']} intersecta o queda demasiado cerca de una fuente.")
        local_sources = [
            {**source, "lower_point_mm": endpoints[0], "upper_point_mm": endpoints[1]}
            for source, endpoints in zip(sources, endpoints_mm, strict=True)
        ]
        values = np.empty(mesh.cell_count, dtype=np.float32)
        for offset in range(0, mesh.cell_count, 32_768):
            block = slice(offset, offset + 32_768)
            values[block] = line_source_fluences(points_mm[block], local_sources, radius_mm) * factor
        if not np.isfinite(values).all() or np.any(values <= 0):
            raise ValueError(f"No se obtuvieron tasas positivas y finitas en {pose['name']}.")
        output = results / "air_calibration" / run_id / f"{index + 1:03d}"
        write_compressed_vti(values, mesh, output / "dose_distribution.vti", material_ids=material_ids)
        summary = summarize_dose(values, mesh)
        summary_path = output / "result.json"
        CaseStore._write_json(summary_path, {
            **summary, "run_id": run_id, "materials": materials,
            "analysis_method": AIR_CALIBRATION_METHOD, "cell_size_cm": max(mesh.spacing),
            "mesh": asdict(mesh),
        })
        rates.append({
            **pose, "summary": str(summary_path.relative_to(root)), "materials": materials,
            "mean_kgy_h": summary["average_dose"], "minimum_kgy_h": summary["minimum_dose"],
            "maximum_kgy_h": summary["maximum_dose"],
        })

    scenario_id = uuid4().hex
    scenario_path = results / "dose_scenarios" / f"{scenario_id}.json"
    count = len(rates)
    combined = write_route_dose_map(root, rates, [60] * count, scenario_path.with_suffix(".vti"),
                                   sum_rates=True, deterministic=True)
    CaseStore._write_json(scenario_path, {
        "run_id": run_id, "quantity": "rate_sum", "positions": rates, "dose_map": combined,
        "summed_rate_kgy_h": sum(rate["mean_kgy_h"] for rate in rates),
        "request": {"start": 0, "end": count - 1, "mode": "rate_sum"},
    })
    study = {
        "case_id": case_id, "run_id": run_id, "irradiator_type": current_irradiator.get(),
        "analysis_method": AIR_CALIBRATION_METHOD, "activity_date": activity_date.isoformat(),
        "radionuclide": radionuclide, "source_count": len(sources),
        "occupancy": "isolated", "unit": "kGy/h", "cell_size_cm": max(mesh.spacing),
        "score_interval": [0, count - 1], "requested_position_count": count,
        "positions": rates, "complete": True, "wall_seconds": perf_counter() - started,
    }
    archive = results / "studies"
    archive.mkdir(exist_ok=True)
    CaseStore._write_json(archive / f"{run_id}.json", study)
    CaseStore._write_json(results / "route_rates.json", study)
    return {"case_id": case_id, "run_id": run_id, "position_count": count}
