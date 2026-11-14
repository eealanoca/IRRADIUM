"""Continue an interval's fixed-source batches only for an identical model."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def configure_restart(settings, previous_summary: Path, desired_histories: int):
    """Keep the original histories per batch so OpenMC can continue its RNG stream."""
    import h5py

    summary = json.loads(previous_summary.read_text())
    # Read the checkpoint rather than infer batch sizes from rounded budgets.
    statepoint = previous_summary.parents[4] / summary["statepoint"]
    with h5py.File(statepoint, "r") as file:
        count = int(file["n_particles"][()])
        batch = int(file["current_batch"][()])
    batches = int(desired_histories) // count
    if batches <= batch:
        return None
    settings.particles = count
    settings.batches = batches
    return {"statepoint": statepoint, "model": statepoint.with_name("model.xml"),
            "histories": count * batch,
            "transport_seconds": float(summary.get("transport_wall_seconds", 0))}


def _signature(path):
    model = ET.parse(path).getroot()
    settings = model.find("settings")
    for tag in ("batches", "state_point"):
        for element in settings.findall(tag):
            settings.remove(element)
    # Serialization is canonicalized to ignore XML indentation/attribute order.
    return ET.canonicalize(ET.tostring(model, encoding="unicode"), strip_text=True)


def compatible_restart(candidate, model: Path):
    if candidate is not None and _signature(candidate["model"]) == _signature(model):
        return candidate
    # A model difference always falls back to a fresh, fully sampled run.
    return None
