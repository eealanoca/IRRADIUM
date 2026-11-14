from __future__ import annotations

import json
import hashlib
import math
import os
import re
import fcntl
from tempfile import TemporaryDirectory
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np


PHITS_DEFAULT_ROOT = Path("/opt/phits")


@dataclass(frozen=True)
class MeshDefinition:
    dimension: tuple[int, int, int]
    lower_left: tuple[float, float, float]
    upper_right: tuple[float, float, float]

    @property
    def spacing(self) -> tuple[float, float, float]:
        return tuple(
            (high - low) / cells
            for low, high, cells in zip(
                self.lower_left, self.upper_right, self.dimension
            )
        )

    @property
    def cell_count(self) -> int:
        return int(np.prod(self.dimension, dtype=np.int64))


@dataclass(frozen=True)
class PhitsModel:
    input_paths: tuple[Path, ...]
    geometry_path: Path
    mesh: MeshDefinition
    material_surfaces: dict[str, Path]
    material_labels: list[dict[str, Any]]
    phits_material_ids: dict[str, int]
    geometry_translation_cm: tuple[float, float, float]
    source_count: int
    photon_rate_per_second: float
    source_activity_reference_date: str
    variance_reduction: dict[str, Any]
    histories_per_batch: int
    batches: int
    chunk_batch_layouts: tuple[tuple[int, int], ...]
    geometry_check_path: Path | None = None
    material_fraction_cache: Path | None = None
    component_surfaces: dict[str, list[Path]] | None = None

    @property
    def simulated_particles(self) -> int:
        return sum(histories * batches for histories, batches in self.chunk_batch_layouts)

    @property
    def input_path(self) -> Path:
        """Compatibilidad para modelos que caben en una sola corrida PHITS."""
        return self.input_paths[0]


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _mesh_for_bounds(bounds: np.ndarray, cell_size_cm: float) -> MeshDefinition:
    if not math.isfinite(cell_size_cm) or cell_size_cm <= 0:
        raise ValueError("El tamaño de celda PHITS debe ser positivo")
    lower = np.asarray(bounds[0], dtype=float)
    upper = np.asarray(bounds[1], dtype=float)
    dimension = np.maximum(np.ceil((upper - lower) / cell_size_cm), 1).astype(int)
    # Mantener exactamente la misma convención que OpenMC: conservar el tamaño
    # solicitado y repartir cualquier holgura simétricamente alrededor de la
    # carga.  Ajustar el espaciado a los límites CAD produce dos mallas con el
    # mismo número de celdas pero centros distintos, impidiendo una comparación
    # dosimétrica celda a celda.
    center = (lower + upper) / 2.0
    half_extent = dimension.astype(float) * cell_size_cm / 2.0
    adjusted_lower = center - half_extent
    adjusted_upper = center + half_extent
    return MeshDefinition(
        tuple(int(value) for value in dimension),
        tuple(float(value) for value in adjusted_lower),
        tuple(float(value) for value in adjusted_upper),
    )


