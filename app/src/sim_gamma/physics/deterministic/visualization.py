import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import art3d
from stl import mesh

def plot_combined_solid_and_points(geometric_center, points_inside, vertices, stl_file):
    """
    Función para graficar el sólido, los puntos generados en los planos XY, YZ y en 3D y un archivo STL.

    Parámetros:
    vertices (numpy.ndarray): Coordenadas de los vértices del sólido.
    geometric_center (numpy.ndarray): Coordenadas del centro geométrico.
    points_inside (numpy.ndarray): Coordenadas de los puntos dentro del sólido.
    stl_file (str): Ruta al archivo STL que se desea graficar.
    """
    fig = plt.figure(figsize=(15, 10))

    # Gráfico XY
    ax1 = fig.add_subplot(221)
    ax1.scatter(vertices[:, 0], vertices[:, 1], color='blue', s=1, label='Vértices del sólido')
    ax1.scatter(geometric_center[0], geometric_center[1], color='red', s=200, marker='*', label='Centro geométrico')
    ax1.scatter(points_inside[:, 0], points_inside[:, 1], color='green', s=5, label='Puntos dentro del sólido')
    ax1.text(geometric_center[0], geometric_center[1], f'({geometric_center[0]}, {geometric_center[1]})', fontsize=11, color='red', ha='right')
    ax1.set_xlabel('X')
    ax1.set_ylabel('Y')
    ax1.legend(loc = 'upper right')
    ax1.set_title("Plano XY")
    ax1.set_aspect('equal')

    # Gráfico YZ
    ax2 = fig.add_subplot(222)
    ax2.scatter(vertices[:, 1], vertices[:, 2], color='blue', s=1, label='Vértices del sólido')
    ax2.scatter(geometric_center[1], geometric_center[2], color='red', s=200, marker='*', label='Centro geométrico')
    ax2.scatter(points_inside[:, 1], points_inside[:, 2], color='green', s=5, label='Puntos dentro del sólido')
    ax2.text(geometric_center[1], geometric_center[2], f'({geometric_center[1]}, {geometric_center[2]})', fontsize=11, color='red', ha='right')
    ax2.set_xlabel('Y')
    ax2.set_ylabel('Z')
    ax2.legend(loc = 'upper right')
    ax2.set_title("Plano YZ")
    ax2.set_aspect('equal')

    # Gráfico 3D de los puntos
    ax3 = fig.add_subplot(223, projection='3d')
    ax3.scatter(vertices[:, 0], vertices[:, 1], vertices[:, 2], color='blue', s=1, label='Vértices del sólido')
    ax3.scatter(geometric_center[0], geometric_center[1], geometric_center[2], color='red', s=200, marker='*', label='Centro geométrico')
    ax3.scatter(points_inside[:, 0], points_inside[:, 1], points_inside[:, 2], color='green', s=5, label='Puntos dentro del sólido')
    ax3.text(geometric_center[0], geometric_center[1], geometric_center[2], f'({geometric_center[0]}, {geometric_center[1]}, {geometric_center[2]})', fontsize=11, color='red', ha='right')
    ax3.set_xlabel('X')
    ax3.set_ylabel('Y')
    ax3.set_zlabel('Z')
    ax3.legend(loc = 'upper right')
    ax3.set_title("Gráfico 3D")
    ax3.set_aspect('equal')

    # Gráfico 3D del STL
    ax4 = fig.add_subplot(224, projection='3d')
    try:
        stl_mesh = mesh.Mesh.from_file(stl_file)
        for vector in stl_mesh.vectors:
            poly = art3d.Poly3DCollection([vector], alpha=0.3, edgecolor='gray', facecolor='silver')
            ax4.add_collection3d(poly)
        scale = np.concatenate([stl_mesh.points.flatten()]).reshape(-1, 3)
        ax4.auto_scale_xyz(scale[:, 0], scale[:, 1], scale[:, 2])
        ax4.set_title("Sólido STL")
        ax4.set_aspect('equal')
    except Exception as e:
        ax4.text(0.5, 0.5, 0.5, "Error al cargar STL", color='red')
        print(f"Error al cargar el archivo STL: {e}")

    plt.tight_layout()
    # plt.show()
    return fig


