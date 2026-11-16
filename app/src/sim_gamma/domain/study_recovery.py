"""Durable continuation of published route positions, never of an incomplete map."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

INPUT_FILES = ("load.step", "materials.json", "simulation_parameters.json", "sources.json", "route.json")
RECOVERABLE_STATES = {"paused", "disk-limited", "memory-limited", "time-limited", "interrupted", "failed", "cancelled"}


def sync_directories(directory: Path, through: Path) -> None:
    """Persist directory entries from a checkpoint back to its case root."""
    for parent in (directory, *directory.parents):
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if parent == through:
            break


def write_checkpoint_json(path: Path, data: dict, *, through: Path | None = None) -> None:
    """Replace a checkpoint only after the new contents are on disk."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directories(path.parent, through or path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_checkpoint_file(path: Path, root: Path) -> None:
    with path.open("rb") as stream:
        if not os.fstat(stream.fileno()).st_size:
            raise OSError(f"El archivo de recuperación está vacío: {path.name}")
        os.fsync(stream.fileno())
    sync_directories(path.parent, root)


def publish_route_checkpoint(root: Path, result: dict, *, position_dir: Path | None = None,
                             restart_statepoint: Path | None = None) -> None:
    """Commit artifacts before the run archive and its latest-study mirror."""
    if position_dir is not None:
        for name in ("result.json", "dose_distribution.vti"):
            _sync_checkpoint_file(position_dir / name, root)
    if restart_statepoint is not None:
        _sync_checkpoint_file(restart_statepoint, root)
        _sync_checkpoint_file(restart_statepoint.with_name("model.xml"), root)
    interval_map = (result.get("interval_precision") or {}).get("map")
    if interval_map:
        _sync_checkpoint_file(root / interval_map, root)
    results = root / "results"
    archive = results / "studies" / f"{result['run_id']}.json"
    write_checkpoint_json(archive, result, through=root)
    write_checkpoint_json(results / "route_rates.json", result)


def input_fingerprints(root: Path) -> dict:
    fingerprints = {}
    for name in INPUT_FILES:
        path = root / "input" / name
        if path.is_file():
            with path.open("rb") as stream:
                fingerprints[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return fingerprints


def pause_requested(run_root: Path, run_id: str) -> bool:
    try:
        return json.loads((run_root / "pause.json").read_text()).get("run_id") == run_id
    except (OSError, ValueError):
        return False


def position_output_name(index: int, route: dict) -> str:
    refinement = f"-r{route['refinement_pass']}" if route.get("refinement_pass") else ""
    continuation = f"-c{route['continuation']}" if route.get("continuation") else ""
    return f"{index + 1:03d}{refinement}{continuation}"


def recover_study(root: Path, request: dict) -> dict:
    """Validate the saved prefix against the original request before reusing it."""
    study = request.get("route_study")
    if not study:
        raise ValueError("La continuación está disponible para estudios de posiciones.")
    if request.get("input_fingerprints") and input_fingerprints(root) != request["input_fingerprints"]:
        raise ValueError("Las entradas del caso cambiaron. Crea un estudio nuevo para no mezclar configuraciones.")
    route = json.loads((root / "input/route.json").read_text())
    from sim_gamma.domain.route_study import selected_position_indices
    indices = selected_position_indices(route["positions"], study)
    positions = [route["positions"][index] for index in indices]
    archive = root / "results/studies" / f"{request['run_id']}.json"
    if not archive.is_file():
        return {}
    result = json.loads(archive.read_text())
    if result.get("case_id") != request["case_id"] or result.get("run_id") != request["run_id"]:
        raise ValueError("El estudio guardado no corresponde a esta ejecución.")
    if result.get("score_interval") != [study["start"], study["end"]]:
        raise ValueError("El intervalo guardado no coincide con la solicitud original.")
    if study.get("position_names") is not None and result.get("selected_position_names") != [point["name"] for point in positions]:
        raise ValueError("La selección guardada no coincide con la solicitud original.")
    expected = {
        "activity_date": study["irradiation_date"], "dose_tally": route.get("dose_tally", "heating"),
        "cell_size_cm": study.get("cell_size_cm") or route.get("cell_size_cm"),
        "occupancy": study.get("occupancy") or route.get("occupancy", "isolated"),
        "variance_reduction": study.get("variance_reduction") or route.get("variance_reduction", "analog"),
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError(f"La configuración guardada cambió ({key}). No se combinarán estudios diferentes.")
    if result.get("transport_geometry_revision") is not None:
        from sim_gamma.simulators.openmc.geometry_contract import TRANSPORT_GEOMETRY_REVISION
        if result["transport_geometry_revision"] != TRANSPORT_GEOMETRY_REVISION:
            raise ValueError("La versión de geometría cambió. Se requiere un nuevo estudio.")
    saved = result.get("positions", [])
    if len(saved) > len(positions):
        raise ValueError("El estudio contiene posiciones fuera del intervalo solicitado.")
    for actual, expected_position in zip(saved, positions):
        expected_yaw = expected_position["yaw_degrees"] + route.get("cad_offset_degrees", 0)
        if any(actual.get(key) != expected_position.get(key) for key in ("name", "center_mm")) or actual.get("yaw_degrees") != expected_yaw:
            raise ValueError("Las posiciones o sus giros cambiaron. Se requiere un estudio nuevo.")
        path = (root / actual["summary"]).resolve()
        if not path.is_relative_to((root / "monte_carlo/positions" / request["run_id"]).resolve()):
            raise ValueError("La ruta del mapa guardado no pertenece al estudio.")
        if not path.is_file() or not (path.parent / "dose_distribution.vti").is_file():
            raise ValueError(f"Falta un archivo publicado de {actual['name']}. Restaura el archivo antes de continuar.")
        summary = json.loads(path.read_text())
        if summary.get("run_id") != request["run_id"]:
            raise ValueError("El resumen de una posición pertenece a otra ejecución.")
    return result