def _translated_components(step_path: Path, cache_root: Path, work_root: Path, target_center_cm):
    import trimesh

    from sim_gamma.application.services.cad_preview import CadPreviewService

    service = CadPreviewService(cache_root)
    manifest = service.derive_component_meshes(step_path)
    parts = []
    load_meshes = []
    for item in manifest["parts"]:
        source = service.component_mesh_path(manifest["digest"], item["file"])
        mesh = trimesh.load_mesh(source, force="mesh", process=True)
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        mesh.apply_scale(0.1)  # STEP/STL de usuario: mm; PHITS: cm.
        parts.append({**item, "mesh": mesh, "surface_path": source})
        if item["material"] != "air":
            load_meshes.append(mesh)
    if not load_meshes:
        raise ValueError("El STEP no contiene un componente de carga distinto de air")

    combined = trimesh.util.concatenate(load_meshes)
    native_bounds = np.asarray(combined.bounds, dtype=float)
    target = native_bounds.mean(axis=0) if target_center_cm is None else np.asarray(target_center_cm, dtype=float)
    translation = target - np.mean(native_bounds, axis=0)
    # These masks depend on the load frame, not the irradiation position.
    surface_key = hashlib.sha256(
        json.dumps([manifest["digest"], translation.tolist()]).encode("ascii")
    ).hexdigest()
    surface_root = cache_root / "material-surfaces-v1" / surface_key
    surface_root.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[Any]] = {}
    for part in parts:
        part["mesh"].apply_translation(translation)
        if part["material"] != "air":
            grouped.setdefault(part["material"], []).append(part["mesh"])

    # Component STEP files preserve the assembly names and placements exactly,
    # including components containing several solids. No geometric matching is needed.
    import cadquery as cq
    for part in parts:
        native = service.component_cad_path(manifest["digest"], part["cad_file"])
        model = cq.importers.importStep(str(native))
        solids = model.solids().vals()
        if not solids or any(not solid.isValid() for solid in solids):
            raise ValueError(f"El componente {part['name']} no contiene sólidos CAD válidos.")
        shape = cq.Compound.makeCompound(solids).scale(.1).translate(tuple(translation))
        part["cad_shape"] = shape
        part["native_step"] = native
        part["solid_count"] = len(solids)
        part["assembly_step"] = step_path
        part["translation_cm"] = translation

    material_surfaces: dict[str, Path] = {}
    with (surface_root / "surfaces.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for material, meshes in grouped.items():
            destination = surface_root / f"{material}.stl"
            if not destination.is_file():
                with TemporaryDirectory(dir=surface_root) as directory:
                    staged = Path(directory) / destination.name
                    trimesh.util.concatenate(meshes).export(staged)
                    staged.replace(destination)
            material_surfaces[material] = destination
    load_bounds = np.asarray(
        trimesh.util.concatenate(
            [part["mesh"] for part in parts if part["material"] != "air"]
        ).bounds,
        dtype=float,
    )
    air_parts = [part["mesh"] for part in parts if part["material"] == "air"]
    air_bounds = (
        np.asarray(trimesh.util.concatenate(air_parts).bounds, dtype=float)
        if air_parts
        else None
    )
    return parts, material_surfaces, load_bounds, air_bounds, tuple(translation.tolist())


def _nastran_number(token):
    token = token.strip().upper().replace("D", "E")
    if "E" not in token:
        token = re.sub(r"(?<=[0-9])([+-])(?=\d+$)", r"E\1", token)
    return float(token)


def _cad_mesh_volume_audit(gmsh, assigned):
    """Compare each CAD region with its linear-tetrahedron volume."""
    node_ids, coordinates, _ = gmsh.model.mesh.getNodes()
    order = np.argsort(node_ids)
    node_ids = np.asarray(node_ids)[order]
    coordinates = np.asarray(coordinates).reshape(-1, 3)[order]
    audit = []
    for tag, owner in assigned.items():
        cad_volume = float(gmsh.model.occ.getMass(3, tag))
        _, connectivity = gmsh.model.mesh.getElementsByType(4, tag)
        connectivity = np.asarray(connectivity).reshape(-1, 4)
        mesh_volume = 0.0
        for start in range(0, len(connectivity), 16384):
            points = coordinates[np.searchsorted(node_ids, connectivity[start:start + 16384])]
            mesh_volume += float(np.abs(np.linalg.det(points[:, 1:] - points[:, :1])).sum() / 6)
        relative = abs(mesh_volume - cad_volume) / cad_volume if cad_volume > 0 else math.inf
        audit.append({"component": owner["name"], "material": owner["material"],
                      "cad_volume_cm3": cad_volume, "tetra_volume_cm3": mesh_volume,
                      "relative_volume_difference": relative})
    return audit


def _write_conformal_cad_bdf(parts, destination, material_ids):
    """Mesh touching CAD bodies together so interfaces share triangles and nodes."""
    import gmsh
    from cadquery import exporters

    gmsh.initialize(readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.model.add("conformal_load")
        entities = []
        owners = []
        assembly = parts[0].get("assembly_step")
        if assembly is not None and all(part.get("assembly_step") == assembly for part in parts):
            # Import in centimetres before OCC builds the shapes. Scaling
            # already-imported shapes with dilate converts analytic faces
            # to splines and can change the volume of curved solids.
            gmsh.option.setString("Geometry.OCCTargetUnit", "CM")
            imported = [entity for entity in gmsh.model.occ.importShapes(str(assembly)) if entity[0] == 3]
            gmsh.model.occ.synchronize()
            named_parts = {part["name"]: part for part in parts}
            imported_owners = [named_parts.get(gmsh.model.getEntityName(*entity).split("/")[-1])
                               for entity in imported]
            imported_counts = {}
            for owner in imported_owners:
                if owner is not None:
                    name = owner["name"]
                    imported_counts[name] = imported_counts.get(name, 0) + 1
            expected_counts = {part["name"]: part["solid_count"] for part in parts}
            if (len(named_parts) == len(parts) and all(imported_owners)
                    and imported_counts == expected_counts):
                entities = imported
                owners = imported_owners
                gmsh.model.occ.translate(entities, *parts[0]["translation_cm"])
            else:
                gmsh.clear()
                gmsh.model.add("conformal_load")
        if not entities:
            for index, part in enumerate(parts):
                step = part.get("native_step")
                if step is None:
                    # These temporary files already carry the desired numeric
                    # coordinates, with a millimetre label written by CadQuery.
                    gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
                    step = destination.parent / f"native_{index:04d}.step"
                    exporters.export(part["cad_shape"], str(step))
                else:
                    gmsh.option.setString("Geometry.OCCTargetUnit", "CM")
                volumes = [entity for entity in gmsh.model.occ.importShapes(str(step)) if entity[0] == 3]
                if not volumes:
                    raise ValueError(f"Gmsh no importó el volumen {part['name']}")
                if part.get("native_step") is not None:
                    gmsh.model.occ.translate(volumes, *part["translation_cm"])
                entities.extend(volumes)
                owners.extend([part] * len(volumes))
        print(f"PHITS CAD: {len(entities)} sólidos; unificando interfaces", flush=True)
        gmsh.model.occ.synchronize()
        if len(entities) > 1:
            _, fragments = gmsh.model.occ.fragment(entities[:1], entities[1:])
        else:
            fragments = [entities]
        gmsh.model.occ.synchronize()
        assigned = {}
        grouped = {}
        for owner, fragments_for_body in zip(owners, fragments, strict=True):
            for dimension, tag in fragments_for_body:
                if dimension != 3:
                    continue
                if tag in assigned:
                    previous = assigned[tag]
                    if previous["material"] == owner["material"]:
                        continue
                    raise ValueError(f"Los sólidos CAD {previous['name']} y {owner['name']} se superponen.")
                assigned[tag] = owner
                grouped.setdefault(owner["material"], []).append(tag)
        for material, tags in grouped.items():
            gmsh.model.addPhysicalGroup(3, tags, material_ids[material])
        gmsh.option.setNumber("General.NumThreads", min(8, os.cpu_count() or 1))
        gmsh.option.setNumber("Mesh.Algorithm3D", 10)
        bounds = gmsh.model.getBoundingBox(-1, -1)
        extent = max(high - low for low, high in zip(bounds[:3], bounds[3:], strict=True))
        # The CAD tessellation resolves surfaces, independently of tally voxels.
        maximum_size = extent / 10  # CAD coordinates are in cm.
        gmsh.option.setNumber("Mesh.MeshSizeMax", maximum_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", extent / 100)
        # Bound refinement from tiny fillets; audit every CAD region and
        # automatically refine again when its volume is not preserved.
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 32)
        # Smoothly extend surface sizes into each volume. Ignoring them
        # creates elongated tetrahedra and stalls HXT's shape optimization.
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.option.setNumber("Mesh.SaveElementTagType", 2)
        tolerance = .01
        for attempt in range(3):
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 32 * 2 ** attempt)
            print(f"PHITS CAD: generando la malla superficial (intento {attempt + 1})", flush=True)
            gmsh.model.mesh.generate(2)
            # Curvature controls boundary fidelity. Once those triangles are
            # fixed, interior tetrahedra need no CAD curvature evaluations.
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            print("PHITS CAD: generando tetraedros conformes", flush=True)
            try:
                gmsh.model.mesh.generate(3)
            except Exception:
                print("PHITS CAD: reintentando tetraedros con Delaunay", flush=True)
                gmsh.model.mesh.clear(gmsh.model.getEntities(3))
                gmsh.option.setNumber("Mesh.Algorithm3D", 1)
                gmsh.model.mesh.generate(3)
            audit = _cad_mesh_volume_audit(gmsh, assigned)
            worst = max(audit, key=lambda item: item["relative_volume_difference"])
            difference = worst["relative_volume_difference"]
            print(f"PHITS CAD: diferencia máxima de volumen {difference:.3%} ({worst['component']})", flush=True)
            if difference <= tolerance:
                break
            if attempt == 2:
                raise ValueError(f"El mallado no conserva el volumen de {worst['component']} "
                                 f"({difference:.2%}; tolerancia geométrica {tolerance:.0%}). "
                                 "No se iniciará el transporte. Revisa los detalles pequeños del CAD.")
            gmsh.model.mesh.clear()
            gmsh.option.setNumber("Mesh.MeshSizeMin", extent / (100 * 2 ** (attempt + 1)))
            gmsh.option.setNumber("Mesh.MeshSizeMax", maximum_size / 2 ** (attempt + 1))
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 32 * 2 ** (attempt + 1))
        print("PHITS CAD: exportando la malla validable", flush=True)
        gmsh.write(str(destination))
        destination.with_suffix(".audit.json").write_text(json.dumps(
            {"relative_volume_tolerance": tolerance, "regions": audit}, indent=2) + "\n")
    finally:
        gmsh.finalize()


