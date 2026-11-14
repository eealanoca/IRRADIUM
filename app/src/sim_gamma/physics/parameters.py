import json
import math
from pathlib import Path
from typing import Tuple


def load_physical_parameters(
    radionuclide: str,
    material_name: str,
    source_file: Path,
    materials_file: Path,
) -> Tuple[float, float]:
    """Obtiene la constante de decaimiento y el factor dosimétrico configurados."""
    with source_file.open("r", encoding="utf-8") as stream:
        source_data = json.load(stream)
    nuclide = source_data["radionuclides"].get(radionuclide)
    if nuclide is None:
        raise ValueError("Radionúclido no configurado: %s" % radionuclide)

    with materials_file.open("r", encoding="utf-8") as stream:
        materials = json.load(stream)["materials"]
    material = next((item for item in materials if item["name"] == material_name), None)
    if material is None:
        raise ValueError("Material no configurado: %s" % material_name)

    dose_factor = 0.0
    for emission in nuclide["emissions"]:
        energy = float(emission["value"])
        probability = float(emission.get("probability", 1.0))
        coefficient = material["mu_en_over_rho"]["%.3f" % energy]
        dose_factor += probability * coefficient * energy * 3.6 * 1.6022e-10
    return math.log(2.0) / float(nuclide["half_life"]), dose_factor


def load_photon_yield(radionuclide: str, source_file: Path) -> float:
    """Obtiene el número esperado de fotones gamma emitidos por desintegración."""

    with source_file.open("r", encoding="utf-8") as stream:
        source_data = json.load(stream)
    nuclide = source_data["radionuclides"].get(radionuclide)
    if nuclide is None:
        raise ValueError("Radionúclido no configurado: %s" % radionuclide)
    photon_yield = sum(
        float(emission.get("probability", 0.0))
        for emission in nuclide.get("emissions", [])
        if emission.get("type", "gamma") == "gamma"
    )
    if not math.isfinite(photon_yield) or photon_yield <= 0:
        raise ValueError("El radionúclido no contiene emisiones gamma válidas")
    return photon_yield
