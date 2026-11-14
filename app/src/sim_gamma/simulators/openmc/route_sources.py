"""Cylindrical source distributions in the local frame of a suspended load."""
from datetime import date
import math

import numpy as np
import openmc

from sim_gamma.domain.route_dose import SOURCE_DIAMETER_MM, source_endpoints
from sim_gamma.domain.route_geometry import to_load_frame as world_to_load


def sources_at_position(configuration, radionuclide, reference_date, pose, load_center_cm, height_cm=0, reference="center"):
    nuclide = configuration["radionuclides"][radionuclide]
    emissions = [item for item in nuclide["emissions"] if item.get("type", "gamma") == "gamma"]
    energies = [item["value"] * 1e6 for item in emissions]
    probabilities = [item["probability"] for item in emissions]
    energy = openmc.stats.Discrete(energies, np.asarray(probabilities) / sum(probabilities))
    decay = math.log(2) / nuclide["half_life"]
    def to_load_frame(world_mm):
        return world_to_load(np.asarray(world_mm) / 10, pose, load_center_cm, height_cm, reference)

    sources = []
    bounds = []
    radius = SOURCE_DIAMETER_MM / 20
    for record in configuration["sources"]:
        lower, upper = map(to_load_frame, source_endpoints(record))
        activity = float(record["intensity"]) * math.exp(
            -decay * max(0, (reference_date - date.fromisoformat(record["initial_reading_date"])).days)
        )
        space = openmc.stats.CylindricalIndependent(
            r=openmc.stats.PowerLaw(0, radius, 1),
            phi=openmc.stats.Uniform(0, 2 * math.pi),
            z=openmc.stats.Uniform(0, upper[2] - lower[2]),
            origin=lower,
        )
        sources.append(openmc.IndependentSource(
            space=space, angle=openmc.stats.Isotropic(), energy=energy, strength=activity, particle="photon",
        ))
        bounds.extend((lower - [radius, radius, 0], upper + [radius, radius, 0]))
    if not sources:
        raise ValueError("El caso no contiene fuentes activas")
    return sources, np.array([np.min(bounds, axis=0), np.max(bounds, axis=0)])