def _write_tetrahedral_bdf(parts, destination: Path, material_ids: dict[str, int], instances=None) -> Path:
    """Malla cada sólido STEP como tetraedros NASTRAN etiquetados por universo."""

    import gmsh
    from sim_gamma.domain.route_geometry import rotation_z

    destination.parent.mkdir(parents=True, exist_ok=True)
    active_parts = [part for part in parts if part["material"] != "air"]
    component_files = []
    if active_parts and all("cad_shape" in part for part in active_parts):
        native_bdf = destination.parent / "conformal.bdf"
        _write_conformal_cad_bdf(active_parts, native_bdf, material_ids)
        component_files.append(native_bdf)
    else:
        for index, part in enumerate(parts, start=1):
            if part["material"] == "air":
                continue
            stl_path = destination.parent / f"component_{index:04d}.stl"
            bdf_path = destination.parent / f"component_{index:04d}.bdf"
            gmsh.initialize(readConfigFiles=False)
            try:
                gmsh.option.setNumber("General.Terminal", 0)
                gmsh.model.add(f"component_{index:04d}")
                if "cad_shape" in part:
                    from cadquery import exporters
                    native_step = destination.parent / f"component_{index:04d}.step"
                    exporters.export(part["cad_shape"], str(native_step))
                    entities = gmsh.model.occ.importShapes(str(native_step))
                    gmsh.model.occ.synchronize()
                    volumes = [tag for dimension, tag in entities if dimension == 3]
                else:
                    part["mesh"].export(stl_path)
                    gmsh.merge(str(stl_path))
                    gmsh.model.mesh.classifySurfaces(math.radians(40.0), True, True, math.pi)
                    gmsh.model.mesh.createGeometry()
                    surfaces = [tag for _, tag in gmsh.model.getEntities(2)]
                    if not surfaces:
                        raise ValueError(f"Gmsh no importó el componente {part['name']}")
                    loop = gmsh.model.geo.addSurfaceLoop(surfaces)
                    volumes = [gmsh.model.geo.addVolume([loop])]
                    gmsh.model.geo.synchronize()
                if not volumes:
                    raise ValueError(f"Gmsh no importó el volumen {part['name']}")
                universe = material_ids[part["material"]]
                gmsh.model.addPhysicalGroup(3, volumes, universe)
                gmsh.option.setNumber("Mesh.Algorithm3D", 1)
                gmsh.option.setNumber("Mesh.ElementOrder", 1)
                gmsh.option.setNumber("Mesh.SaveElementTagType", 2)
                gmsh.model.mesh.generate(3)
                gmsh.write(str(bdf_path))
                component_files.append(bdf_path)
            finally:
                gmsh.finalize()

    def field(line: str, start: int) -> int:
        return int(line[start : start + 8].strip())

    node_offset = 0
    element_offset = 0
    with destination.open("w", encoding="ascii") as output:
        output.write("$ Created by IRRADIUM for PHITS\n")
        for component in component_files:
            for instance in instances or [None]:
                maximum_node = node_offset
                maximum_element = element_offset
                if instance is not None:
                    rotation = rotation_z(instance["angle"])
                with component.open(encoding="ascii") as source:
                    for raw in source:
                        line = raw.rstrip("\r\n")
                        if line.startswith("GRID"):
                            node_id = field(line, 8) + node_offset
                            maximum_node = max(maximum_node, node_id)
                            tail = line[16:]
                            if instance is not None:
                                point = [_nastran_number(line[i:i + 8]) for i in (24, 32, 40)]
                                position = rotation @ point + instance["translation"]
                                coordinates = [f"{value:8.3f}" for value in position]
                                if any(len(value) != 8 for value in coordinates):
                                    raise ValueError("Coordenada de instancia fuera del rango NASTRAN")
                                tail = line[16:24] + "".join(coordinates) + line[48:]
                            output.write(f"{line[:8]}{node_id:<8d}{tail}\n")
                        elif line.startswith("CTETRA"):
                            element_id = field(line, 8) + element_offset
                            maximum_element = max(maximum_element, element_id)
                            rewritten = f"{line[:8]}{element_id:<8d}{line[16:24]}"
                            for start in range(24, min(len(line), 56), 8):
                                token = line[start:start + 8]
                                rewritten += f"{int(token.strip()) + node_offset:<8d}" if token.strip() else token
                            rewritten += line[56:] if len(line) > 56 else ""
                            output.write(rewritten + "\n")
                node_offset = maximum_node
                element_offset = maximum_element
        output.write("ENDDATA\n")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise OSError("Gmsh no generó la geometría tetraédrica PHITS")
    if active_parts and all("cad_shape" in part for part in active_parts):
        destination.with_suffix(".audit.json").write_bytes(native_bdf.with_suffix(".audit.json").read_bytes())
    return destination


