"""Genera cargas STEP de ejemplo con metadatos de materiales reproducibles.

Cada ejemplo tiene dos representaciones:

* ``load.step``: modelo de análisis con detalle suficiente y un número moderado
  de volúmenes para no penalizar el transporte Monte Carlo.
* ``presentation.step``: modelo visual con mayor número de piezas.

Los nombres de componente comienzan con el tag de material que consume la
aplicación (por ejemplo ``fruit_crate_001`` -> ``fruit``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import cadquery as cq


AIR = {
    "name": "air",
    "elements": {"C": 0.000124, "N": 0.755267, "O": 0.231781, "Ar": 0.012827},
    "density": 0.00120479,
    "fraction_type": "weight",
}

MATERIALS = {
    "wood": {"elements": {"H": 0.060, "C": 0.500, "O": 0.440}, "density": 0.60},
    "cardboard": {"elements": {"H": 0.062, "C": 0.444, "O": 0.494}, "density": 0.70},
    "fruit": {"elements": {"H": 0.107, "C": 0.120, "N": 0.010, "O": 0.763}, "density": 0.95},
    "polyethylene": {"elements": {"H": 0.143711, "C": 0.856289}, "density": 0.92},
    "polypropylene": {"elements": {"H": 0.167714, "C": 0.832286}, "density": 0.90},
    "glass": {"elements": {"O": 0.5396, "Si": 0.3772, "B": 0.0400, "Na": 0.0280, "Ca": 0.0152}, "density": 2.23},
    "steel": {"elements": {"Fe": 0.700, "Cr": 0.190, "Ni": 0.100, "Mn": 0.010}, "density": 7.85},
    "aluminum": {"elements": {"Al": 1.0}, "density": 2.70},
    "saline": {"elements": {"H": 0.1100, "O": 0.8797, "Na": 0.0039, "Cl": 0.0064}, "density": 1.005},
    "quartz": {"elements": {"Si": 0.4674, "O": 0.5326}, "density": 2.65},
    "corundum": {"elements": {"Al": 0.5293, "O": 0.4707}, "density": 3.98},
    "topaz": {"elements": {"Al": 0.29321, "Si": 0.15261, "O": 0.34773, "F": 0.20645}, "density": 3.53},
    "plant": {"elements": {"H": 0.105, "C": 0.185, "N": 0.025, "O": 0.680, "P": 0.005}, "density": 0.82},
    "agar": {"elements": {"H": 0.067, "C": 0.400, "O": 0.533}, "density": 1.02},
    "pupae": {"elements": {"H": 0.102, "C": 0.300, "N": 0.080, "O": 0.503, "S": 0.015}, "density": 0.78},
    "spice": {"elements": {"H": 0.065, "C": 0.445, "N": 0.025, "O": 0.455, "K": 0.010}, "density": 0.58},
    "jute": {"elements": {"H": 0.061, "C": 0.445, "O": 0.494}, "density": 0.46},
    "seed": {"elements": {"H": 0.075, "C": 0.500, "N": 0.035, "O": 0.380, "P": 0.010}, "density": 0.72},
    "feed": {"elements": {"H": 0.070, "C": 0.455, "N": 0.040, "O": 0.420, "P": 0.007, "S": 0.003, "Ca": 0.005}, "density": 0.64},
    "cellulose": {"elements": {"H": 0.0622, "C": 0.4445, "O": 0.4933}, "density": 1.50},
    "cotton": {"elements": {"H": 0.0622, "C": 0.4445, "O": 0.4933}, "density": 0.35},
    "polyester": {"elements": {"H": 0.0420, "C": 0.6250, "O": 0.3330}, "density": 1.38},
}


@dataclass(frozen=True)
class Component:
    name: str
    shape: cq.Workplane


@dataclass(frozen=True)
class Example:
    slug: str
    title: str
    purpose: str
    material_names: tuple[str, ...]
    builder: Callable[[bool], list[Component]]


def box(name: str, size: tuple[float, float, float], center: tuple[float, float, float]) -> Component:
    return Component(name, cq.Workplane("XY").box(*size).translate(center))


def cylinder(name: str, radius: float, height: float, center: tuple[float, float, float]) -> Component:
    return Component(
        name,
        cq.Workplane("XY").workplane(offset=-height / 2).circle(radius).extrude(height).translate(center),
    )


def hollow_box(
    name: str,
    size: tuple[float, float, float],
    center: tuple[float, float, float],
    thickness: float,
) -> Component:
    outer = cq.Workplane("XY").box(*size)
    inner_size = tuple(value - 2 * thickness for value in size)
    inner = cq.Workplane("XY").box(*inner_size)
    return Component(name, outer.cut(inner).translate(center))


def hollow_cylinder(
    name: str,
    radius: float,
    height: float,
    center: tuple[float, float, float],
    thickness: float,
) -> Component:
    outer = cq.Workplane("XY").workplane(offset=-height / 2).circle(radius).extrude(height)
    inner = (
        cq.Workplane("XY")
        .workplane(offset=-(height - 2 * thickness) / 2)
        .circle(radius - thickness)
        .extrude(height - 2 * thickness)
    )
    return Component(name, outer.cut(inner).translate(center))


def sphere(name: str, radius: float, center: tuple[float, float, float]) -> Component:
    return Component(name, cq.Workplane("XY").sphere(radius).translate(center))


def gemstone(name: str, width: float, height: float, center: tuple[float, float, float]) -> Component:
    shape = (
        cq.Workplane("XY")
        .workplane(offset=-height / 2)
        .polygon(8, width)
        .extrude(height)
    )
    return Component(name, shape.translate(center))


def air_world() -> Component:
    return box("air_001", (16000, 14000, 5000), (0, 0, 0))


def pallet(prefix: str = "wood", z: float = -650.0) -> list[Component]:
    parts = []
    for index, y in enumerate((-600, -300, 0, 300, 600), 1):
        # Mantener una holgura real entre tablas, tacos y largueros.  Los
        # sólidos solapados pueden ser tolerados por algunos visores CAD, pero
        # no forman un dominio tetraédrico válido para PHITS.
        parts.append(box(f"{prefix}_pallet_top_{index:03d}", (1180, 110, 24), (0, y, z + 75)))
    for index, y in enumerate((-540, 0, 540), 1):
        parts.append(box(f"{prefix}_pallet_runner_{index:03d}", (1180, 90, 30), (0, y, z - 55)))
        for column, x in enumerate((-500, 0, 500), 1):
            parts.append(box(f"{prefix}_pallet_block_{index:02d}_{column:02d}", (120, 100, 90), (x, y, z + 10)))
    return parts


def food_load(presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    columns, rows, layers = (4, 5, 4) if presentation else (3, 3, 3)
    sx, sy = 1100 / columns, 1400 / rows
    for layer in range(layers):
        z = -400 + layer * 330
        for row in range(rows):
            for column in range(columns):
                number = layer * rows * columns + row * columns + column + 1
                x = -550 + sx / 2 + column * sx
                y = -700 + sy / 2 + row * sy
                parts.append(hollow_box(f"cardboard_crate_{number:03d}", (sx - 14, sy - 14, 290), (x, y, z), 8))
                if presentation:
                    for fruit_index, (dx, dy) in enumerate(((-55, -55), (55, -55), (-55, 55), (55, 55)), 1):
                        parts.append(sphere(f"fruit_unit_{number:03d}_{fruit_index:02d}", 42, (x + dx, y + dy, z + 10)))
                else:
                    parts.append(box(f"fruit_content_{number:03d}", (sx - 42, sy - 42, 250), (x, y, z)))
    # Película externa: cuatro paneles delgados, sin solapamiento con las cajas.
    wrap_bottom = -550.0
    wrap_top = -400 + (layers - 1) * 330 + 155
    wrap_height = wrap_top - wrap_bottom
    wrap_center_z = (wrap_top + wrap_bottom) / 2
    parts.extend(
        [
            box("polyethylene_wrap_front", (1116, 3, wrap_height), (0, -714, wrap_center_z)),
            box("polyethylene_wrap_back", (1116, 3, wrap_height), (0, 714, wrap_center_z)),
            box("polyethylene_wrap_left", (3, 1410, wrap_height), (-564, 0, wrap_center_z)),
            box("polyethylene_wrap_right", (3, 1410, wrap_height), (564, 0, wrap_center_z)),
        ]
    )
    return parts


def medical_load(presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    count = 24 if presentation else 12
    for i in range(count):
        column, row, layer = i % 4, (i // 4) % 3, i // 12
        x, y, z = -450 + column * 300, -480 + row * 480, -270 + layer * 620
        parts.append(hollow_box(f"cardboard_medical_case_{i+1:03d}", (270, 430, 540), (x, y, z), 8))
        parts.append(cylinder(f"saline_bag_group_{i+1:03d}", 62, 390, (x - 45, y, z)))
        parts.append(cylinder(f"polypropylene_syringe_group_{i+1:03d}", 24, 360, (x + 75, y, z)))
    parts.extend(
        box(f"steel_corner_{i+1:03d}", (14, 14, 1320), (x, y, -10))
        for i, (x, y) in enumerate(
            ((-593, -703), (593, -703), (-593, 703), (593, 703))
        )
    )
    return parts


def gemstone_load(presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    number = 36 if presentation else 18
    materials = ("quartz", "corundum", "topaz")
    for i in range(number):
        column, row, layer = i % 6, (i // 6) % 3, i // 18
        x, y, z = -500 + column * 200, -450 + row * 450, -320 + layer * 560
        parts.append(box(f"polyethylene_tray_{i+1:03d}", (180, 400, 80), (x, y, z - 170)))
        parts.append(gemstone(f"{materials[i % 3]}_stone_{i+1:03d}", 125, 144, (x, y, z)))
    return parts


def graft_load(presentation: bool) -> list[Component]:
    parts = [air_world()]
    trays = 8 if presentation else 4
    for tray in range(trays):
        z = -600 + tray * (1500 / max(trays - 1, 1))
        parts.append(box(f"aluminum_shelf_{tray+1:03d}", (1160, 1420, 18), (0, 0, z - 90)))
        for i, (x, y) in enumerate(((-420, -480), (0, -480), (420, -480), (-420, 0), (0, 0), (420, 0), (-420, 480), (0, 480), (420, 480)), 1):
            parts.append(hollow_cylinder(f"polypropylene_vessel_{tray+1:02d}_{i:02d}", 58, 150, (x, y, z), 5))
            parts.append(cylinder(f"agar_medium_{tray+1:02d}_{i:02d}", 49, 45, (x, y, z - 40)))
            parts.append(cylinder(f"plant_graft_{tray+1:02d}_{i:02d}", 12, 80, (x, y, z + 25)))
    return parts


def pupae_load(presentation: bool) -> list[Component]:
    parts = [air_world()]
    shelves = 8 if presentation else 4
    for layer in range(shelves):
        z = -650 + layer * (1400 / max(shelves - 1, 1))
        parts.append(box(f"aluminum_rack_shelf_{layer+1:03d}", (1180, 1450, 20), (0, 0, z - 110)))
        for row in range(4):
            for column in range(3):
                n = layer * 12 + row * 3 + column + 1
                x, y = -380 + column * 380, -540 + row * 360
                parts.append(hollow_box(f"polypropylene_pupae_tray_{n:03d}", (330, 310, 100), (x, y, z), 7))
                parts.append(box(f"pupae_batch_{n:03d}", (290, 270, 65), (x, y, z + 5)))
    return parts


def bagged_load(content: str, presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    count = 30 if presentation else 15
    for i in range(count):
        column, row, layer = i % 3, (i // 3) % 5, i // 15
        x, y, z = -360 + column * 360, -560 + row * 280, -270 + layer * 620
        parts.append(hollow_box(f"jute_bag_{i+1:03d}", (320, 245, 520), (x, y, z), 8))
        parts.append(box(f"{content}_content_{i+1:03d}", (280, 205, 470), (x, y, z)))
    return parts


def boxed_bulk_load(content: str, presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    count = 24 if presentation else 12
    for i in range(count):
        column, row, layer = i % 4, (i // 4) % 3, i // 12
        x, y, z = -450 + column * 300, -470 + row * 470, -250 + layer * 650
        parts.append(hollow_box(f"cardboard_case_{i+1:03d}", (275, 425, 560), (x, y, z), 8))
        parts.append(box(f"{content}_content_{i+1:03d}", (235, 385, 510), (x, y, z)))
        parts.append(box(f"polyethylene_liner_{i+1:03d}", (245, 395, 4), (x, y, z + 258)))
    return parts


def archive_load(presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    count = 36 if presentation else 18
    for i in range(count):
        column, row, layer = i % 3, (i // 3) % 3, i // 9
        x, y, z = -360 + column * 360, -480 + row * 480, -410 + layer * 330
        parts.append(hollow_box(f"cardboard_archive_box_{i+1:03d}", (330, 430, 290), (x, y, z), 8))
        parts.append(box(f"cellulose_documents_{i+1:03d}", (290, 390, 245), (x, y, z)))
    return parts


def textile_load(presentation: bool) -> list[Component]:
    parts = [air_world(), *pallet()]
    count = 32 if presentation else 16
    for i in range(count):
        column, row, layer = i % 4, (i // 4) % 4, i // 16
        x, y, z = -450 + column * 300, -525 + row * 350, -250 + layer * 720
        material = "cotton" if i % 2 == 0 else "polyester"
        parts.append(hollow_box(f"polyethylene_pack_{i+1:03d}", (270, 320, 600), (x, y, z), 6))
        parts.append(box(f"{material}_textile_{i+1:03d}", (235, 285, 550), (x, y, z)))
    return parts


EXAMPLES = (
    Example("01_food_pallet", "Pallet de alimentos frescos", "Frutas empacadas para irradiación fitosanitaria", ("wood", "cardboard", "fruit", "polyethylene"), food_load),
    Example("02_medical_supplies", "Insumos médicos", "Cajas con bolsas de solución y dispositivos poliméricos", ("wood", "cardboard", "saline", "polypropylene", "steel"), medical_load),
    Example("03_gemstones", "Piedras preciosas", "Bandejas heterogéneas para modificación de color", ("wood", "polyethylene", "quartz", "corundum", "topaz"), gemstone_load),
    Example("04_plant_grafts", "Injertos vegetales", "Recipientes de cultivo con medio e injertos", ("polypropylene", "agar", "plant", "aluminum"), graft_load),
    Example("05_sit_pupae", "Pupas para técnica del insecto estéril", "Contenedor modular con bandejas y pupas", ("polypropylene", "pupae", "aluminum"), pupae_load),
    Example("06_spices_coffee", "Especias, cacao y café", "Sacos de producto seco a granel", ("wood", "jute", "spice"), lambda detailed: bagged_load("spice", detailed)),
    Example("07_agricultural_seeds", "Semillas agrícolas", "Cajas con bolsas de semillas", ("wood", "cardboard", "polyethylene", "seed"), lambda detailed: boxed_bulk_load("seed", detailed)),
    Example("08_animal_feed", "Alimento balanceado", "Cajas revestidas con alimento seco", ("wood", "cardboard", "polyethylene", "feed"), lambda detailed: boxed_bulk_load("feed", detailed)),
    Example("09_archives_wood", "Archivos y productos de madera", "Documentos embalados sobre pallet", ("wood", "cardboard", "cellulose"), archive_load),
    Example("10_hospital_textiles", "Textiles hospitalarios", "Paquetes de algodón y poliéster", ("wood", "polyethylene", "cotton", "polyester"), textile_load),
)


def material_document(names: Iterable[str]) -> dict:
    materials = [AIR]
    for name in names:
        source = MATERIALS[name]
        materials.append({"name": name, "elements": source["elements"], "density": source["density"], "fraction_type": "weight"})
    return {"materials": materials}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def export_step(path: Path, components: list[Component], title: str) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString(title))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    for component in components:
        label = shape_tool.AddShape(component.shape.val().wrapped, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(component.name))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.Transfer(document)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise OSError(f"No se pudo exportar {path}")


def export_analysis_stl(
    path: Path,
    components: list[Component],
    exterior_envelope: bool = False,
) -> None:
    load_shapes = [component.shape.val() for component in components if not component.name.startswith("air_")]
    if exterior_envelope:
        boxes = [shape.BoundingBox() for shape in load_shapes]
        low = [min(getattr(bounds, axis + "min") for bounds in boxes) for axis in "xyz"]
        high = [max(getattr(bounds, axis + "max") for bounds in boxes) for axis in "xyz"]
        size = [upper - lower for lower, upper in zip(low, high)]
        center = [(lower + upper) / 2 for lower, upper in zip(low, high)]
        output = cq.Workplane("XY").box(*size).translate(tuple(center)).val()
    else:
        output = cq.Compound.makeCompound(load_shapes)
    cq.exporters.export(output, str(path), exportType="STL", tolerance=0.25, angularTolerance=0.15)


def component_summary(components: list[Component]) -> dict:
    counts: dict[str, int] = {}
    for component in components:
        tag = component.name.split("_", 1)[0]
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def positive_volume_overlaps(components: list[Component]) -> list[tuple[str, str, float]]:
    """Detecta penetraciones CAD que invalidarían una malla tetraédrica."""

    load_components = [
        component for component in components if not component.name.startswith("air_")
    ]
    overlaps: list[tuple[str, str, float]] = []
    for index, left in enumerate(load_components):
        left_shape = left.shape.val()
        left_bounds = left_shape.BoundingBox()
        for right in load_components[index + 1 :]:
            right_shape = right.shape.val()
            right_bounds = right_shape.BoundingBox()
            separated = (
                left_bounds.xmax <= right_bounds.xmin
                or right_bounds.xmax <= left_bounds.xmin
                or left_bounds.ymax <= right_bounds.ymin
                or right_bounds.ymax <= left_bounds.ymin
                or left_bounds.zmax <= right_bounds.zmin
                or right_bounds.zmax <= left_bounds.zmin
            )
            if separated:
                continue
            volume = float(left_shape.intersect(right_shape).Volume())
            if volume > 1.0e-6:
                overlaps.append((left.name, right.name, volume))
    return overlaps


def generate(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    catalogue = []
    for example in EXAMPLES:
        destination = root / example.slug
        destination.mkdir(parents=True, exist_ok=True)
        analysis = example.builder(False)
        presentation = example.builder(True)
        for representation, components in (
            ("analysis", analysis),
            ("presentation", presentation),
        ):
            overlaps = positive_volume_overlaps(components)
            if overlaps:
                names = ", ".join(
                    f"{left}/{right}" for left, right, _volume in overlaps[:8]
                )
                raise ValueError(
                    f"{example.slug} ({representation}) contiene "
                    f"{len(overlaps)} intersecciones de volumen: {names}"
                )
        export_step(destination / "load.step", analysis, example.title + " - análisis")
        export_step(destination / "presentation.step", presentation, example.title + " - presentación")
        export_analysis_stl(
            destination / "world_load.stl",
            analysis,
            exterior_envelope=example.slug == "01_food_pallet",
        )
        write_json(destination / "materials.json", material_document(example.material_names))
        manifest = {
            "schema_version": 1,
            "title": example.title,
            "purpose": example.purpose,
            "coordinate_units": "mm",
            "nominal_footprint_mm": [1200, 1500],
            "analysis_step": "load.step",
            "presentation_step": "presentation.step",
            "deterministic_surface": "world_load.stl",
            "deterministic_surface_representation": (
                "exterior-envelope" if example.slug == "01_food_pallet" else "component-surfaces"
            ),
            "materials_file": "materials.json",
            "analysis_component_counts": component_summary(analysis),
            "presentation_component_counts": component_summary(presentation),
            "geometry_validation": {
                "analysis_positive_volume_intersections": 0,
                "presentation_positive_volume_intersections": 0,
            },
            "operator_sequence": [
                "Seleccionar load.step como geometría de cálculo",
                "Importar materials.json y verificar nombres y densidades",
                "Seleccionar world_load.stl para el método determinista",
                "Usar presentation.step únicamente para presentación visual",
            ],
            "material_values_status": "Valores iniciales de ingeniería; deben validarse contra el lote real antes de uso dosimétrico.",
        }
        write_json(destination / "load_manifest.json", manifest)
        catalogue.append({
            "slug": example.slug,
            "title": example.title,
            "purpose": example.purpose,
            "analysis_components": len(analysis),
            "presentation_components": len(presentation),
            "materials": ["air", *example.material_names],
        })
    write_json(
        root / "catalogue.json",
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "examples": catalogue,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("examples/irradiation_loads"))
    args = parser.parse_args()
    generate(args.output.resolve())


if __name__ == "__main__":
    main()
