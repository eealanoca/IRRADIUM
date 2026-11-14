"""Conversor STEP -> DAGMC ejecutado en un entorno CAD aislado."""

import json
import sys
from pathlib import Path


def convert(directory: Path, file_name: str, material_tags=None, implicit_air=False) -> Path:
    directory = directory.resolve()
    h5m_path = directory / f"{file_name}.h5m"
    step_path = next(
        (
            candidate
            for candidate in (
                directory / f"{file_name}.step",
                directory / f"{file_name}.STEP",
            )
            if candidate.is_file()
        ),
        None,
    )
    if step_path is None:
        raise FileNotFoundError(
            f"No se encontró {file_name}.step ni {file_name}.STEP en {directory}"
        )

    import CAD_to_OpenMC.assembly as assembly

    world = assembly.Assembly([str(step_path)], implicit_complement="air" if implicit_air else None)
    options = {}
    if material_tags:
        options["sequential_tags"] = [str(tag).strip().lower() for tag in material_tags]
    world.run(
        backend="stl2",
        merge=True,
        h5m_filename=str(h5m_path),
        **options,
    )
    if not h5m_path.is_file():
        raise FileNotFoundError("CAD_to_OpenMC finalizó sin generar el archivo H5M")
    return h5m_path


def main() -> int:
    if len(sys.argv) not in {3, 4, 5}:
        print(
            "Uso: cad_converter_cli.py DIRECTORIO NOMBRE_BASE [MATERIALES_JSON] [air]",
            file=sys.stderr,
        )
        return 2
    try:
        material_tags = json.loads(sys.argv[3]) if len(sys.argv) >= 4 else None
        if material_tags is not None and not isinstance(material_tags, list):
            raise ValueError("MATERIALES_JSON debe ser una lista")
        output = convert(Path(sys.argv[1]), sys.argv[2], material_tags, implicit_air=sys.argv[4:] == ["air"])
    except Exception as error:
        print(f"Conversión STEP→DAGMC fallida: {error}", file=sys.stderr)
        return 1
    print(f"DAGMC generado: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