def _decayed_activity(
    source: dict[str, Any], decay_constant_per_day: float, reference_date: date
) -> float:
    initial = datetime.strptime(source["initial_reading_date"], "%Y-%m-%d").date()
    elapsed_days = (reference_date - initial).days
    return float(source["intensity"]) * math.exp(-decay_constant_per_day * elapsed_days)


def _focus_line_source_on_load(
    lower: np.ndarray,
    upper: np.ndarray,
    load_bounds: np.ndarray,
    safety_margin_degrees: float = 0.5,
) -> dict[str, Any]:
    """Construye el cono mínimo que cubre la carga desde una fuente lineal.

    El eje se define desde el punto medio de la fuente al centro de la caja de
    la carga. El semiángulo se calcula usando las ocho esquinas de la caja y
    *ambos* extremos de la fuente, por lo que no se pierde ninguna dirección
    primaria que pueda intersectar la carga. La fracción de ángulo sólido se
    usa después como peso físico del cono condicionado.
    """

    bounds = np.asarray(load_bounds, dtype=float)
    if bounds.shape != (2, 3) or np.any(bounds[1] <= bounds[0]):
        raise ValueError("Los límites de la carga deben tener forma (2, 3)")
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    midpoint = 0.5 * (lower + upper)
    target_center = np.mean(bounds, axis=0)
    if np.all((midpoint >= bounds[0]) & (midpoint <= bounds[1])):
        return {"directional": False, "solid_angle_fraction": 1.0}

    axis = target_center - midpoint
    axis_norm = float(np.linalg.norm(axis))
    if axis_norm == 0.0:
        return {"directional": False, "solid_angle_fraction": 1.0}
    axis /= axis_norm
    corners = np.asarray(
        [
            [x, y, z]
            for x in bounds[:, 0]
            for y in bounds[:, 1]
            for z in bounds[:, 2]
        ],
        dtype=float,
    )
    maximum_angle = 0.0
    for source_point in (lower, upper):
        directions = corners - source_point
        norms = np.linalg.norm(directions, axis=1)
        if np.any(norms == 0.0):
            return {"directional": False, "solid_angle_fraction": 1.0}
        cosines = np.clip((directions @ axis) / norms, -1.0, 1.0)
        maximum_angle = max(maximum_angle, float(np.max(np.arccos(cosines))))

    half_angle = min(
        math.pi,
        maximum_angle + math.radians(float(safety_margin_degrees)),
    )
    # Un cono casi esférico no aporta reducción de varianza y puede ser
    # numéricamente frágil en PHITS; en ese caso se conserva la isotropía.
    if half_angle >= math.radians(175.0):
        return {"directional": False, "solid_angle_fraction": 1.0}
    solid_angle_fraction = 0.5 * (1.0 - math.cos(half_angle))
    return {
        "directional": True,
        "axis": axis,
        "half_angle_degrees": math.degrees(half_angle),
        "solid_angle_fraction": solid_angle_fraction,
    }


