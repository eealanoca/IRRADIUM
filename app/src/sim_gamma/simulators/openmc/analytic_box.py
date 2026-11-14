"""Recognize and transport exact homogeneous rectangular loads."""
from pathlib import Path

import numpy as np


def rectangular_surface_bounds(path: Path):
    import trimesh

    surface = trimesh.load_mesh(path, force="mesh", process=True)
    if len(surface.faces) != 12 or not surface.is_watertight or not surface.is_winding_consistent:
        return None
    bounds = np.asarray(surface.bounds) * .1
    triangles = np.asarray(surface.triangles) * .1
    on_low = np.isclose(triangles, bounds[0], rtol=0, atol=1e-7)
    on_high = np.isclose(triangles, bounds[1], rtol=0, atol=1e-7)
    if not np.all(on_low | on_high) or np.any(bounds[1] <= bounds[0]):
        return None
    corners = on_high.astype(int)
    sides = {}
    for triangle in corners:
        fixed = np.flatnonzero(np.ptp(triangle, axis=0) == 0)
        if len(fixed) != 1:
            return None
        axis = int(fixed[0])
        sides.setdefault((axis, int(triangle[0, axis])), []).append({tuple(vertex) for vertex in triangle})
    if len(sides) != 6:
        return None
    for (axis, _), faces in sides.items():
        if len(faces) != 2:
            return None
        shared = faces[0] & faces[1]
        if len(shared) != 2 or len(faces[0] | faces[1]) != 4:
            return None
        edge = np.array(list(shared))
        if not np.all(np.ptp(edge, axis=0)[np.arange(3) != axis] == 1):
            return None
    if not np.isclose(abs(surface.volume) * .001, np.prod(bounds[1] - bounds[0]), rtol=1e-7):
        return None
    return bounds


def rectangular_geometry(bounds, transport_bounds, material, surrounding_material):
    import openmc

    def region(limits, boundary):
        box = None
        for axis, plane in enumerate((openmc.XPlane, openmc.YPlane, openmc.ZPlane)):
            coordinate = ("x0", "y0", "z0")[axis]
            lower = plane(**{coordinate: limits[0][axis]}, boundary_type=boundary)
            upper = plane(**{coordinate: limits[1][axis]}, boundary_type=boundary)
            slab = +lower & -upper
            box = slab if box is None else box & slab
        return box

    bounds, transport_bounds = np.asarray(bounds, dtype=float), np.asarray(transport_bounds, dtype=float)
    for limits in (bounds, transport_bounds):
        if limits.shape != (2, 3) or not np.isfinite(limits).all() or np.any(limits[1] <= limits[0]):
            raise ValueError("Los límites geométricos deben ser finitos y definir un volumen positivo.")
    if np.any(transport_bounds[0] >= bounds[0]) or np.any(transport_bounds[1] <= bounds[1]):
        raise ValueError("El dominio de transporte debe contener completamente la carga, con espacio exterior.")
    world = region(transport_bounds, "vacuum")
    load = world & region(bounds, "transmission")
    return openmc.Geometry([
        openmc.Cell(name="Carga rectangular", fill=material, region=load),
        openmc.Cell(name="Medio exterior a la carga", fill=surrounding_material, region=world & ~load),
    ])


def homogeneous_box_exterior(h5m_path: Path, load_material: str):
    """Preserve the actual DAGMC exterior, including an unassigned (void) complement."""
    import h5py

    with h5py.File(h5m_path, "r") as mesh:
        labels = {
            tag.tobytes().decode().replace("\x00", "")[4:]
            for tag in mesh["/tstt/tags/NAME/values"]
            if tag.tobytes().decode().startswith("mat:")
        }
    if load_material not in labels:
        return False, None
    extra = labels - {load_material}
    if not extra:
        return True, None
    if extra in ({"air"}, {"air_comp"}):
        return True, "air"
    return False, None


def validate_source_clearance(sources, bounds):
    """Reject cylindrical sources that physically intersect a solid box load."""
    box = np.asarray(bounds, dtype=float)
    for index, source in enumerate(sources, 1):
        origin = np.asarray(source.space.origin, dtype=float)
        radius = float(source.space.r.b)
        low_z, high_z = origin[2] + source.space.z.a, origin[2] + source.space.z.b
        if min(high_z, box[1, 2]) <= max(low_z, box[0, 2]):
            continue
        distance = np.maximum(np.maximum(box[0, :2] - origin[:2], origin[:2] - box[1, :2]), 0)
        if np.dot(distance, distance) < radius * radius - 1e-12:
            raise ValueError(f"La fuente cilíndrica {index} intersecta la carga. Revisa las posiciones y las dimensiones antes de simular.")
