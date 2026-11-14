"""Unbiased angular importance sampling for the suspended load."""
from pathlib import Path
import fcntl
import hashlib
import math
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np
import openmc


def source_library(source: Path | None = None) -> Path:
    source = Path(__file__).with_suffix(".cpp") if source is None else source
    runtime = Path(sys.prefix)
    flags = ["-std=c++17", "-O3", "-fno-math-errno", "-shared", "-fPIC"]
    signature = hashlib.sha256(
        source.read_bytes() + openmc.__version__.encode() + " ".join(flags).encode()
    ).hexdigest()[:16]
    target = runtime.parent / "extensions" / f"mixture-source-{signature}.so"
    target.parent.mkdir(exist_ok=True)
    with target.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not target.is_file():
            with TemporaryDirectory(dir=target.parent) as temporary:
                staged = Path(temporary) / target.name
                subprocess.run(["g++", *flags,
                                "-I", str(runtime / "include"), str(source), "-o", str(staged)],
                               check=True, capture_output=True, text=True)
                staged.replace(target)
    return target


def focus_mixture(sources, target_bounds, focused_fraction=.90, *, target_box=False):
    library = source_library()
    bounds = np.asarray(target_bounds, dtype=float)
    center = bounds.mean(axis=0)
    corners = np.array([[x, y, z] for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]])
    result = []
    for source in sources:
        origin = np.asarray(source.space.origin)
        lower = origin + [0, 0, source.space.z.a]
        height = source.space.z.b - source.space.z.a
        radius = source.space.r.b
        axis = center - (lower + [0, 0, height / 2])
        norm = np.linalg.norm(axis)
        if not norm:
            result.append(source)
            continue
        axis /= norm
        emission_corners = np.array([[lower[0]+dx, lower[1]+dy, z]
                                    for dx in (-radius, radius) for dy in (-radius, radius)
                                    for z in (lower[2], lower[2]+height)])
        vectors = corners[None, :, :] - emission_corners[:, None, :]
        lengths = np.linalg.norm(vectors, axis=2)
        if np.any(lengths == 0):
            result.append(source)
            continue
        half_angle = np.max(np.arccos(np.clip((vectors @ axis) / lengths, -1, 1))) + math.radians(1)
        if half_angle >= math.pi * .95:
            result.append(source)
            continue
        values = [source.strength, *lower, radius, height, *axis, math.cos(half_angle),
                  focused_fraction, len(source.energy.x)]
        for energy, probability in zip(source.energy.x, source.energy.p, strict=True):
            values.extend((energy, probability))
        if target_box:
            values.extend((1, *bounds[0], *bounds[1]))
        biased = openmc.CompiledSource(library, parameters=" ".join(map(str, values)), strength=source.strength)
        # Scale population-control weights to typical focused starting weights.
        # This only changes splitting efficiency, never the source likelihood.
        biased._irradium_window_scale = (1 - math.cos(half_angle)) / (2 * focused_fraction)
        result.append(biased)
    return result