def _source_records(
    sources_document: dict[str, Any],
    radionuclide: str,
    deterministic: dict[str, Any],
    load_bounds: np.ndarray,
    activity_reference_date: date | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[float, float]], dict[str, Any]]:
    activity_reference_date = activity_reference_date or date.today()
    nuclide = sources_document.get("radionuclides", {}).get(radionuclide)
    if not nuclide:
        raise ValueError(f"Radionúclido no configurado: {radionuclide}")
    emissions = [
        (float(item["value"]), float(item.get("probability", 1.0)))
        for item in nuclide.get("emissions", [])
        if item.get("type", "gamma") == "gamma"
    ]
    photon_yield = sum(probability for _, probability in emissions)
    if photon_yield <= 0:
        raise ValueError("El radionúclido no tiene emisiones gamma válidas")
    normalized = [(energy, probability / photon_yield) for energy, probability in emissions]
    decay_constant = math.log(2.0) / float(nuclide["half_life"])
    rotation_point = np.asarray(deterministic["geometric_center_cm"], dtype=float)
    angles = deterministic["step_angle_degrees_best_real"]
    translations = [float(value) / 10.0 for value in deterministic["best_length_points_mm"]]
    records: list[dict[str, Any]] = []
    for translation in translations:
        for angle_degrees in angles:
            theta = math.radians(float(angle_degrees))
            cosine, sine = math.cos(theta), math.sin(theta)
            for source in sources_document.get("sources", []):
                lower = np.asarray(source["lower_point"], dtype=float) / 10.0
                upper = np.asarray(source["upper_point"], dtype=float) / 10.0
                lower[2] += translation
                upper[2] += translation
                for point in (lower, upper):
                    dx, dy = point[0] - rotation_point[0], point[1] - rotation_point[1]
                    point[0] = rotation_point[0] + cosine * dx - sine * dy
                    point[1] = rotation_point[1] + sine * dx + cosine * dy
                activity = _decayed_activity(source, decay_constant, activity_reference_date)
                physical_strength = activity * photon_yield
                focus = _focus_line_source_on_load(lower, upper, load_bounds)
                records.append(
                    {
                        "lower": lower,
                        "upper": upper,
                        **focus,
                        # PHITS muestrea la distribución angular condicionada
                        # al cono. Esta compensación Omega/(4*pi) conserva la
                        # densidad angular de la fuente isotrópica original.
                        "strength": physical_strength
                        * float(focus["solid_angle_fraction"]),
                        "physical_strength": physical_strength,
                    }
                )
    if not records:
        raise ValueError("El caso no contiene fuentes activas")
    focused = [item for item in records if item["directional"]]
    fractions = [float(item["solid_angle_fraction"]) for item in focused]
    physical_total = sum(float(item["physical_strength"]) for item in records)
    useful_total = sum(float(item["strength"]) for item in records)
    diagnostics = {
        "method": "dynamic-load-cone-plus-photon-importance-splitting",
        "unbiased_for_primary_rays_intersecting_load": True,
        "normalization": "solid-angle-fraction-omega-over-4pi",
        "outside_cone_air_scatter_omitted": True,
        "dynamic_target_bounds_cm": load_bounds.tolist(),
        "photon_importance_load": 4.0,
        "photon_importance_air": 1.0,
        "source_emission": "dynamic-cone-covering-entire-load",
        "focused_sources": len(focused),
        "total_sources": len(records),
        "solid_angle_fraction_minimum": min(fractions) if fractions else 1.0,
        "solid_angle_fraction_mean": (
            useful_total / physical_total if physical_total > 0 else 1.0
        ),
        "solid_angle_fraction_maximum": max(fractions) if fractions else 1.0,
        "estimated_primary_sampling_gain": (
            physical_total / useful_total if useful_total > 0 else 1.0
        ),
        "physical_photon_rate_per_second": physical_total,
        "cone_photon_rate_per_second": useful_total,
    }
    return records, normalized, diagnostics


def _phits_material_sections(materials: list[dict[str, Any]], required: Iterable[str]):
    by_name = {str(item["name"]).strip().lower(): item for item in materials}
    required_names = ["air", *sorted(set(required) - {"air"})]
    missing = [name for name in required_names if name not in by_name]
    if missing:
        raise ValueError("Faltan materiales PHITS: " + ", ".join(missing))
    ids = {name: index + 1 for index, name in enumerate(required_names)}
    universes = {name: 5001 + index for index, name in enumerate(required_names[1:])}
    lines = ["[ Material ]"]
    cell_materials = {}
    for name in required_names:
        item = by_name[name]
        lines.append(f"MAT[{ids[name]}]  $ {name}")
        fraction_type = str(item.get("fraction_type", "weight")).strip().lower()
        for element, fraction in item["elements"].items():
            value = float(fraction)
            if fraction_type == "weight":
                value = -abs(value)
            lines.append(f"  {element:<4} {value:.12g}")
        cell_materials[name] = (ids[name], float(item["density"]))
    return lines, cell_materials, universes


