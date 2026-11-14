"""Rigid placement in the reference frame of the load being scored."""
import math

import numpy as np


def rotation_z(angle):
    angle = math.radians(angle)
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def placement_center(pose, height_cm, reference):
    center = np.asarray(pose["center_mm"], dtype=float) / 10
    if reference == "bottom_center":
        center[2] += height_cm / 2
    return center


def to_load_frame(points_cm, pose, native_center, height_cm, reference="center"):
    world_center = placement_center(pose, height_cm, reference)
    return (np.asarray(points_cm) - world_center) @ rotation_z(pose["yaw_degrees"]) + native_center


def bounds_corners(bounds):
    return np.array([[x, y, z] for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]])


def occupied_placements(route, target, native_bounds):
    """Return rigid instances, including the target, in its fixed local tally frame."""
    bounds = np.asarray(native_bounds)
    center = bounds.mean(axis=0)
    height = bounds[1, 2] - bounds[0, 2]
    reference = route.get("position_reference", "center")
    poses = route["positions"] if route.get("occupancy") == "occupied" else [target]
    instances = []
    corners = bounds_corners(bounds)
    for pose in poses:
        angle = pose["yaw_degrees"] - target["yaw_degrees"]
        rotation = rotation_z(angle)
        local_center = to_load_frame(placement_center(pose, height, reference), target, center, height, reference)
        translation = local_center - rotation @ center
        vertices = corners @ rotation.T + translation
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        for previous in instances:
            if np.all(np.minimum(high, previous["high"]) - np.maximum(low, previous["low"]) > 1e-7):
                raise ValueError(f"Las cajas de carga se superponen en {previous['name']} y {pose['name']}. Revisa las dimensiones y el recorrido.")
        instances.append({"name": pose["name"], "angle": angle, "translation": translation,
                          "low": low, "high": high})
    return instances