# import numpy as np
# import matplotlib.pyplot as plt
# from matplotlib.tri import Triangulation

# def plot_isodose_map(points_inside_moved, fluxes, flux_ratio, points_view = False,title="Mapa de Isodosis"):
#     """
#     Genera un mapa de isodosis logarítmico usando los puntos y flujos calculados en las vistas XY y YZ.

#     Parámetros:
#     - points_inside_moved: Arreglo de puntos (n x 3) representando las coordenadas x, y, z.
#     - fluxes: Arreglo columna (n x 1) representando los flujos en cada punto.
#     - flux_ratio: Valor numérico que representa la proporción de flujo que se desea mostrar en el gráfico.
#     - points_view: Booleano que indica si se deben mostrar los puntos en el gráfico.
#     - title: Título del gráfico.
#     """
#     # Extraer las coordenadas x, y, z de los puntos
#     x = points_inside_moved[:, 0]
#     y = points_inside_moved[:, 1]
#     z = points_inside_moved[:, 2]

#     # Convertir flujos a escala logarítmica
#     fluxes_log = (fluxes.flatten())  # Evitar log(0)

#     # Crear las triangulaciones para graficar
#     triang_xy = Triangulation(x, y)
#     triang_yz = Triangulation(y, z)

#     # Crear la figura y los subgráficos
#     fig, axes = plt.subplots(2, 1, figsize=(10, 15), constrained_layout=True)

#     # Vista XY
#     ax_xy = axes[0]
#     cont_xy = ax_xy.tricontourf(triang_xy, fluxes_log, levels=10, cmap="viridis")
#     fig.colorbar(cont_xy, ax=ax_xy, label="Flujo")
    
#     if points_view == True:
#         ax_xy.scatter(x, y, c="red", s=10, label="Puntos")

#     ax_xy.set_title(f"Vista XY - {title}", fontsize=14)
#     ax_xy.set_xlabel("X")
#     ax_xy.set_ylabel("Y")
#     ax_xy.axis("equal")
#     ax_xy.grid(True, linestyle="--", alpha=0.5)

#     # Vista YZ
#     ax_yz = axes[1]
#     cont_yz = ax_yz.tricontourf(triang_yz, fluxes_log, levels=10, cmap="viridis")
#     fig.colorbar(cont_yz, ax=ax_yz, label="Flujo")

#     if points_view == True:
#         ax_yz.scatter(y, z, c="red", s=10, label="Puntos")

#     ax_yz.set_title(f"Vista YZ - {title}", fontsize=14)
#     ax_yz.set_xlabel("Y")
#     ax_yz.set_ylabel("Z")
#     ax_yz.axis("equal")
#     ax_yz.grid(True, linestyle="--", alpha=0.5)

#     # Agregar texto del valor de flux_ratio
#     for ax in axes:
#         ax.text(0.05, 0.95, f"Flux Ratio: {flux_ratio:.2f}", transform=ax.transAxes,
#                 fontsize=12, verticalalignment='top', bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))

#     # Mostrar la figura
#     plt.show()


import matplotlib.pyplot as plt
import numpy as np
from matplotlib.tri import Triangulation
from pathlib import Path