def _source_section(records, emissions) -> list[str]:
    total = sum(float(item["strength"]) for item in records)
    lines = ["[ Source ]", f"  totfact = {total:.16e}"]
    for item in records:
        weight = float(item["strength"]) / total
        lines.append(f"<source> = {weight:.16e}")
        lower = item["lower"]
        upper = item["upper"]
        # Las fuentes actuales son verticales. s-type=1 representa exactamente
        # una fuente cilíndrica uniforme de radio cero entre z0 y z1.
        lines.extend(
            [
                "  s-type = 1",
                "  proj = photon",
                f"  x0 = {lower[0]:.12g}",
                f"  y0 = {lower[1]:.12g}",
                f"  z0 = {min(lower[2], upper[2]):.12g}",
                f"  z1 = {max(lower[2], upper[2]):.12g}",
                f"  r0 = {item.get('radius_cm', 0):.12g}",
            ]
        )
        if item["directional"]:
            axis = item["axis"]
            lines.extend(
                [
                    f"  dir = {float(axis[2]):.12g}",
                    f"  phi = {math.degrees(math.atan2(float(axis[1]), float(axis[0]))):.12g}",
                    f"  dom = {float(item['half_angle_degrees']):.12g}",
                ]
            )
        else:
            lines.append("  dir = all")
        lines.extend(["  e-type = 8", f"  ne = {len(emissions)}"])
        lines.extend(f"    {energy:.12g} {probability:.12g}" for energy, probability in emissions)
    return lines


def _route_source_records(document, radionuclide, pose, load_bounds, reference, activity_date):
    from sim_gamma.domain.route_dose import SOURCE_DIAMETER_MM, source_endpoints
    from sim_gamma.domain.route_geometry import to_load_frame

    nuclide = document["radionuclides"][radionuclide]
    emissions = [(float(row["value"]), float(row["probability"])) for row in nuclide["emissions"]
                 if row.get("type", "gamma") == "gamma"]
    photon_yield = sum(probability for _, probability in emissions)
    if photon_yield <= 0:
        raise ValueError("El radionúclido no tiene emisiones gamma válidas")
    decay = math.log(2) / float(nuclide["half_life"])
    center = load_bounds.mean(axis=0)
    height = load_bounds[1, 2] - load_bounds[0, 2]
    records = []
    for source in document["sources"]:
        lower, upper = to_load_frame(np.asarray(source_endpoints(source)) / 10, pose, center, height, reference)
        strength = _decayed_activity(source, decay, activity_date) * photon_yield
        records.append({"lower": lower, "upper": upper, "strength": strength,
                        "physical_strength": strength, "directional": False,
                        "radius_cm": SOURCE_DIAMETER_MM / 20})
    if not records:
        raise ValueError("El caso no contiene fuentes activas")
    return records, [(energy, probability / photon_yield) for energy, probability in emissions]


def _batch_layout(
    particles: int,
    workers: int = 1,
    preferred_batches: int | None = None,
) -> tuple[int, int]:
    if particles < 1:
        raise ValueError("El número de partículas PHITS debe ser positivo")
    workers = max(1, int(workers))
    target = (
        min(100, max(10, int(math.ceil(particles / 1_000_000))))
        if preferred_batches is None
        else max(2, int(preferred_batches))
    )
    batches = max(workers, int(math.ceil(target / workers)) * workers)
    histories = int(math.ceil(particles / batches))
    return histories, batches


