"""Version and verify the physical contract of a generated transport geometry."""
import hashlib
import json
from pathlib import Path

TRANSPORT_GEOMETRY_REVISION = 2


def geometry_signature(step: Path, material_tags, implicit_air: bool) -> dict:
    with step.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "revision": TRANSPORT_GEOMETRY_REVISION,
        "step_sha256": digest,
        "material_tags": list(material_tags or []),
        "implicit_air": bool(implicit_air),
    }


def require_air_complement(path: Path):
    import h5py
    with h5py.File(path, "r") as mesh:
        labels = {tag.tobytes().decode().replace("\x00", "")
                  for tag in mesh["/tstt/tags/NAME/values"]}
    if "mat:air_comp" not in labels:
        raise ValueError("La geometría solicitó aire exterior pero el H5M no contiene mat:air_comp. No se simulará con vacío implícito.")


def cached_geometry_matches(path: Path, signature: dict) -> bool:
    record = path.with_suffix(".geometry.json")
    if not path.is_file() or not record.is_file():
        return False
    try:
        if json.loads(record.read_text()) != signature:
            return False
        if signature["implicit_air"]:
            require_air_complement(path)
        return True
    except (OSError, KeyError, ValueError):
        return False


def occupied_geometry_key(step: Path, route: dict) -> str:
    """Identify the canonical scene; targets apply their own rigid transforms."""
    data = {
        'input': geometry_signature(step, [], True),
        'positions': route['positions'],
        'position_reference': route.get('position_reference', 'center'),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
