"""Editable solids for the suspended-litter example cases."""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class Example:
    name: str
    method: str
    description: str
    materials: tuple[str, ...]


EXAMPLES = {
    "airload": Example("Aire seco", "flux", "Bloque de referencia para comparar tasas por conversión de flujo.", ("airload",)),
    "eps": Example("Piezas de poliestireno expandido", "heating", "Seis piezas con chaflanes y canales de aire.", ("eps",)),
    "water": Example("Depósitos de agua apilados", "heating", "Seis depósitos con esquinas achaflanadas y separación entre ellos.", ("water",)),
    "citrus": Example("Mandarinas en bandejas", "heating", "24 frutas, cuatro bandejas y cuatro soportes. Dos materiales editables.", ("citrus", "carton")),
}


def _build_assembly(example_id: str, dimensions: tuple[float, float, float]):
    from cadquery import Assembly, Matrix, Workplane

    x, y, z = dimensions
    assembly = Assembly(name=f"{example_id}_000")

    def box(sx, sy, sz):
        return Workplane("XY").box(sx, sy, sz, centered=(True, True, False))

    if example_id == "airload":
        return assembly.add(box(x, y, z), name="airload_001")

    if example_id in {"eps", "water"}:
        width, height = .47 * x, .30 * z
        for tier in range(3):
            for side, sign in enumerate((-1, 1), start=1):
                body = box(width, y, height)
                if example_id == "eps":
                    body = body.edges("|Z").chamfer(.12 * min(width, y))
                    groove = box(.22 * width, .35 * y, 1.2 * height).translate((0, .44 * y, -.1 * height))
                    body = body.cut(groove)
                else:
                    body = body.edges("|Z").chamfer(.20 * min(width, y))
                assembly.add(body.translate((sign * (x - width) / 2, 0, tier * (z - height) / 2)),
                             name=f"{example_id}_{2 * tier + side:03d}")
        return assembly

    for tier in range(4):
        base_z = .235 * tier * z
        tray = box(.86 * x, .88 * y, .047 * z)
        tray = tray.cut(box(.79 * x, .81 * y, .048 * z).translate((0, 0, .016 * z)))
        assembly.add(tray.translate((0, 0, base_z)), name=f"carton_tray{tier + 1:02d}")
        for row, center_x in enumerate((-.20 * x, .20 * x)):
            for column, center_y in enumerate((-.26 * y, 0, .26 * y)):
                scale = (1, .91, 1.06)[(tier + row + column) % 3]
                rx, ry, rz = .138 * x * scale, .103 * y * scale, .050 * z * scale
                ellipsoid = Workplane("XY").sphere(1).val().transformGeometry(Matrix([
                    [rx, 0, 0, 0], [0, ry, 0, 0], [0, 0, rz, 0],
                ]))
                assembly.add(ellipsoid.translate((center_x, center_y, base_z + .025 * z + rz)),
                             name=f"citrus_{6 * tier + 3 * row + column + 1:03d}")
    for row, sign_x in enumerate((-1, 1)):
        for column, sign_y in enumerate((-1, 1)):
            post = box(.048 * x, .048 * y, z).translate((sign_x * .476 * x, sign_y * .476 * y, 0))
            assembly.add(post, name=f"carton_post{2 * row + column + 1:02d}")
    return assembly


def example_step(cache_root: Path, example_id: str, dimensions: tuple[float, float, float]) -> Path:
    if example_id not in EXAMPLES or any(not 1 <= value <= 5000 for value in dimensions):
        raise ValueError("Selecciona una carga de ejemplo y dimensiones entre 1 y 5000 mm")
    size = "-".join(format(value, ".12g") for value in dimensions)
    cache_root.mkdir(parents=True, exist_ok=True)
    output = cache_root / f"{example_id}-{size}.step"
    if not output.is_file():
        with TemporaryDirectory(dir=cache_root) as temporary:
            staged = Path(temporary) / output.name
            _build_assembly(example_id, dimensions).export(str(staged))
            staged.replace(output)
    return output