def prepare_phits_model(
    case_root: Path,
    phits_root: Path | None = None,
    cpu_workers: int = 1,
    route_context=None,
    output_root: Path | None = None,
) -> PhitsModel:
    case_root = Path(case_root).resolve()
    input_root = case_root / "input"
    output_root = output_root or case_root / "monte_carlo"
    work_root = output_root / "work" / "phits"
    work_root.mkdir(parents=True, exist_ok=True)
    simulation = _read_json(input_root / "simulation_parameters.json")
    if route_context is not None:
        from sim_gamma.domain.route_study import study_simulation

        simulation = study_simulation(simulation, route_context[-1])
    deterministic = _read_json(case_root / "deterministic" / "result.json") if route_context is None else None
    sources = _read_json(input_root / "sources.json")
    materials_document = _read_json(input_root / "materials.json")
    cell_size = float(simulation.get("monte_carlo", {}).get("cell_size_cm", 2.0))
    particles = int(simulation.get("monte_carlo", {}).get("particles", 1_000_000))
    requested_batches = int(simulation.get("monte_carlo", {}).get("batches", 20))
    photon_importance_load = float(
        simulation.get("monte_carlo", {}).get("photon_importance_load", 4.0)
    )
    if not np.isfinite(photon_importance_load) or photon_importance_load < 1.0:
        raise ValueError("La importancia fotónica PHITS de la carga debe ser >= 1")
    reference_text = simulation.get("monte_carlo", {}).get("source_activity_reference_date")
    activity_reference_date = date.fromisoformat(reference_text) if reference_text else date.today()
    parts, material_surfaces, load_bounds, air_bounds, translation = _translated_components(
        input_root / "load.step",
        case_root.parent.parent / "cache" / "phits-step-components",
        work_root,
        deterministic["geometric_center_cm"] if deterministic is not None else None,
    )
    required = set(material_surfaces)
    material_lines, cell_materials, universes = _phits_material_sections(
        materials_document.get("materials", []), required
    )
    geometry_path = work_root / "load.bdf"
    geometry_metadata = work_root / "load.bdf.json"
    geometry_bounds = load_bounds
    instances = None
    if route_context is not None:
        from sim_gamma.domain.route_geometry import occupied_placements

        _, _, position, activity_reference_date, route = route_context
        instances = occupied_placements(route, position, load_bounds)
        geometry_bounds = np.array([
            np.min([item["low"] for item in instances], axis=0),
            np.max([item["high"] for item in instances], axis=0),
        ])
        records, emissions = _route_source_records(
            sources, simulation.get("radionuclide", "Co-60"), position, load_bounds,
            route.get("position_reference", "center"), activity_reference_date,
        )
        method = route.get("variance_reduction", "analog")
        photon_importance_load = 4.0 if method == "phits_importance" else 1.0
        variance_reduction = {"method": method, "source_emission": "isotropic",
                              "outside_cone_air_scatter_omitted": False}
    else:
        records, emissions, variance_reduction = _source_records(
            sources, simulation.get("radionuclide", "Co-60"), deterministic, load_bounds, activity_reference_date,
        )

    digest = hashlib.sha256((input_root / "load.step").read_bytes())
    digest.update(json.dumps({
        "mesher": "conformal-native-step-volume-audit-v2",
        "translation_cm": translation,
        "universes": universes,
        "parts": [(part["name"], part["material"]) for part in parts if part["material"] != "air"],
        "instances": None if instances is None else [
            (item["angle"], item["translation"].tolist()) for item in instances
        ],
    }, sort_keys=True).encode("utf-8"))
    geometry_key = digest.hexdigest()
    shared_root = case_root.parent.parent / "cache" / "phits-tetrahedra" / geometry_key
    shared_root.mkdir(parents=True, exist_ok=True)
    shared_geometry = shared_root / "load.bdf"
    cached_key = None
    if geometry_metadata.is_file():
        try:
            cached_key = _read_json(geometry_metadata).get("key")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    if not geometry_path.is_file() or cached_key != geometry_key:
        with (shared_root / "geometry.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not shared_geometry.is_file():
                with TemporaryDirectory(dir=shared_root) as directory:
                    staged = Path(directory) / "load.bdf"
                    _write_tetrahedral_bdf(parts, staged, universes,
                                          instances=instances if instances and len(instances) > 1 else None)
                    staged.replace(shared_geometry)
                    audit_path = staged.with_suffix(".audit.json")
                    if audit_path.is_file():
                        audit_path.replace(shared_root / "load.audit.json")
            geometry_path.unlink(missing_ok=True)
            os.link(shared_geometry, geometry_path)
            shared_audit = shared_root / "load.audit.json"
            if shared_audit.is_file():
                geometry_path.with_suffix(".audit.json").write_bytes(shared_audit.read_bytes())
        geometry_metadata.write_text(
            json.dumps({"key": geometry_key}, indent=2) + "\n", encoding="utf-8"
        )
    geometry_check_path = None
    if instances is not None and len(instances) > 1:
        # Rigid placements preserve the validated internal mesh. Their
        # bounding boxes have already been checked for pairwise overlap.
        shared_validation = shared_root / "one-load-validation.bdf"
        with (shared_root / "validation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not shared_validation.is_file():
                with TemporaryDirectory(dir=shared_root) as directory:
                    staged = Path(directory) / "load.bdf"
                    _write_tetrahedral_bdf(parts, staged, universes)
                    staged.replace(shared_validation)
            geometry_check_path = work_root / "load-validation.bdf"
            geometry_check_path.unlink(missing_ok=True)
            os.link(shared_validation, geometry_check_path)
    variance_reduction["photon_importance_load"] = photon_importance_load
    mesh = _mesh_for_bounds(load_bounds, cell_size)
    histories, batches = _batch_layout(
        particles,
        cpu_workers,
        preferred_batches=requested_batches,
    )
    all_source_points = np.vstack(
        [np.vstack((item["lower"], item["upper"])) for item in records]
    )
    if air_bounds is None or route_context is not None:
        padding = 50.0 if route_context is not None else 25.0
        world_low = np.minimum(geometry_bounds[0], np.min(all_source_points, axis=0)) - padding
        world_high = np.maximum(geometry_bounds[1], np.max(all_source_points, axis=0)) + padding
    else:
        world_low = np.minimum(air_bounds[0], np.min(all_source_points, axis=0)) - 1.0
        world_high = np.maximum(air_bounds[1], np.max(all_source_points, axis=0)) + 1.0
    load_low, load_high = load_bounds[0] - 0.01, load_bounds[1] + 0.01
    tetra_low, tetra_high = geometry_bounds[0] - 1.0, geometry_bounds[1] + 1.0
    if variance_reduction.get("method") == "phits_windows":
        guide_low, guide_high = world_low - .037, world_high + .039
        variance_reduction["guide_mesh"] = {
            "lower_left": guide_low.tolist(), "upper_right": guide_high.tolist(),
            "dimension": np.maximum(1, np.ceil((guide_high - guide_low) / 20)).astype(int).tolist(),
        }
    air_id, air_density = cell_materials["air"]
    phits_root = Path(phits_root or os.environ.get("PHITSPATH", PHITS_DEFAULT_ROOT)).resolve()

    input_paths = []
    # Dividir la corrida conserva todas las fuentes; la prueba física en
    # PHITS 3.341 comprobará el límite de subsecciones de esta versión.
    # corrida conserva todas las fuentes y después permite sumar los tallies
    # físicamente normalizados, sin aproximar el rack ni descartar posiciones.
    chunks = [records[start : start + 500] for start in range(0, len(records), 500)]
    total_strength = sum(float(item["strength"]) for item in records)
    allocated = 0
    chunk_batch_layouts = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        chunk_strength = sum(float(item["strength"]) for item in chunk)
        chunk_particles = (
            particles - allocated
            if chunk_index == len(chunks)
            else max(1, int(round(particles * chunk_strength / total_strength)))
        )
        allocated += chunk_particles
        chunk_histories, chunk_batches = _batch_layout(
            chunk_particles,
            cpu_workers,
            preferred_batches=requested_batches,
        )
        chunk_batch_layouts.append((chunk_histories, chunk_batches))
        lines = [
            "[ Title ]",
            f"IRRADIUM PHITS comparison - {case_root.name} - part {chunk_index}/{len(chunks)}",
            "",
            "[ Parameters ]",
            "  icntl = 0",
            f"  maxcas = {chunk_histories}",
            f"  maxbch = {chunk_batches}",
            *([f"  rseed = {104729 + route_context[0] * 200 + chunk_index * 2}"] if route_context else []),
            "  istdev = 1",
            "  itall = -1",
            "  negs = 1",
            "  emin(12) = 0.1",
            "  emin(13) = 0.1",
            "  itetvol = 1",
            "  itetauto = 0",
            "  itgchk = 0",
            f"  file(1) = {phits_root}",
            f"  file(6) = phits-{chunk_index:03d}.out",
            "",
            *_source_section(chunk, emissions),
            "",
            *material_lines,
            "",
            "[ Surface ]",
            f"  10 rpp {load_low[0]:.12g} {load_high[0]:.12g} {load_low[1]:.12g} {load_high[1]:.12g} {load_low[2]:.12g} {load_high[2]:.12g}",
            f"  11 rpp {tetra_low[0]:.12g} {tetra_high[0]:.12g} {tetra_low[1]:.12g} {tetra_high[1]:.12g} {tetra_low[2]:.12g} {tetra_high[2]:.12g}",
            f"  20 rpp {world_low[0]:.12g} {world_high[0]:.12g} {world_low[1]:.12g} {world_high[1]:.12g} {world_low[2]:.12g} {world_high[2]:.12g}",
            "  90 so 1000000.0",
            "",
            "[ Cell ]",
            # PHITS 3.341 agrega '.bdf' a nfile; pasar la extensión duplica
            # el sufijo y produce un error Fortran al abrir la geometría.
            # NASTRAN BDF 3.341 interpreta nodos en metros y los convierte a cm
            # (factor 100); nuestra malla contiene cm, por lo que TSFAC=0.01.
            f"  101 {air_id} {-air_density:.12g} -11 U=1 LAT=3 nfile={geometry_path.stem} TSFAC=0.01",
            "  1 0 -10 FILL=1",
            *(
                ["  3 0 10 -11 FILL=1", f"  2 {air_id} {-air_density:.12g} 11 -20"]
                if route_context is not None else [f"  2 {air_id} {-air_density:.12g} 10 -20"]
            ),
            "  999 -1 20",
        ]
        for material in sorted(required):
            mat_id, density = cell_materials[material]
            lines.append(f"  {universes[material]} {mat_id} {-density:.12g} -90 U={universes[material]}")
        lines.extend([
            "",
            "[ Importance ]",
            "  part = photon",
            "  reg imp",
            f"  1 {photon_importance_load:.12g}",
            "  2 1.0",
            *(["  3 1.0"] if route_context is not None else []),
        ])
        nx, ny, nz = mesh.dimension
        low, high = mesh.lower_left, mesh.upper_right
        lines.extend(
            [
            "",
            "[ T-Deposit ]",
            "  title = PHITS EGS5 absorbed dose rate",
            "  mesh = xyz",
            "  x-type = 2",
            f"  xmin = {low[0]:.12g}",
            f"  xmax = {high[0]:.12g}",
            f"  nx = {nx}",
            "  y-type = 2",
            f"  ymin = {low[1]:.12g}",
            f"  ymax = {high[1]:.12g}",
            f"  ny = {ny}",
            "  z-type = 2",
            f"  zmin = {low[2]:.12g}",
            f"  zmax = {high[2]:.12g}",
            f"  nz = {nz}",
            "  output = dose",
            "  unit = 0",
            "  factor = 3.6",
            "  part = all",
            "  axis = xy",
            f"  file = deposit-{chunk_index:03d}.out",
            "  epsout = 0",
            "  vtkout = 0",
            "  vtkfmt = 1",
            "",
            "[ End ]",
            ]
        )
        input_path = work_root / f"sim_gamma_phits_{chunk_index:03d}.inp"
        input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        input_paths.append(input_path)
    labels = [
        {"id": index + 1, "name": name}
        for index, name in enumerate(sorted(required))
    ]
    component_surfaces = {}
    for part in parts:
        if part["material"] != "air":
            component_surfaces.setdefault(part["material"], []).append(part["surface_path"])
    return PhitsModel(
        input_paths=tuple(input_paths),
        geometry_path=geometry_path,
        mesh=mesh,
        material_surfaces=material_surfaces,
        material_labels=labels,
        phits_material_ids={name: cell_materials[name][0] for name in required},
        geometry_translation_cm=translation,
        source_count=len(records),
        photon_rate_per_second=sum(
            float(item["physical_strength"]) for item in records
        ),
        source_activity_reference_date=activity_reference_date.isoformat(),
        variance_reduction=variance_reduction,
        histories_per_batch=histories,
        batches=batches,
        chunk_batch_layouts=tuple(chunk_batch_layouts),
        geometry_check_path=geometry_check_path,
        material_fraction_cache=case_root.parent.parent / "cache" / "phits-material-volume-fractions",
        component_surfaces=component_surfaces,
    )