def plot_isodose_map(points_inside_moved, fluxes, flux_ratio, flux_max, flux_min, points_view=False, title="Mapa de Isodosis"):
    """
    Genera un mapa de isodosis logarítmico usando los puntos y flujos calculados en las vistas XY y YZ.

    Parámetros:
    - points_inside_moved: Arreglo de puntos (n x 3) representando las coordenadas x, y, z.
    - fluxes: Arreglo columna (n x 1) representando los flujos en cada punto.
    - flux_ratio: Valor numérico que representa la proporción de flujo que se desea mostrar en el gráfico.
    - flux_max: Valor máximo del flujo en la escala del gráfico.
    - flux_min: Valor mínimo del flujo en la escala del gráfico.
    - points_view: Booleano que indica si se deben mostrar los puntos en el gráfico.
    - title: Título del gráfico.
    """
    # Extraer las coordenadas x, y, z de los puntos
    x = points_inside_moved[:, 0]
    y = points_inside_moved[:, 1]
    z = points_inside_moved[:, 2]

    # Convertir flujos a escala logarítmica
    fluxes_log = fluxes.flatten()

    # Crear las triangulaciones para graficar
    triang_xy = Triangulation(x, y)
    triang_yz = Triangulation(y, z)

    # Crear la figura y los subgráficos
    fig, axes = plt.subplots(2, 1, figsize=(10, 15), constrained_layout=True)

    # Definir niveles de contorno basados en flux_max y flux_min
    levels = np.linspace(flux_min, flux_max, 10)

    # Vista XY
    ax_xy = axes[0]
    cont_xy = ax_xy.tricontourf(triang_xy, fluxes_log, levels=levels, cmap="viridis")
    fig.colorbar(cont_xy, ax=ax_xy, label="kGy/h")
    
    if points_view == True:
        ax_xy.scatter(x, y, c="red", s=10, label="Puntos")

    ax_xy.set_title(f"Vista XY - {title}", fontsize=14)
    ax_xy.set_xlabel("X")
    ax_xy.set_ylabel("Y")
    ax_xy.axis("equal")
    ax_xy.grid(True, linestyle="--", alpha=0.5)

    # Vista YZ
    ax_yz = axes[1]
    cont_yz = ax_yz.tricontourf(triang_yz, fluxes_log, levels=levels, cmap="viridis")
    fig.colorbar(cont_yz, ax=ax_yz, label="kGy/h")

    if points_view == True:
        ax_yz.scatter(y, z, c="red", s=10, label="Puntos")

    ax_yz.set_title(f"Vista YZ - {title}", fontsize=14)
    ax_yz.set_xlabel("Y")
    ax_yz.set_ylabel("Z")
    ax_yz.axis("equal")
    ax_yz.grid(True, linestyle="--", alpha=0.5)

    # Agregar texto del valor de flux_ratio, flux_max y flux_min
    for ax in axes:
        ax.text(0.05, 0.95, f"Flux Ratio: {flux_ratio:.2f}\nFlux Max: {flux_max:.2e}\nFlux Min: {flux_min:.2e}",
                transform=ax.transAxes, fontsize=12, verticalalignment='top', 
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))

    # Mostrar la figura
    # plt.show()
    return fig


def render_isodose_cuts(points, values, output_root, title="Distribución de dosis"):
    """Guarda las proyecciones XY y YZ usadas para comparar ambos métodos."""
    points = np.asarray(points, dtype=float)
    values = np.asarray(values, dtype=float).reshape(-1)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    minimum = float(values.min())
    maximum = float(values.max())
    ratio = maximum / minimum if minimum > 0 else float("inf")
    if np.isclose(minimum, maximum):
        levels = np.linspace(minimum * 0.99, maximum * 1.01 + 1e-12, 10)
    else:
        levels = np.linspace(minimum, maximum, 12)

    results = {}
    for plane, first_axis, second_axis, labels in (
        ("xy", 0, 1, ("X", "Y")),
        ("yz", 1, 2, ("Y", "Z")),
    ):
        fig, axis = plt.subplots(figsize=(9, 6), constrained_layout=True)
        try:
            triangulation = Triangulation(
                points[:, first_axis], points[:, second_axis]
            )
            contour = axis.tricontourf(
                triangulation, values, levels=levels, cmap="viridis"
            )
        except (RuntimeError, ValueError):
            contour = axis.scatter(
                points[:, first_axis],
                points[:, second_axis],
                c=values,
                s=9,
                cmap="viridis",
                vmin=minimum,
                vmax=maximum,
            )
        fig.colorbar(contour, ax=axis, label="Tasa de dosis absorbida [kGy/h]")
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
        destination = output_root / ("isodose_%s.png" % plane)
        fig.savefig(destination, dpi=170)
        plt.close(fig)
        results[plane] = destination.name
    return {
        "cuts": results,
        "minimum_dose": minimum,
        "maximum_dose": maximum,
        "dose_uniformity_ratio": ratio,
    }
