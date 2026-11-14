"""Bounded-memory inspection of the cached CAD triangulation."""
import math
from pathlib import Path
import struct

import numpy as np

from sim_gamma.workers.study_guard import available_memory_mb


def estimate_study(root: Path, preview, cell_size_cm: float, memory_limit_mb: int, *, geometry_instances: int = 1) -> dict:
    if geometry_instances < 1:
        raise ValueError("La escena debe contener al menos una carga")
    manifest = preview.derive_component_meshes(root / "input" / "load.step")
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    facets = 0
    triangle = np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    for part in manifest["parts"]:
        if part["material"] == "air":
            continue
        path = preview.component_mesh_path(manifest["digest"], part["file"])
        with path.open("rb") as stream:
            header = stream.read(84)
            if len(header) != 84:
                raise ValueError("La previsualización CAD está incompleta")
            count = struct.unpack("<I", header[80:84])[0]
            if path.stat().st_size != 84 + 50 * count:
                raise ValueError("La previsualización CAD no tiene el formato binario esperado")
            facets += count
            remaining = count
            while remaining:
                block = np.fromfile(stream, dtype=triangle, count=min(10000, remaining))
                vertices = block["vertices"].reshape(-1, 3)
                low = np.minimum(low, vertices.min(axis=0))
                high = np.maximum(high, vertices.max(axis=0))
                remaining -= len(block)
    if not np.isfinite(low).all() or np.any(high <= low):
        raise ValueError("La carga no contiene un volumen válido")
    with np.errstate(over="ignore", divide="ignore"):
        ratios = (high - low) / (10 * cell_size_cm)
    if not np.isfinite(ratios).all():
        raise ValueError("La malla es demasiado fina para representarse")
    dimensions = [max(1, math.ceil(float(value))) for value in ratios]
    count = math.prod(dimensions)
    if count > 1_000_000_000:
        raise ValueError("La malla supera mil millones de vóxeles. Aumenta el tamaño de celda.")
    materials = len({part["material"] for part in manifest["parts"]})
    # Only the target is scored, but occupied transport contains every load
    # in the route, including neighbors outside the selected study interval.
    # Native CAD, welded arrays and ray indices coexist during preparation.
    # The complex 73-body benchmark exceeded the old 160-byte/facet allowance.
    geometry_mb = facets * 640 * geometry_instances / 1024**2
    estimated = 1200 + count * (256 + 16 * materials) / 1024**2 + geometry_mb
    effective = min(memory_limit_mb, max(512, available_memory_mb() * .75))
    return {
        "cell_size_cm": cell_size_cm, "dimensions": dimensions, "cells": count,
        "estimated_ram_mb": math.ceil(estimated), "memory_limit_mb": math.floor(effective),
        "geometry_instances": geometry_instances, "estimated_geometry_ram_mb": math.ceil(geometry_mb),
        "feasible": estimated <= effective * .90,
    }
