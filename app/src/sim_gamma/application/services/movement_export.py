import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


REQUIRED_KEYS = (
    "step_angle_degrees_best_real",
    "step_length",
    "work_section",
    "work_section_position",
    "average_dose",
    "description",
)


def build_movement_document(
    angles: Sequence[float],
    length_points: Sequence[float],
    best_length_points: Iterable[float],
    work_section: str,
    work_section_position: Sequence[float],
    average_dose: float,
    description: str,
) -> Dict[str, Any]:
    best = set(best_length_points)
    step_length = {}
    for index, point in enumerate(length_points, start=1):
        suffix = "_best" if point in best else ""
        step_length["length_complete_%d%s" % (index, suffix)] = point

    return {
        "step_angle_degrees_best_real": list(angles),
        "step_length": step_length,
        "work_section": work_section,
        "work_section_position": list(work_section_position),
        "average_dose": float(average_dose),
        "description": description,
    }


def validate_movement_document(document: Dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in document]
    extra = [key for key in document if key not in REQUIRED_KEYS]
    if missing or extra:
        raise ValueError("Esquema inválido; faltan=%s, sobran=%s" % (missing, extra))
    if not document["step_angle_degrees_best_real"]:
        raise ValueError("La trayectoria debe contener al menos un ángulo")
    if not document["step_length"]:
        raise ValueError("La trayectoria debe contener al menos una posición lineal")


def write_movement_document(path: Path, document: Dict[str, Any]) -> Path:
    validate_movement_document(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=4, ensure_ascii=False)
    temporary.replace(path)
    return path


def build_from_deterministic_case(case_root: Path) -> Dict[str, Any]:
    """Construye el contrato externo a partir de un caso determinista terminado."""
    with (case_root / "manifest.json").open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    with (case_root / "deterministic" / "request.json").open(
        "r", encoding="utf-8"
    ) as stream:
        request = json.load(stream)
    with (case_root / "deterministic" / "result.json").open(
        "r", encoding="utf-8"
    ) as stream:
        result = json.load(stream)
    with (case_root / "input" / "work_sections.json").open(
        "r", encoding="utf-8"
    ) as stream:
        positions = json.load(stream)["positions"]

    work_section = request["parameters"]["work_section"]
    if work_section not in positions:
        raise ValueError("La sección de trabajo del caso no existe en su instantánea")
    average_dose = result.get(
        "average_dose", result.get("average_theoretical_dose")
    )
    if average_dose is None:
        raise ValueError("El resultado determinista no contiene una dosis promedio")
    return build_movement_document(
        angles=result["step_angle_degrees_best_real"],
        length_points=result["length_points_mm"],
        best_length_points=result["best_length_points_mm"],
        work_section=work_section,
        work_section_position=positions[work_section],
        average_dose=average_dose,
        description=manifest["description"],
    )
