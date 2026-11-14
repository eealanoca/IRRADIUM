"""Importance sampling towards a load and the loads that can scatter into it."""
from pathlib import Path
import numpy as np
import openmc
from .mixture_source import source_library


def scene_mixture(sources, boxes, focused_fraction=.9, target_share=.5):
    bounds = np.asarray(boxes, dtype=float)
    if bounds.ndim != 3 or bounds.shape[1:] != (2, 3) or not 1 <= len(bounds) <= 128:
        raise ValueError("La propuesta angular requiere entre 1 y 128 cargas")
    if not np.isfinite(bounds).all() or np.any(bounds[:, 1] <= bounds[:, 0]):
        raise ValueError("Los límites de las cargas no son válidos")
    library = source_library(Path(__file__).with_suffix(".cpp"))
    result = []
    for source in sources:
        lower = np.asarray(source.space.origin) + [0, 0, source.space.z.a]
        height = source.space.z.b - source.space.z.a
        values = [source.strength, *lower, source.space.r.b, height, focused_fraction, target_share,
                  len(bounds), len(source.energy.x)]
        for energy, probability in zip(source.energy.x, source.energy.p, strict=True):
            values.extend((energy, probability))
        values.extend(bounds.ravel().tolist())
        result.append(openmc.CompiledSource(library, parameters=" ".join(map(str, values)), strength=source.strength))
    return result
