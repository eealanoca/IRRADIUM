"""Analytical free-field fluence from uniformly active line sources."""

import math

import numpy as np


def line_source_fluence(point_mm, sources, exclusion_radius_mm):
    return float(line_source_fluences(np.asarray([point_mm]), sources, exclusion_radius_mm)[0])


def line_source_fluences(points_mm, sources, exclusion_radius_mm):
    fluence_per_mm2_s = np.zeros(len(points_mm))
    for source in sources:
        lower = np.asarray(source["lower_point_mm"])
        upper = np.asarray(source["upper_point_mm"])
        length = math.dist(lower, upper)
        if length <= 0:
            raise ValueError("Una fuente tiene longitud activa nula.")
        axis = (upper - lower) / length
        displacement = points_mm - lower
        axial = np.einsum("ij,j->i", displacement, axis)
        radius = np.linalg.norm(displacement - axial[:, None] * axis, axis=1)
        if np.any(np.hypot(radius, axial - np.clip(axial, 0, length)) <= exclusion_radius_mm):
            raise ValueError("La calibración intersecta una fuente o pasa demasiado cerca de ella.")
        integral = np.empty_like(radius)
        off_axis = radius > 1e-10
        r, a = radius[off_axis], axial[off_axis]
        # atan2 retains the angle beyond either endpoint without cancellation.
        integral[off_axis] = np.arctan2(length * r, r**2 + a * (a - length)) / r
        a = axial[~off_axis]
        integral[~off_axis] = length / (a * (a - length))
        fluence_per_mm2_s += source["current_activity_bq"] * integral / (4 * math.pi * length)
    return fluence_per_mm2_s * 100
