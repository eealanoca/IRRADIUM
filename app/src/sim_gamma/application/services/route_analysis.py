"""The full results catalogue for route positions and accumulated maps."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import fcntl
from functools import lru_cache


def position_analysis(root: Path, position: dict) -> tuple[dict, Path]:
    path = root / position["summary"]
    summary = json.loads(path.read_text(encoding="utf-8"))
    scored = summary.get("visualization") or summary
    return {
        "state": "completed", "progress": 100,
        "artifact_revision": f"{summary.get('run_id', '')}:{path.parent.name}",
        "summary": {**summary, **scored, "statistical_evaluation": scored},
        "artifacts": {
            "vtk": (path.parent / "dose_distribution.vti").is_file(), "vtk_format": "vti",
            "cut_xy": (path.parent / "isodose_xy.png").is_file(),
            "cut_yz": (path.parent / "isodose_yz.png").is_file(),
        },
    }, path.parent


@lru_cache(maxsize=64)
def map_error_statistics(path: Path, modified_ns: int) -> dict:
    """Cache only summary numbers, with the map revision as part of the key."""
    import numpy as np
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy
    from sim_gamma.simulators.openmc.structured_postprocess import summarize_relative_error

    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(str(path))
    reader.Update()
    cells = reader.GetOutput().GetCellData()
    relative = cells.GetArray("relative_error")
    if relative is None:
        return summarize_relative_error(np.empty(0))
    ghosts = cells.GetArray("vtkGhostType")
    active = None if ghosts is None else (vtk_to_numpy(ghosts) & 32) == 0
    return summarize_relative_error(vtk_to_numpy(relative), active)


def accumulated_analysis(path: Path) -> tuple[dict, Path]:
    import numpy as np
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy
    from sim_gamma.simulators.openmc.structured_postprocess import (
        RegularMeshSpec, summarize_dose, summarize_relative_error, write_isodose_cuts,
    )

    output = path.with_suffix("")
    output.mkdir(exist_ok=True)
    metadata = output / "analysis.json"
    with (output / "analysis.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if metadata.is_file():
            summary = json.loads(metadata.read_text(encoding="utf-8"))
            if "relative_error_p95" not in summary or "relative_error_p98" not in summary:
                summary.update(map_error_statistics(path, path.stat().st_mtime_ns))
                with TemporaryDirectory(dir=output) as directory:
                    staged = Path(directory) / metadata.name
                    staged.write_text(json.dumps(summary, allow_nan=False), encoding="utf-8")
                    staged.replace(metadata)
        else:
            reader = vtk.vtkXMLImageDataReader()
            reader.SetFileName(str(path))
            reader.Update()
            image = reader.GetOutput()
            cells = image.GetCellData()
            values = vtk_to_numpy(cells.GetScalars())
            ghosts = cells.GetArray("vtkGhostType")
            inside = np.ones(values.shape, dtype=bool) if ghosts is None else (vtk_to_numpy(ghosts) & 32) == 0
            dimensions = tuple(value - 1 for value in image.GetDimensions())
            origin, spacing = image.GetOrigin(), image.GetSpacing()
            high = tuple(low + count * step for low, count, step in zip(origin, dimensions, spacing, strict=True))
            mesh = RegularMeshSpec(dimensions, origin, high)
            summary = summarize_dose(values, mesh, inside)
            relative_error = cells.GetArray("relative_error")
            if relative_error is not None:
                summary.update(summarize_relative_error(vtk_to_numpy(relative_error), inside))
            summary["cell_size_cm"] = float(spacing[0])
            label = "Suma de tasas [kGy/h]" if cells.GetScalars().GetName() == "summed_rate_kgy_h" else "Dosis absorbida [kGy]"
            with TemporaryDirectory(dir=output) as directory:
                staging = Path(directory)
                write_isodose_cuts(values, mesh, staging, inside, quantity_label=label)
                for file in staging.glob("*.png"):
                    file.replace(output / file.name)
                staged = staging / metadata.name
                staged.write_text(json.dumps(summary, allow_nan=False), encoding="utf-8")
                staged.replace(metadata)
    summary = json.loads(metadata.read_text(encoding="utf-8"))
    scenario_path = path.with_suffix(".json")
    if scenario_path.is_file():
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        positions = scenario.get("positions", [])
        times = scenario.get("times_minutes") or [1] * len(positions)
        records = []
        case_root = path.parents[2]
        for position, minutes in zip(positions, times, strict=True):
            source = (case_root / position["summary"]).resolve()
            if minutes > 0 and source.is_relative_to(case_root.resolve()) and source.is_file():
                records.append(json.loads(source.read_text(encoding="utf-8")))
        scored_records = [row for row in records if row.get("simulated_particles") is not None]
        if scored_records:
            batches = [row["actual_batches"] for row in scored_records if row.get("actual_batches") is not None]
            methods = {row.get("variance_reduction", {}).get("method", "analog") for row in scored_records}
            summary.update(
                aggregated_maps=True, contributing_positions=len(scored_records),
                simulated_particles=sum(row["simulated_particles"] for row in scored_records),
                transport_wall_seconds=sum(row.get("transport_wall_seconds", 0) for row in scored_records),
                batches_per_position_min=min(batches) if batches else None,
                batches_per_position_max=max(batches) if batches else None,
                variance_reduction={"method": next(iter(methods)) if len(methods) == 1 else "mixed-position-methods"},
            )
    return {
        "state": "completed", "progress": 100, "artifact_revision": path.stem,
        "summary": summary,
        "artifacts": {"vtk": True, "vtk_format": "vti", "cut_xy": True, "cut_yz": True},
    }, output
