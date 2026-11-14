import numpy as np
import matplotlib.pyplot as plt
from matplotlib.tri import Triangulation

# def plot_isodose_map(points_inside_moved, fluxes, flux_ratio, flux_max, flux_min, points_view = False,title="Mapa de Isodosis"):
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
    fig.colorbar(cont_xy, ax=ax_xy, label="Flujo")
    
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
    fig.colorbar(cont_yz, ax=ax_yz, label="Flujo")

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
    plt.show()
