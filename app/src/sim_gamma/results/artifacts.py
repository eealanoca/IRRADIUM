"""Contrato de artefactos compartido por todos los motores de cálculo."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.tri import Triangulation
from scipy.spatial import Delaunay


MAX_CUT_POINTS = 50000


def _even_sample(points, values, maximum):
    if len(points) <= maximum:
        return points, values
    indices = np.linspace(0, len(points) - 1, maximum, dtype=np.int64)
    return points[indices], values[indices]


def _write_vtk(points, values, destination):
    """Escribe una malla volumétrica VTK legacy ASCII con valores nodales."""
    triangulation = Delaunay(points)
    with Path(destination).open("w", encoding="utf-8") as stream:
        stream.write("# vtk DataFile Version 2.0\n")
        stream.write("IRRADIUM absorbed dose distribution\n")
        stream.write("ASCII\n\nDATASET UNSTRUCTURED_GRID\n")
        stream.write("POINTS %d float\n" % len(points))
        np.savetxt(stream, points, fmt="%.6f")
        cells = triangulation.simplices
        stream.write("\nCELLS %d %d\n" % (len(cells), len(cells) * 5))
        np.savetxt(
            stream,
            np.hstack((4 * np.ones((len(cells), 1)), cells)).astype(int),
            fmt="%d",
        )
        stream.write("\nCELL_TYPES %d\n" % len(cells))
        np.savetxt(stream, 10 * np.ones(len(cells), dtype=int), fmt="%d")
        stream.write("\nPOINT_DATA %d\n" % len(points))
        stream.write("SCALARS absorbed_dose_rate float 1\nLOOKUP_TABLE default\n")
        np.savetxt(stream, values, fmt="%.9e")


def _write_cuts(points, values, output_root, title):
    points, values = _even_sample(points, values, MAX_CUT_POINTS)
    minimum = float(values.min())
    maximum = float(values.max())
    ratio = maximum / minimum if minimum > 0 else float("inf")
    if np.isclose(minimum, maximum):
        levels = np.linspace(minimum * 0.99, maximum * 1.01 + 1e-12, 10)
    else:
        levels = np.linspace(minimum, maximum, 12)

    cuts = {}
    for plane, first_axis, second_axis, labels in (
        ("xy", 0, 1, ("X", "Y")),
        ("yz", 1, 2, ("Y", "Z")),
    ):
        figure, axis = plt.subplots(figsize=(9, 6), constrained_layout=True)
        try:
            mesh = Triangulation(points[:, first_axis], points[:, second_axis])
            plot = axis.tricontourf(mesh, values, levels=levels, cmap="viridis")
        except (RuntimeError, ValueError):
            plot = axis.scatter(
                points[:, first_axis],
                points[:, second_axis],
                c=values,
                s=9,
                cmap="viridis",
                vmin=minimum,
                vmax=maximum,
            )
        figure.colorbar(plot, ax=axis, label="Tasa de dosis absorbida [kGy/h]")
        axis.set_title("%s · corte %s" % (title, plane.upper()))
        axis.set_xlabel("%s [cm]" % labels[0])
        axis.set_ylabel("%s [cm]" % labels[1])
        axis.set_aspect("equal", adjustable="box")
        axis.grid(True, linestyle="--", alpha=0.25)
        axis.text(
            0.02,
            0.98,
            "DUR: %.3f\nMáx: %.3e\nMín: %.3e" % (ratio, maximum, minimum),
            transform=axis.transAxes,
            va="top",
            fontsize=9,
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.82},
        )
        destination = Path(output_root) / ("isodose_%s.png" % plane)
        figure.savefig(destination, dpi=170)
        plt.close(figure)
        cuts[plane] = destination.name
    return {
        "cuts": cuts,
        "minimum_dose": minimum,
        "maximum_dose": maximum,
        "dose_uniformity_ratio": ratio,
    }


def write_dose_artifacts(points, dose_values, output_root, title):
    """Genera el VTK y los cortes XY/YZ sin depender del motor de origen."""
    points = np.asarray(points, dtype=np.float32)
    values = np.asarray(dose_values, dtype=np.float32).reshape(-1)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) != len(values):
        raise ValueError("Los puntos y valores de dosis no tienen dimensiones compatibles")
    if len(points) < 4:
        raise ValueError("Se requieren al menos cuatro puntos para generar el volumen VTK")

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    full_data_path = root / "dose_distribution_full.npz"
    np.savez_compressed(full_data_path, points=points, absorbed_dose_rate=values)

    vtk_path = root / "dose_distribution.vtk"
    _write_vtk(points, values, vtk_path)
    return {
        "vtk_file": vtk_path.name,
        "full_data_file": full_data_path.name,
        "total_points": len(points),
        "vtk_points": len(points),
        "visualization": _write_cuts(points, values, root, title),
    }
