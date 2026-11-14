"""A single CAD universe for repeatedly scattering homogeneous loads."""
from pathlib import Path
from tempfile import TemporaryDirectory

from sim_gamma.domain.route_geometry import occupied_placements


def write_occupied_step(source: Path, destination: Path, route: dict, bounds, material: str) -> int:
    import cadquery as cq

    solids = cq.importers.importStep(str(source)).solids().vals()
    placements = occupied_placements(route, route["positions"][0], bounds)
    if not destination.is_file():
        assembly = cq.Assembly(name=f"{material}_rack")
        index = 0
        for placement in placements:
            location = cq.Location(cq.Vector(*(placement["translation"] * 10)), cq.Vector(0, 0, 1),
                                   placement["angle"])
            for solid in solids:
                index += 1
                assembly.add(solid, name=f"{material}_{index:04d}", loc=location)
        with TemporaryDirectory(dir=destination.parent) as directory:
            staged = Path(directory) / destination.name
            assembly.export(str(staged))
            staged.replace(destination)
    return len(solids) * len(placements)
