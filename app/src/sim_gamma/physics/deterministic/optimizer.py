import numpy as np
from stl import mesh
import trimesh
import matplotlib.pyplot as plt
import pandas as pd
from mpl_toolkits.mplot3d import Axes3D


# Función para trasladar puntos tomando como referencia el centro geométrico
def translate_to_point(points, geometric_center, target_point):
    """
    Traslada todos los puntos de forma que el centro geométrico se desplace a un punto dado.
    
    Parameters:
    - points: ndarray, forma (N, 3), contiene las coordenadas [x, y, z] de los puntos.
    - geometric_center: array-like, forma (3,), el centro geométrico actual [gx, gy, gz].
    - target_point: array-like, forma (3,), el punto objetivo [tx, ty, tz] al que se trasladará el centro geométrico.
    
    Returns:
    - ndarray, puntos trasladados.
    """
    translation_vector = np.array(target_point) - np.array(geometric_center)
    return points + translation_vector


import numpy as np

def rotate_points(angle_degrees, points, axis_point1, axis_point2):
    """
    Rota un conjunto de puntos alrededor de un eje definido por dos puntos en el espacio 3D.

    Parámetros:
        angle_degrees (float): Ángulo de rotación en grados.
        points (np.ndarray): Matriz de forma (n, 3) que contiene los puntos a rotar.
        axis_point1 (array-like): Primer punto que define el eje de rotación.
        axis_point2 (array-like): Segundo punto que define el eje de rotación.

    Retorna:
        np.ndarray: Matriz de puntos rotados con la misma forma que `points`.
    """
    # Calcular el eje de rotación y normalizarlo
    axis = np.array(axis_point2) - np.array(axis_point1)
    axis = axis / np.linalg.norm(axis)

    # Convertir el ángulo a radianes
    angle_radians = np.radians(angle_degrees)

    # Calcular los componentes de la matriz de rotación
    cos_theta = np.cos(angle_radians)
    sin_theta = np.sin(angle_radians)
    ux, uy, uz = axis

    rotation_matrix = np.array([
        [
            cos_theta + ux**2 * (1 - cos_theta),
            ux * uy * (1 - cos_theta) - uz * sin_theta,
            ux * uz * (1 - cos_theta) + uy * sin_theta
        ],
        [
            uy * ux * (1 - cos_theta) + uz * sin_theta,
            cos_theta + uy**2 * (1 - cos_theta),
            uy * uz * (1 - cos_theta) - ux * sin_theta
        ],
        [
            uz * ux * (1 - cos_theta) - uy * sin_theta,
            uz * uy * (1 - cos_theta) + ux * sin_theta,
            cos_theta + uz**2 * (1 - cos_theta)
        ]
    ])

    # Trasladar los puntos al origen del eje de rotación
    translated_points = np.array(points) - np.array(axis_point1)

    # Aplicar la rotación
    rotated_points = np.dot(translated_points, rotation_matrix.T)

    # Regresar los puntos a su posición original
    rotated_points += np.array(axis_point1)

    return rotated_points


import numpy as np

def generate_points_within_solid(mesh, num_points=1000):
    """
    Genera puntos aleatorios dentro de un mesh sólido.

    Parámetros:
        mesh: El objeto de malla con un método `contains` y una propiedad `bounds`.
        num_points (int): La cantidad deseada de puntos a generar.

    Retorna:
        np.ndarray: Array de puntos dentro del mesh sólido.
    """
    bbox_min, bbox_max = mesh.bounds  # Límites de la caja delimitadora
    points = []

    while len(points) < num_points:
        random_points = np.random.uniform(
            bbox_min, bbox_max, size=(num_points * 2, 3)
        )  # Generar puntos aleatorios
        inside_points = [point for point in random_points if mesh.contains([point])[0]]
        points.extend(inside_points[:num_points - len(points)])  # Agregar solo los puntos necesarios

    return np.array(points)



import json
import numpy as np
import trimesh
from stl import mesh

def upload_cad_file(
    cad_file,
    work_section,
    num_points=1000,
    angle_degrees=0,
    work_sections_file="work_sections.json",
):
    """
    Carga un archivo STL, genera puntos dentro del sólido, realiza transformaciones opcionales
    y traslada los puntos a una sección de trabajo específica.

    Args:
        cad_file (str): Ruta del archivo STL.
        work_section (str): Nombre de la sección de trabajo.
        num_points (int, optional): Número de puntos a generar dentro del sólido. Default es 1000.
        angle_degrees (float, optional): Ángulo de rotación en grados alrededor del eje Z. Default es 0, en caso de que sea necesario rotar el sólido al cargar el archivo.

    Returns:
        tuple: Centro geométrico, puntos dentro del sólido, vértices transformados.
    """
    # Cargar el archivo CAD y obtener los vértices
    solid_mesh = mesh.Mesh.from_file(cad_file)
    vertices = solid_mesh.vectors.reshape(-1, 3)
    geometric_center = np.mean(vertices, axis=0)

    # Crear una malla de triángulos
    tri_mesh = trimesh.Trimesh(vertices=vertices, faces=np.arange(len(vertices)).reshape(-1, 3))
    points_inside = generate_points_within_solid(tri_mesh, num_points=num_points)

    # Aplicar rotación si es necesario
    if angle_degrees != 0:
        # Trasladar al origen para rotar
        vertices = translate_to_point(vertices, geometric_center, [0, 0, 0])
        points_inside = translate_to_point(points_inside, geometric_center, [0, 0, 0])
        geometric_center = [0, 0, 0]

        # Rotar alrededor del eje Z
        axis_point1 = [0, 0, 0]
        axis_point2 = [0, 0, 1]
        vertices = rotate_points(angle_degrees, vertices, axis_point1, axis_point2)
        points_inside = rotate_points(angle_degrees, points_inside, axis_point1, axis_point2)

    # Cargar las secciones de trabajo desde un archivo JSON
    with open(work_sections_file, 'r') as file:
        work_sections = json.load(file)

    positions = work_sections.get('positions', {})

    # Verificar si la sección de trabajo existe
    if work_section in positions:
        position = positions[work_section]
    else:
        raise ValueError(f"Sección de trabajo no válida: {work_section}")

    # Trasladar a la sección de trabajo
    vertices = translate_to_point(vertices, geometric_center, position)
    points_inside = translate_to_point(points_inside, geometric_center, position)
    geometric_center = position

    return geometric_center, points_inside, vertices


def rotate_translate_z(step_angle_degrees, step_length, geometric_center, points_inside, vertices,
                       axis_point1=None, axis_point2=None):
    """
    Realiza una serie de transformaciones sobre un conjunto de puntos y vértices. Primero traslada los puntos y
    vértices desde el centro geométrico hacia el origen de coordenadas, luego los rota alrededor de un eje especificado
    y finalmente los traslada de regreso al centro geométrico y los desplaza una longitud a lo largo del eje Z.

    Parámetros:
    - step_angle_degrees (float): El ángulo de rotación en grados.
    - step_length (float): La longitud para el desplazamiento en el eje Z.
    - geometric_center (list): Las coordenadas del centro geométrico [x, y, z].
    - points_inside (list): Lista de puntos a ser transformados.
    - vertices (list): Lista de vértices a ser transformados.
    - axis_point1 (list, opcional): El primer punto que define el eje de rotación (por defecto [0, 0, 0]).
    - axis_point2 (list, opcional): El segundo punto que define el eje de rotación (por defecto [0, 0, 1]).

    Retorna:
    - geometric_center_moved (list): El nuevo centro geométrico después de la transformación.
    - points_inside_moved (list): Lista de puntos transformados.
    - vertices_moved (list): Lista de vértices transformados.
    """
    if axis_point1 is None:
        axis_point1 = [0, 0, 0]
    if axis_point2 is None:
        axis_point2 = [0, 0, 1]

    # Trasladar los puntos desde el centro geométrico hacia el origen de coordenadas
    points_inside_moved = translate_to_point(points_inside, geometric_center, [0, 0, 0])
    vertices_moved = translate_to_point(vertices, geometric_center, [0, 0, 0])

    # Realizar la rotación respecto al eje Z
    points_inside_moved = rotate_points(step_angle_degrees, points_inside_moved, axis_point1, axis_point2)
    vertices_moved = rotate_points(step_angle_degrees, vertices_moved, axis_point1, axis_point2)

    # Trasladar los puntos de regreso a la posición original (centro geométrico)
    points_inside_moved = translate_to_point(points_inside_moved, [0, 0, 0], geometric_center)
    vertices_moved = translate_to_point(vertices_moved, [0, 0, 0], geometric_center)

    # Trasladar los puntos una longitud en el eje Z
    points_inside_moved = translate_to_point(points_inside_moved, geometric_center,
                                              [geometric_center[0], geometric_center[1],
                                               geometric_center[2] + step_length])
    vertices_moved = translate_to_point(vertices_moved, geometric_center,
                                        [geometric_center[0], geometric_center[1],
                                         geometric_center[2] + step_length])

    geometric_center_moved = [geometric_center[0], geometric_center[1], geometric_center[2] + step_length]

    return geometric_center_moved, points_inside_moved, vertices_moved


import json
from datetime import datetime

def add_sources_with_decay_theo(sources_data, decay_factor=0.1):
    """
    Calcula el decaimiento de la intensidad de las fuentes basado en la fecha actual.

    Parámetros:
    -----------
    sources_data : list of dicts
        Lista de diccionarios que contiene los datos de las fuentes (cada fuente tiene 
        los campos 'lower_point', 'upper_point', 'intensity' y 'initial_reading_date').
    decay_factor : float, opcional
        El factor de decaimiento de la intensidad (por defecto es 0.1).

    Retorna:
    --------
    tuple of lists
        Retorna una lista:
        - activities : lista de intensidades decaídas de las fuentes.
    """
    # Obtener la fecha actual del sistema
    current_date = datetime.today().strftime('%Y-%m-%d')

    activities = []

    # Calcular el decaimiento para cada fuente
    for source in sources_data:

        initial_reading_date = datetime.strptime(source["initial_reading_date"], "%Y-%m-%d")
        intensity = source["intensity"]

        # Calcular el tiempo transcurrido desde la fecha de lectura inicial
        time_diff = (datetime.strptime(current_date, "%Y-%m-%d") - initial_reading_date).days

        # Aplicar el decaimiento de la intensidad
        decayed_intensity = intensity * np.exp(-decay_factor * time_diff)

        # Añadir la intensidad decaída a las listas correspondientes
        activities.append(decayed_intensity)
    
    return activities




import numpy as np
import json

# def calculate_gamma_flux(sources, step_angle_degrees, step_length, geometric_center, points_inside, vertices, decay_factor):
#     """
#     Calcula el flujo de rayos gamma debido a varias fuentes lineales.
    
#     Parámetros:
#     - sources: Lista de fuentes lineales, cada una con lower_point, upper_point, intensidad y fecha inicial.
#     - step_angle_degrees: Ángulo de rotación en grados.
#     - step_length: Longitud de traslación en la dirección Z.
#     - geometric_center: Centro geométrico inicial.
#     - points_inside: Puntos dentro del volumen que se mueven.
#     - vertices: Vértices del volumen.
    
#     Retorna:
#     - points_inside_moved: Puntos dentro del volumen después de la transformación geométrica.
#     - fluxes: Un arreglo columna (n x 1) con el flujo total en cada punto del volumen.
#     """
#     # Aplica la transformación geométrica
#     _, points_inside_moved, _ = rotate_translate_z(
#         step_angle_degrees, step_length, geometric_center, points_inside, vertices
#     )
    
    
#     # Calcula las intensidades decaídas de las fuentes
#     decayed_intensities = add_sources_with_decay_theo(sources, decay_factor)
    
#     # Inicializa el arreglo para almacenar los flujos
#     fluxes = np.zeros(len(points_inside_moved))
    
#     # Calcula el flujo en cada punto del volumen debido a cada fuente lineal
#     for source, intensity in zip(sources, decayed_intensities):
#         lower_point = np.array(source["lower_point"])
#         upper_point = np.array(source["upper_point"])
        
#         # Calcular la longitud de la fuente lineal
#         L_source = np.linalg.norm(upper_point - lower_point)
        
#         # Intensidad por unidad de longitud
#         q_l = intensity / L_source
        
#         for i, point in enumerate(points_inside_moved):
#             # Distancia en XY con respecto al punto
#             a = np.linalg.norm(lower_point[:2] - point[:2])

#             if lower_point[2] <= point[2] <= upper_point[2]:
#                 # Caso 1: Punto dentro del rango de la fuente en z
#                 l_1 = np.abs(point[2] - lower_point[2])
#                 l_2 = np.abs(upper_point[2] - point[2])

#                 flux = (q_l / (4 * np.pi * a)) * (np.arctan(l_2 / a) + np.arctan(l_1 / a))
            
#             elif point[2] > upper_point[2]:
#                 # Caso 2: Punto fuera del rango por encima de la fuente
#                 b = np.abs(point[2] - upper_point[2])
#                 flux = (q_l / (4 * np.pi * a)) * (np.arctan((L_source + b) / a) - np.arctan(b / a))
            
#             elif point[2] < upper_point[2]:
#                 # Caso 3: Punto fuera del rango por debajo de la fuente
#                 b = np.abs(lower_point[2] - point[2])
#                 flux = (q_l / (4 * np.pi * a)) * (np.arctan((L_source + b) / a) - np.arctan(b / a))
            
#             fluxes[i] += flux

#     # Convierte el arreglo en una columna (n x 1)
#     fluxes = fluxes.reshape(-1, 1)
    
#     return points_inside_moved, fluxes

def calculate_gamma_flux(sources, step_angle_degrees, step_length,
                         geometric_center, points_inside, vertices, decay_factor,
                         point_chunk_size=2048):

    _, points_inside_moved, _ = rotate_translate_z(
        step_angle_degrees, step_length, geometric_center, points_inside, vertices
    )

    decayed_intensities = np.array(add_sources_with_decay_theo(sources, decay_factor))

    # Extraer geometría de todas las fuentes de una vez → shape (S, 3)
    lower_points = np.array([s["lower_point"] for s in sources])   # (S, 3)
    upper_points = np.array([s["upper_point"] for s in sources])   # (S, 3)

    # Longitud de cada fuente y q_l → shape (S,)
    L_source = np.linalg.norm(upper_points - lower_points, axis=1)  # (S,)
    q_l = decayed_intensities / L_source                             # (S,)

    # Las matrices fuente x punto son el principal pico de RAM. Se calculan
    # por bloques para que 100 000 puntos no creen decenas de matrices
    # gigantes simultáneas en cada proceso del optimizador.
    pts = np.asarray(points_inside_moved)
    chunk_size = max(1, int(point_chunk_size))
    fluxes = np.empty((len(pts), 1), dtype=np.float64)
    lp = lower_points[:, np.newaxis, :]
    up = upper_points[:, np.newaxis, :]
    lpz = lp[:, :, 2]
    upz = up[:, :, 2]
    source_lengths = L_source[:, np.newaxis]
    linear_intensities = q_l[:, np.newaxis]

    for start in range(0, len(pts), chunk_size):
        stop = min(start + chunk_size, len(pts))
        point_block = pts[start:stop][np.newaxis, :, :]
        distance_xy = np.linalg.norm(
            lp[:, :, :2] - point_block[:, :, :2], axis=2
        )
        distance_xy = np.maximum(distance_xy, 1e-12)
        point_z = point_block[:, :, 2]
        prefactor = linear_intensities / (4.0 * np.pi * distance_xy)

        distance_lower = np.abs(point_z - lpz)
        distance_upper = np.abs(upz - point_z)
        flux_inside = prefactor * (
            np.arctan(distance_upper / distance_xy)
            + np.arctan(distance_lower / distance_xy)
        )

        above_distance = np.abs(point_z - upz)
        flux_above = prefactor * (
            np.arctan((source_lengths + above_distance) / distance_xy)
            - np.arctan(above_distance / distance_xy)
        )

        below_distance = np.abs(lpz - point_z)
        flux_below = prefactor * (
            np.arctan((source_lengths + below_distance) / distance_xy)
            - np.arctan(below_distance / distance_xy)
        )

        in_range = (lpz <= point_z) & (point_z <= upz)
        above = point_z > upz
        block_flux = np.where(
            in_range, flux_inside, np.where(above, flux_above, flux_below)
        )
        fluxes[start:stop, 0] = block_flux.sum(axis=0)

    return points_inside_moved, fluxes

# from itertools import product
# import numpy as np

# def calculate_accumulated_flux_with_steps(
#     sources, 
#     step_angle_degrees_range, 
#     step_length_range, 
#     num_steps_angle,
#     num_steps_length, 
#     geometric_center, 
#     points_inside, 
#     vertices,
#     decay_factor
# ):
    
#     """
#     Calcula el flujo acumulado de rayos gamma para variaciones de step_angle_degrees y step_length
#     usando un número diferente de pasos para ambos rangos.
    
#     Parámetros:
#     - sources: Lista de fuentes puntuales, cada una con [x, y, z, intensidad].
#     - step_angle_degrees_range: Tupla (min, max) con los valores de inicio y fin para step_angle_degrees.
#     - step_length_range: Tupla (min, max) con los valores de inicio y fin para step_length.
#     - num_steps_angle: Número de pasos en ambos rangos para la rotación.
#     - num_steps_lenght: Número de pasos en ambos rangos para la traslación.
#     - geometric_center: Centro geométrico inicial.
#     - points_inside: Puntos dentro del volumen que se mueven.
#     - vertices: Vértices del volumen.
    
#     Retorna:
#     - points_inside_moved: Posiciones transformadas en el último paso.
#     - accumulated_fluxes: Arreglo columna (n x 1) con el flujo acumulado en cada punto del volumen.
#     """

    
#     # Genera los rangos de valores con un número fijo de pasos
#     step_angle_degrees_values = np.linspace(step_angle_degrees_range[0], step_angle_degrees_range[1], num_steps_angle)
#     step_length_values = np.linspace(step_length_range[0], step_length_range[1], num_steps_length)
    
#     # Inicializa el arreglo para almacenar el flujo acumulado como columna
#     accumulated_fluxes = np.zeros((len(points_inside), 1))
    
#     # Itera sobre las combinaciones de step_angle_degrees y step_length
#     for step_angle, step_length in product(step_angle_degrees_values, step_length_values):
#         # Calcula el flujo para los valores actuales de step_angle y step_length
#         points_inside_moved, fluxes = calculate_gamma_flux(
#             sources, 
#             step_angle, 
#             step_length, 
#             geometric_center, 
#             points_inside, 
#             vertices,
#             decay_factor=decay_factor
#         )
#         # Suma el flujo al acumulado
#         accumulated_fluxes += fluxes
        
#     # Calcula el cociente entre el valor máximo y mínimo del flujo acumulado
#     max_flux = np.max(accumulated_fluxes)
#     min_flux = np.min(accumulated_fluxes)
#     flux_ratio = max_flux / min_flux if min_flux > 0 else float('inf')  # Evitar división por cero
    
#     return points_inside_moved, accumulated_fluxes, flux_ratio


# CAMBIO COMBINACION ALTURAS

from itertools import product
import numpy as np

def calculate_accumulated_flux_with_steps(
    sources, 
    step_angle_degrees_range, 
    step_length_values,
    num_steps_angle,
    num_steps_length, 
    geometric_center, 
    points_inside, 
    vertices,
    decay_factor
):
    
    """
    Calcula el flujo acumulado de rayos gamma para variaciones de
    step_angle_degrees y posiciones específicas de step_length.

    step_length_values puede contener cualquier combinación de
    posiciones verticales.

    Ejemplos:
        (-25, 25)
        (-50, 0, 50)
        (-50, -25, 25, 50)

    Retorna:
    - points_inside_moved: Posiciones transformadas en el último paso.
    - accumulated_fluxes: Flujo acumulado.
    - flux_ratio: Relación entre flujo máximo y mínimo.
    """

    # Genera los valores de ángulo
    step_angle_degrees_values = np.linspace(
        step_angle_degrees_range[0],
        step_angle_degrees_range[1],
        num_steps_angle
    )

    # Utiliza directamente las posiciones verticales proporcionadas
    step_length_values = np.asarray(
        step_length_values,
        dtype=float
    )
    
    # Inicializa el arreglo para almacenar el flujo acumulado
    accumulated_fluxes = np.zeros(
        (len(points_inside), 1)
    )
    
    # Itera sobre todas las combinaciones de ángulo y posición vertical
    for step_angle, step_length in product(
        step_angle_degrees_values,
        step_length_values
    ):
        
        points_inside_moved, fluxes = calculate_gamma_flux(
            sources, 
            step_angle, 
            step_length, 
            geometric_center, 
            points_inside, 
            vertices,
            decay_factor=decay_factor
        )
        
        accumulated_fluxes += fluxes
        
    # Calcula el cociente entre el valor máximo y mínimo
    max_flux = np.max(accumulated_fluxes)
    min_flux = np.min(accumulated_fluxes)
    
    flux_ratio = (
        max_flux / min_flux
        if min_flux > 0
        else float('inf')
    )
    
    return (
        points_inside_moved,
        accumulated_fluxes,
        flux_ratio
    )






# import json

# def process_sources_and_calculate_flux_accumulated(
#     geometric_center, 
#     points_inside, 
#     vertices, 
#     num_steps_angle=4, 
#     revolutions=1, 
#     num_steps_length=1, 
#     length_complete_lower=0, 
#     length_complete_upper=0,
#     filename="sources.json",
#     decay_factor=0.1
# ):
    
#     """Procesa el archivo JSON de fuentes, calcula el flujo acumulado con pasos de rotación y traslación.
#     El archivo JSON debe contener una lista de fuentes con coordenadas y intensidades.
#     El numero total de pasos es num_steps_angle * num_steps_length.

#     Args:
#         geometric_center: Centro geométrico del volumen de la carga.
#         points_inside: Puntos dentro del volumen de la carga.
#         vertices: Vértices de la geometría del volumen de la carga.
#         num_steps_angle (int): Número de pasos para los ángulos de rotación.
#         revolutions (int): Número de revoluciones completas de la carga.
#         num_steps_length (int): Número de pasos entre las posiciones de traslación.
#         length_complete_lower (float): Posición de traslación inicial en el eje Z.
#         length_complete_upper (float): Posición de traslación final en el eje Z.
#         filename (str): Nombre del archivo JSON con las fuentes.
        
        

#     Returns:
#         tuple: Puntos dentro del área movida, flujos acumulados y la relación de flujos.
#     """
#     # Cargar el archivo JSON con las fuentes
#     with open(filename, "r") as json_file:
#         data = json.load(json_file)

#     # Calcular los rangos de pasos
#     angle = 360 * revolutions
#     angle_complete = angle - (angle / num_steps_angle)
#     step_angle_degrees_range = (0, angle_complete)  # Ángulo de rotación entre 0 y 360 grados para un giro completo

#     step_length_range = (length_complete_lower, length_complete_upper)  # Longitud de traslación

#     # Asegúrate de definir la función `calculate_accumulated_flux_with_steps` y los parámetros correspondientes antes de llamarla.
#     points_inside_moved, accumulated_fluxes, flux_ratio = calculate_accumulated_flux_with_steps(
#         data["sources"], 
#         step_angle_degrees_range, 
#         step_length_range, 
#         num_steps_angle,
#         num_steps_length, 
#         geometric_center, 
#         points_inside, 
#         vertices,
#         decay_factor=decay_factor
#     )


#     return points_inside_moved, accumulated_fluxes, flux_ratio

# CAMBIO COMBINACION ALTURAS

import json
import numpy as np

def process_sources_and_calculate_flux_accumulated(
    geometric_center, 
    points_inside, 
    vertices, 
    num_steps_angle=4, 
    revolutions=1, 
    num_steps_length=1, 
    length_complete_lower=0, 
    length_complete_upper=0,
    filename="sources.json",
    decay_factor=0.1,
    length_complete_values=None
):
    
    """
    Procesa el archivo JSON de fuentes y calcula el flujo acumulado.

    length_complete_values permite especificar directamente las
    posiciones verticales que se quieren evaluar.

    Ejemplos:
        length_complete_values=(-25, 25)

        length_complete_values=(-50, -25, 50)

    Si length_complete_values=None, mantiene el comportamiento
    original utilizando length_complete_lower y upper.
    """

    # Cargar el archivo JSON con las fuentes
    with open(filename, "r") as json_file:
        data = json.load(json_file)

    # Calcular los rangos de pasos angulares
    angle = 360 * revolutions

    angle_complete = angle - (
        angle / num_steps_angle
    )

    step_angle_degrees_range = (
        0,
        angle_complete
    )

    # ------------------------------------------------------------
    # NUEVO:
    # Si se proporciona una combinación de niveles, utilizarla
    # directamente.
    # ------------------------------------------------------------

    if length_complete_values is not None:

        step_length_values = length_complete_values

        num_steps_length = len(
            length_complete_values
        )

    else:

        # Comportamiento original
        step_length_values = np.linspace(
            length_complete_lower,
            length_complete_upper,
            num_steps_length
        )

    # Calcular flujo acumulado
    points_inside_moved, accumulated_fluxes, flux_ratio = (
        calculate_accumulated_flux_with_steps(
            data["sources"], 
            step_angle_degrees_range, 
            step_length_values,
            num_steps_angle,
            num_steps_length, 
            geometric_center, 
            points_inside, 
            vertices,
            decay_factor=decay_factor
        )
    )

    return (
        points_inside_moved,
        accumulated_fluxes,
        flux_ratio
    )





# def optimize_flux_ratio(
#     geometric_center,
#     points_inside,
#     vertices,
#     num_steps_angle_range=(1, 36),
#     length_complete_lower_range=(-1, 0),
#     length_complete_upper_range=(0, 1),
#     resolution_length=1,
#     decay_factor=0.1
# ):
#     """
#     Encuentra la combinación óptima de parámetros enteros para que el `flux_ratio` sea lo más cercano posible a 1.

#     Parámetros:
#         geometric_center: Centro geométrico de la estructura.
#         points_inside: Puntos dentro del área de interés.
#         vertices: Vértices de la estructura.
#         num_steps_angle_range (tuple): Rango de valores enteros para `num_steps_angle`.
#         revolutions_range (tuple): Rango de valores enteros para `revolutions`.
#         num_steps_length_range (tuple): Rango de valores enteros para `num_steps_length`.
#         length_complete_lower_range (tuple): Rango de valores enteros para `length_complete_lower`.
#         length_complete_upper_range (tuple): Rango de valores enteros para `length_complete_upper`.
#         resolution_length (int): Resolución del incremento de búsqueda en los límites de longitud.
#         decay_factor (float): Factor de decaimiento.

#     Retorna:
#         best_params (tuple): Tupla con los parámetros enteros óptimos.
#         best_flux_ratio (float): Valor más cercano a 1 encontrado para `flux_ratio`.
#     """
#     best_params = None
#     best_flux_ratio = float('inf')
#     best_diff = float('inf')

#     for num_steps_angle in range(num_steps_angle_range[0], num_steps_angle_range[1]):

#         if length_complete_lower_range == length_complete_upper_range:
#             revolutions = 1
#             num_steps_length = 1
#             length_complete_lower = length_complete_upper = length_complete_lower_range[0]

#             _, _, flux_ratio = process_sources_and_calculate_flux_accumulated(
#                 geometric_center,
#                 points_inside,
#                 vertices,
#                 num_steps_angle=num_steps_angle,
#                 revolutions=revolutions,
#                 num_steps_length=num_steps_length,
#                 length_complete_lower=length_complete_lower,
#                 length_complete_upper=length_complete_upper,
#                 decay_factor=decay_factor
#             )

#             flux_ratio = round(flux_ratio, 2)
#             diff = abs(flux_ratio - 1)
#             if diff < best_diff:
#                 best_diff = diff
#                 best_flux_ratio = flux_ratio
#                 best_params = (num_steps_angle, revolutions, num_steps_length, length_complete_lower, length_complete_upper)

#                 if best_diff == 0:
#                     return best_params, best_flux_ratio

#         else:
#             revolutions = 1
#             num_steps_length = 2

#             for length_complete_lower in range(length_complete_lower_range[0], length_complete_lower_range[1], resolution_length):
#                 for length_complete_upper in range(length_complete_upper_range[0], length_complete_upper_range[1] + 1, resolution_length):
                    
#                     _, _, flux_ratio = process_sources_and_calculate_flux_accumulated(
#                         geometric_center,
#                         points_inside,
#                         vertices,
#                         num_steps_angle=num_steps_angle,
#                         revolutions=revolutions,
#                         num_steps_length=num_steps_length,
#                         length_complete_lower=length_complete_lower,
#                         length_complete_upper=length_complete_upper,
#                         decay_factor=decay_factor
#                     )

#                     flux_ratio = round(flux_ratio, 2)
#                     diff = abs(flux_ratio - 1)
#                     if diff < best_diff:
#                         best_diff = diff
#                         best_flux_ratio = flux_ratio
#                         best_params = (num_steps_angle, revolutions, num_steps_length, length_complete_lower, length_complete_upper)

#                         if best_diff == 0:
#                             return best_params, best_flux_ratio

#     return best_params, best_flux_ratio


# from joblib import Parallel, delayed

# def optimize_flux_ratio(
#     geometric_center,
#     points_inside,
#     vertices,
#     num_steps_angle_range=(1, 36),
#     length_complete_lower_range=(-1, 0),
#     length_complete_upper_range=(0, 1),
#     resolution_length=1,
#     decay_factor=0.1,
#     n_jobs=-1  # -1 = todos los cores disponibles
# ):
#     """
#     Encuentra la combinación óptima de parámetros enteros para que el `flux_ratio` sea lo más cercano posible a 1.
#     Versión paralelizada con joblib.

#     Parámetros:
#         geometric_center: Centro geométrico de la estructura.
#         points_inside: Puntos dentro del área de interés.
#         vertices: Vértices de la estructura.
#         num_steps_angle_range (tuple): Rango de valores enteros para `num_steps_angle`.
#         length_complete_lower_range (tuple): Rango de valores enteros para `length_complete_lower`.
#         length_complete_upper_range (tuple): Rango de valores enteros para `length_complete_upper`.
#         resolution_length (int): Resolución del incremento de búsqueda en los límites de longitud.
#         decay_factor (float): Factor de decaimiento.
#         n_jobs (int): Número de cores a usar. -1 usa todos los disponibles.

#     Retorna:
#         best_params (tuple): Tupla con los parámetros enteros óptimos.
#         best_flux_ratio (np.float64): Valor más cercano a 1 encontrado para `flux_ratio`.
#     """

#     is_degenerate = (length_complete_lower_range == length_complete_upper_range)

#     # --- Construir lista de combinaciones a evaluar ---
#     combinations = []

#     if is_degenerate:
#         length_complete_lower = length_complete_upper = length_complete_lower_range[0]
#         for num_steps_angle in range(num_steps_angle_range[0], num_steps_angle_range[1]):
#             combinations.append((
#                 num_steps_angle, 1, 1,
#                 length_complete_lower,
#                 length_complete_upper
#             ))
#     else:
#         for num_steps_angle in range(num_steps_angle_range[0], num_steps_angle_range[1]):
#             for length_complete_lower in range(length_complete_lower_range[0], length_complete_lower_range[1], resolution_length):
#                 for length_complete_upper in range(length_complete_upper_range[0], length_complete_upper_range[1] + 1, resolution_length):
#                     combinations.append((
#                         num_steps_angle, 1, 2,
#                         length_complete_lower,
#                         length_complete_upper
#                     ))

#     # --- Función que evalúa una sola combinación ---
#     def evaluate(num_steps_angle, revolutions, num_steps_length, length_complete_lower, length_complete_upper):
#         _, _, flux_ratio = process_sources_and_calculate_flux_accumulated(
#             geometric_center,
#             points_inside,
#             vertices,
#             num_steps_angle=num_steps_angle,
#             revolutions=revolutions,
#             num_steps_length=num_steps_length,
#             length_complete_lower=length_complete_lower,
#             length_complete_upper=length_complete_upper,
#             decay_factor=decay_factor
#         )
#         flux_ratio = round(flux_ratio, 2)
#         return flux_ratio

#     # --- Ejecutar todas las combinaciones en paralelo ---
#     flux_ratios = Parallel(n_jobs=n_jobs, backend="loky")(
#         delayed(evaluate)(*combo) for combo in combinations
#     )

#     # --- Seleccionar el mejor resultado ---
#     diffs = [abs(fr - 1.0) for fr in flux_ratios]
#     best_idx = int(np.argmin(diffs))

#     best_params = combinations[best_idx]
#     best_flux_ratio = np.float64(flux_ratios[best_idx])

#     return best_params, best_flux_ratio


# CAMBIO COMBINACION ALTURAS

from joblib import Parallel, delayed
from itertools import combinations
import numpy as np


def optimize_flux_ratio(
    geometric_center,
    points_inside,
    vertices,
    num_steps_angle_range=(1, 36),
    length_complete_lower_range=(-50, 0),
    length_complete_upper_range=(0, 50),
    resolution_length=25,
    decay_factor=0.1,
    sources_file="sources.json",
    n_jobs=-1,
    length_complete_values=None,
):
    """
    Encuentra la combinación óptima de:

        - cantidad de posiciones angulares equidistantes por revolución
        - niveles de length_complete

    para que flux_ratio sea lo más cercano posible a 1.

    Ejemplo:

        lower_range = (-50, 0)
        upper_range = (0, 50)
        resolution_length = 25

    genera:

        [-50, -25, 0, 25, 50]

    Se evalúan todas las combinaciones posibles de esos puntos,
    desde 2 puntos hasta todos los puntos disponibles.
    """

    # ============================================================
    # 1. CONSTRUIR LOS PUNTOS DE length_complete
    # ============================================================

    if length_complete_values is not None:
        length_points = sorted(set(length_complete_values))
    else:
        lower_values = range(
            length_complete_lower_range[0],
            length_complete_lower_range[1] + 1,
            resolution_length
        )

        upper_values = range(
            length_complete_upper_range[0],
            length_complete_upper_range[1] + 1,
            resolution_length
        )

        # Unir ambos rangos, eliminar duplicados y ordenar
        length_points = sorted(
            set(lower_values).union(upper_values)
        )

    if len(length_points) < 2:
        raise ValueError("Se requieren al menos dos posiciones longitudinales")

    # ============================================================
    # 2. GENERAR TODAS LAS COMBINACIONES DE NIVELES
    # ============================================================

    level_combinations = []

    for n_levels in range(
        2,
        len(length_points) + 1
    ):

        level_combinations.extend(
            combinations(
                length_points,
                n_levels
            )
        )

    # ============================================================
    # 3. COMBINAR ÁNGULOS + NIVELES
    # ============================================================

    combinations_to_evaluate = []

    for num_steps_angle in range(
        num_steps_angle_range[0],
        num_steps_angle_range[1]
    ):

        for length_combo in level_combinations:

            combinations_to_evaluate.append(
                (
                    num_steps_angle,
                    length_combo
                )
            )

    # ============================================================
    # 4. FUNCIÓN PARA EVALUAR UNA COMBINACIÓN
    # ============================================================

    def evaluate(
        num_steps_angle,
        length_combo
    ):

        _, _, flux_ratio = (
            process_sources_and_calculate_flux_accumulated(
                geometric_center,
                points_inside,
                vertices,

                num_steps_angle=num_steps_angle,

                revolutions=1,

                num_steps_length=len(
                    length_combo
                ),

                decay_factor=decay_factor,
                filename=sources_file,

                length_complete_values=length_combo
            )
        )

        flux_ratio = round(
            flux_ratio,
            2
        )

        return {
            "num_steps_angle": num_steps_angle,
            "length_complete": length_combo,
            "num_steps_length": len(length_combo),
            "flux_ratio": flux_ratio,
            "difference": abs(flux_ratio - 1.0)
        }

    # ============================================================
    # 5. EJECUTAR TODAS LAS COMBINACIONES EN PARALELO
    # ============================================================

    results = Parallel(
        n_jobs=n_jobs,
        backend="threading",
        batch_size=1,
        pre_dispatch=n_jobs if n_jobs > 0 else "2*n_jobs",
    )(
        delayed(evaluate)(*combo)
        for combo in combinations_to_evaluate
    )

    # ============================================================
    # 6. ENCONTRAR LA MEJOR COMBINACIÓN
    # ============================================================

    best_result = min(
        results,
        key=lambda x: x["difference"]
    )

    best_params = (
        best_result["num_steps_angle"],
        1,
        best_result["num_steps_length"],
        best_result["length_complete"]
    )

    best_flux_ratio = np.float64(
        best_result["flux_ratio"]
    )

    return best_params, best_flux_ratio



def calculate_best_angle_aproximate_real_world(step_angle_degrees_machine, num_steps_angle_best):
    """Calcula el valor más cercano a la realidad para el ángulo de rotación en el mundo real.
    
    Parámetros:
        step_angle_degrees_machine (list): Lista de valores de ángulos de rotación en la máquina.
        num_steps_angle_best (int): Número de pasos óptimo, 360/num_steps_angle [degrees].
        
    Returns:
        tuple: (Lista de ángulos ideales, Lista de valores más cercanos a la realidad)
    """
    step_angle_real = 360 / num_steps_angle_best  # Ángulo ideal en la realidad
    
    # Generar la lista de ángulos ideales
    step_angle_ideal = [step_angle_real * i for i in range(num_steps_angle_best)]
    
    # Buscar el valor más cercano en la lista de la máquina para cada múltiplo del ángulo real
    step_angle_degrees_best_real = [
        min(step_angle_degrees_machine, key=lambda x: abs(x - angle))
        for angle in step_angle_ideal
    ]
    
    return step_angle_ideal, step_angle_degrees_best_real


# import numpy as np

# def process_sources_and_calculate_flux_accumulated_real_world(
#     geometric_center, 
#     points_inside, 
#     vertices,
#     step_angle_degrees_range,
#     dose_coeff = 1,
#     step_length_range = [0, 0], 
#     filename="sources.json",
#     decay_factor=0.1
# ):
#     """Obtiene el flujo acumulado de para los valores de step_angle_degrees y step_length.
#     El archivo JSON debe contener una lista de fuentes con coordenadas y intensidades.
    
#         Args:
#         geometric_center: Centro geométrico del volumen de la carga.
#         points_inside: Puntos dentro del volumen de la carga.
#         vertices: Vértices de la geometría del volumen de la carga.
#         step_angle_degrees_range (list): Lista de valores para step_angle_degrees.
#         step_length_range (list): Lista de valores para step_length.
#         filename (str): Nombre del archivo JSON con las fuentes.
        
#         Returns:
#         tuple: Puntos dentro del área movida, flujos acumulados y la relación de flujos.
#     """

    
#     geometric_center = [geometric * 10 for geometric in geometric_center]  # Convertir a milímetros
#     points_inside = points_inside * 10  # Convertir a milímetros
#     vertices = vertices * 10  # Convertir a milímetros
    
    
#     # Cargar el archivo JSON con las fuentes
#     with open(filename, "r") as json_file:
#         data = json.load(json_file)
    
#     # Inicializa el arreglo para almacenar el flujo acumulado como columna
#     accumulated_fluxes = np.zeros((len(points_inside), 1))
    
#     step_angle_degrees_values= np.array(step_angle_degrees_range)
    
#     step_length_values = np.array([step_length_range[0]]) if step_length_range[0] == step_length_range[1] else np.array(step_length_range)
    
#     # Itera sobre las combinaciones de step_angle_degrees y step_length
#     for step_angle, step_length in product(step_angle_degrees_values, step_length_values):
#         # Calcula el flujo para los valores actuales de step_angle y step_length
#         points_inside_moved, fluxes = calculate_gamma_flux(
#             data["sources"], 
#             step_angle, 
#             step_length, 
#             geometric_center, 
#             points_inside, 
#             vertices,
#             decay_factor=decay_factor
#         )
        
#         # Suma el flujo al acumulado
#         fluxes_cm = fluxes * 100  # Convertir a particles/cm^2
        
#         accumulated_fluxes += fluxes_cm * dose_coeff  # Multiplicar por el coeficiente para obtener el valor de la tasa de dosis absorbida en kGy/h
    
#     # Calcula el cociente entre el valor máximo y mínimo del flujo acumulado
#     max_flux = np.max(accumulated_fluxes)
#     min_flux = np.min(accumulated_fluxes)
#     flux_ratio = max_flux / min_flux if min_flux > 0 else float('inf')  # Evitar división por cero
    
#     return points_inside_moved, accumulated_fluxes, flux_ratio

# CAMBIO COMBINACION ALTURAS

import numpy as np

def process_sources_and_calculate_flux_accumulated_real_world(
    geometric_center, 
    points_inside, 
    vertices,
    step_angle_degrees_range,
    dose_coeff=1,
    step_length_range=[0, 0], 
    filename="sources.json",
    decay_factor=0.1
):
    
    geometric_center = [geometric * 10 for geometric in geometric_center]
    points_inside = points_inside * 10
    vertices = vertices * 10
    
    with open(filename, "r") as json_file:
        data = json.load(json_file)
    
    accumulated_fluxes = np.zeros(
        (len(points_inside), 1)
    )
    
    step_angle_degrees_values = np.array(
        step_angle_degrees_range
    )
    
    # CAMBIO MÍNIMO
    step_length_values = np.array(
        step_length_range
    )
    
    for step_angle, step_length in product(
        step_angle_degrees_values,
        step_length_values
    ):
        
        points_inside_moved, fluxes = calculate_gamma_flux(
            data["sources"], 
            step_angle, 
            step_length, 
            geometric_center, 
            points_inside, 
            vertices,
            decay_factor=decay_factor
        )
        
        fluxes_cm = fluxes * 100
        
        accumulated_fluxes += (
            fluxes_cm * dose_coeff
        )
    
    max_flux = np.max(accumulated_fluxes)
    min_flux = np.min(accumulated_fluxes)
    
    flux_ratio = (
        max_flux / min_flux
        if min_flux > 0
        else float('inf')
    )
    
    return (
        points_inside_moved,
        accumulated_fluxes,
        flux_ratio
    )



import numpy as np
from scipy.spatial import Delaunay

def to_vtk_delaunay(points, data_values, vtk_file):
    """
    Convierte arrays 3D en una malla volumétrica VTK con triangulación de Delaunay
    
    Parámetros:
    points : array Nx3 con coordenadas (x, y, z)
    data_values : array N con valores escalares en cada punto
    vtk_file : nombre del archivo de salida VTK
    """
    # Convertir a arrays NumPy si no lo son
    points = np.asarray(points, dtype=np.float32)
    data_values = np.asarray(data_values, dtype=np.float32)
    
    # Calcular triangulación de Delaunay 3D
    tri = Delaunay(points)
    
    # Escribir archivo VTK
    with open(vtk_file, 'w') as file:
        # Encabezado
        file.write("# vtk DataFile Version 2.0\n")
        file.write("Malla volumétrica con Delaunay 3D\n")
        file.write("ASCII\n\n")
        
        file.write("DATASET UNSTRUCTURED_GRID\n")
        
        # Escribir puntos
        n_points = points.shape[0]
        file.write(f"POINTS {n_points} float\n")
        np.savetxt(file, points, fmt='%.6f')
        file.write("\n")
        
        # Escribir celdas (tetraedros)
        n_cells = tri.simplices.shape[0]
        file.write(f"CELLS {n_cells} {5*n_cells}\n")
        cells_output = np.hstack([4*np.ones((n_cells,1)), tri.simplices]).astype(int)
        np.savetxt(file, cells_output, fmt='%d')
        file.write("\n")
        
        # Tipos de celdas (tetraedro = 10)
        file.write(f"CELL_TYPES {n_cells}\n")
        np.savetxt(file, 10*np.ones(n_cells, dtype=int), fmt='%d')
        file.write("\n")
        
        # Datos en los puntos
        file.write(f"POINT_DATA {n_points}\n")
        file.write("SCALARS accumulated_flux float 1\n")
        file.write("LOOKUP_TABLE default\n")
        np.savetxt(file, data_values, fmt='%.6f')


import csv
import numpy as np
from scipy.spatial import Delaunay

def csv_to_vtk_delaunay(csv_file, vtk_file):
    # Leer datos del CSV
    points = []
    dose_values = []
    
    with open(csv_file, 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            points.append([float(row['x']), float(row['y']), float(row['z'])])
            dose_values.append(float(row['absorbed_dose_rate']))
    
    points = np.array(points)
    dose_values = np.array(dose_values)
    
    # Calcular triangulación de Delaunay 3D
    tri = Delaunay(points)
    
    # Escribir archivo VTK
    with open(vtk_file, 'w') as file:
        # Encabezado
        file.write("# vtk DataFile Version 2.0\n")
        file.write("Dosis absorbida - Malla Delaunay 3D\n")
        file.write("ASCII\n\n")
        
        file.write("DATASET UNSTRUCTURED_GRID\n")
        
        # Escribir puntos
        file.write(f"POINTS {len(points)} float\n")
        for p in points:
            file.write(f"{p[0]} {p[1]} {p[2]}\n")
        file.write("\n")
        
        # Escribir celdas (tetraedros)
        file.write(f"CELLS {len(tri.simplices)} {5 * len(tri.simplices)}\n")
        for simplex in tri.simplices:
            file.write(f"4 {simplex[0]} {simplex[1]} {simplex[2]} {simplex[3]}\n")
        file.write("\n")
        
        # Tipos de celdas (tetraedro = 10)
        file.write(f"CELL_TYPES {len(tri.simplices)}\n")
        file.write("\n".join(["10"] * len(tri.simplices)) + "\n\n")
        
        # Datos en los puntos
        file.write(f"POINT_DATA {len(points)}\n")
        file.write("SCALARS absorbed_dose_rate float 1\n")
        file.write("LOOKUP_TABLE default\n")
        file.write("\n".join(map(str, dose_values)) + "\n")


import numpy as np
import vtk
from vtk.util import numpy_support
from scipy.spatial import Delaunay

def create_vtk_solid_delaunay(puntos: np.ndarray, valores: np.ndarray, archivo_salida: str):
    """
    Crea un archivo VTK con un sólido utilizando triangulación Delaunay en 3D.
    
    Parámetros:
    puntos (np.ndarray): Un array de forma (n, 3) que contiene las coordenadas de los puntos 3D.
    valores (np.ndarray): Un array de forma (n, 1) que contiene los valores asociados a cada punto.
    archivo_salida (str): Nombre del archivo VTK que se generará.
    """
    # Asegurarse de que los valores estén en formato adecuado (unidimensional)
    if valores.ndim == 2 and valores.shape[1] == 1:
        valores = valores.ravel()  # Convertir la columna a un vector unidimensional

    # Generar la triangulación Delaunay en 3D (usando scipy.spatial.Delaunay)
    delaunay = Delaunay(puntos)

    # Convertir los puntos y valores a formato vtk
    vtk_puntos = numpy_support.numpy_to_vtk(puntos.ravel(), deep=True, array_type=vtk.VTK_FLOAT)
    vtk_valores = numpy_support.numpy_to_vtk(valores, deep=True, array_type=vtk.VTK_FLOAT)

    # Crear un objeto PolyData de VTK para los puntos
    polydata = vtk.vtkPolyData()

    # Crear un conjunto de puntos en vtk
    points = vtk.vtkPoints()
    for punto in puntos:
        points.InsertNextPoint(punto)

    # Asignar los puntos al objeto PolyData
    polydata.SetPoints(points)

    # Crear celdas triangulares utilizando los índices de la triangulación Delaunay
    triangles = vtk.vtkCellArray()
    for simplex in delaunay.simplices:
        triangles.InsertNextCell(3)
        for i in simplex:
            triangles.InsertCellPoint(i)
    
    # Asignar las celdas triangulares al objeto PolyData
    polydata.SetPolys(triangles)

    # Agregar los valores a los puntos como un array
    polydata.GetPointData().SetScalars(vtk_valores)

    # Crear un escritor VTK para guardar los datos en un archivo
    writer = vtk.vtkPolyDataWriter()
    writer.SetFileName(archivo_salida)
    writer.SetInputData(polydata)
    writer.Write()

    print(f"Archivo VTK con malla triangulada (Delaunay) creado con éxito: {archivo_salida}")

def generate_step_angles(start=0, stop=360, step=30):
    """
    Genera una lista de ángulos desde `start` hasta `stop - step`, con incrementos de `step`.
    
    :param start: Ángulo inicial (por defecto 0)
    :param stop: Ángulo final exclusivo (por defecto 360)
    :param step: Incremento entre ángulos (por defecto 15)
    :return: Lista de ángulos
    """
    return list(range(start, stop, step))


def best_params_flux_ratio(geometric_center, 
                           points_inside, 
                           vertices,
                           step_angles_machine, 
                           num_steps_angle_range=(1, 12), 
                           length_complete_lower_range=(-100, 0), 
                           length_complete_upper_range=(0, 100), 
                           resolution_length=50, 
                           decay_factor=0.1,
                           sources_file="sources.json",
                           length_complete_values=None,
                           n_jobs=-1):
    
    """Obtiene los valores óptimos de flux_ratio y los ángulos ideales para la máquina.
    
    Args:
        geometric_center (numpy.ndarray): Centro geométrico de la pieza.
        points_inside (numpy.ndarray): Puntos interiores de la pieza.
        vertices (numpy.ndarray): Vértices de la pieza.
        step_angles_machine (list): Lista de ángulos disponibles en la máquina.
        num_steps_angle_range (tuple): Rango semiabierto de cantidades de posiciones
            angulares equidistantes por revolución. El nombre se conserva por
            compatibilidad con el flujo original.
        
    Returns:
        tuple:
            float: Valor de flux_ratio más óptimo.
            list: Lista de ángulos disponibles en la máquina.
            list: Lista de ángulos ideales.
            list: Lista de ángulos aproximados en la máquina.
    """
    
    # Obtiene los valores óptimos de los parámetros (num_steps_angle, revolutions, num_steps_length, length_complete_lower, length_complete_upper)
    # num_steps_angle_range depende de los pasos de rotación de la máquina
    best_params, best_flux_ratio = optimize_flux_ratio(
        geometric_center, points_inside, 
        vertices, 
        num_steps_angle_range,
        length_complete_lower_range, 
        length_complete_upper_range, 
        resolution_length,
        decay_factor=decay_factor,
        sources_file=sources_file,
        length_complete_values=length_complete_values,
        n_jobs=n_jobs)
    
    # num_steps_angle_best = int(best_params[0]/best_params[1])
    num_steps_angle_best = best_params[0]
    
    # Llamada a la función
    step_angle_ideal, step_angle_degrees_best_real = calculate_best_angle_aproximate_real_world(step_angles_machine, num_steps_angle_best)
    
    return best_flux_ratio, step_angles_machine, step_angle_ideal, step_angle_degrees_best_real, best_params


def generate_optimized_flux_ratio_from_stl(cad_file, 
                                        step_angles_machine, 
                                        work_section = 'A', 
                                        num_points=10000, 
                                        angle_degrees=0, 
                                        num_steps_angle_range=(1, 12),
                                        revolutions_range=(1, 1), 
                                        num_steps_length_range=(1, 2),
                                        length_complete_lower_range=(-100, 0), 
                                        length_complete_upper_range=(0, 100), 
                                        resolution_length=50, 
                                        decay_factor=0.1,
                                        work_sections_file="work_sections.json",
                                        sources_file="sources.json",
                                        length_complete_values=None,
                                        n_jobs=-1):
    
    """Obtiene el valor de flux_ratio más óptimo para un archivo CAD en formato STL.
    
    Args:
        cad_file (str): Ruta del archivo CAD en formato STL.
        step_angles_machine (list): Lista de ángulos disponibles en la máquina.
        work_section (str): Sección de trabajo de la máquina, consultar el archivo work_sections.json.
        num_points (int): Número de puntos a generar.
        angle_degrees (int): Ángulo de rotación del CAD.
        num_steps_angle_range (tuple): Rango semiabierto de cantidades de posiciones
            angulares equidistantes por revolución.
        
    Returns:
        float: Valor de flux_ratio más óptimo.
        list: Ángulos disponibles en la máquina.
        list: Ángulos ideales.
        list: Ángulos aproximados en la máquina.
        list: Centro geométrico.
        list: Puntos interiores.
        list: Vértices.
    """

    geometric_center, points_inside, vertices = upload_cad_file(
        cad_file,
        work_section,
        num_points,
        angle_degrees,
        work_sections_file,
    )

    best_flux_ratio, step_angles_machine, step_angle_ideal, step_angle_degrees_best_real, best_params = best_params_flux_ratio(
            geometric_center, 
            points_inside, 
            vertices,
            step_angles_machine, 
            num_steps_angle_range, 
            length_complete_lower_range, 
            length_complete_upper_range, 
            resolution_length, 
            decay_factor,
            sources_file,
            length_complete_values,
            n_jobs)
    
    geometric_center = [geometric / 10 for geometric in geometric_center] # Convertir a cm
    points_inside = points_inside / 10 # Convertir a cm
    vertices = vertices / 10 # Convertir a cm
    
    return best_flux_ratio, step_angles_machine, step_angle_ideal, step_angle_degrees_best_real, geometric_center, points_inside, vertices, best_params


import json

def update_simulation_params(step_angle_degrees_best_real, geometric_center, filename, translated_length = [0, 0]):
    """
    Actualiza los parámetros en un archivo JSON, especificamente el archivo simulations_params.json, que contiene los valores para optmizar los valores de los angulos de la maquina.
    
    :param filename: Ruta del archivo JSON.
    :param step_angle_degrees_best_real: Nueva lista de ángulos óptimos.
    :param geometric_center: Nueva lista del centro geométrico.
    """
    # Cargar el archivo JSON
    with open(filename, "r") as file:
        data = json.load(file)
    
    # Modificar los valores con nuevas listas
    data["step_angle_degrees_best_real"] = step_angle_degrees_best_real
    data["translated_length"] = translated_length  # Nueva lista fija
    data["geometric_center"] = geometric_center
    
    # Guardar los cambios en el archivo JSON
    with open(filename, "w") as file:
        json.dump(data, file, indent=4)
    
    print(f"Archivo {filename} actualizado correctamente con los valores óptimos.")


import json

def update_config_json(json_filename, directory_openmc = "", file_name_stl = "world", json_file_path = "materials.json", sources_json = "sources.json", particles = 1e9):
    """
    Actualiza los parámetros en un archivo JSON de configuración de las simulaciones de OpenMC denominado config.json.
    
    :param json_filename: Ruta del archivo JSON.
    :param directory_openmc: Directorio de OpenMC del archivo STL, por defecto es "", ya que el archivo file_name_stl debe encontrarse en la misma carpeta donde se encuentra exec_openmc.py.
    :param file_name: Nombre del archivo STL.
    :param json_file_path: Ruta del archivo JSON de materiales.
    :param sources_json: Ruta del archivo JSON de fuentes.
    :param particles: Número de partículas a simular.
    """
    # Cargar el archivo JSON
    with open(json_filename, "r") as file:
        data = json.load(file)
    
    # Modificar los valores con nuevos parámetros
    data["directory_openmc"] = directory_openmc
    data["file_name"] = file_name_stl
    data["json_file_path"] = json_file_path
    data["sources_json"] = sources_json
    data["particles"] = int(particles)
    
    # Guardar los cambios en el archivo JSON
    with open(json_filename, "w") as file:
        json.dump(data, file, indent=4)
    
    print(f"Archivo {json_filename} actualizado correctamente con la nueva configuración.")


import json
import os

# def save_optimized_parameters(
#     step_angle_degrees_best_real, lower_point, upper_point,
#     filename="output/movements_parameters_optimized.json"
# ):
#     """
#     Guarda o actualiza los parámetros optimizados en un archivo JSON.

#     Parameters:
#     - step_angle_degrees_best_real: Mejor ángulo en grados encontrado en la optimización.
#     - lower_point: Punto inferior óptimo en mm.
#     - upper_point: Punto superior óptimo en mm.
#     - filename: Nombre del archivo donde se guardarán los datos (por defecto 'output/movements_parameters_optimized.json').
#     """
    
#     data = {
#         "step_angle_degrees_best_real": step_angle_degrees_best_real,
#         "step_length": [lower_point, upper_point]
#     }
    
#     # Asegura que el directorio de salida existe
#     os.makedirs(os.path.dirname(filename), exist_ok=True)
    
#     # Verifica si el archivo ya existe y carga los datos previos
#     if os.path.exists(filename):
#         with open(filename, "r", encoding="utf-8") as file:
#             try:
#                 existing_data = json.load(file)
#             except json.JSONDecodeError:
#                 existing_data = {}
#     else:
#         existing_data = {}
    
#     # Actualiza los datos existentes con los nuevos parámetros
#     existing_data.update(data)
    
#     # Guarda los datos actualizados en el archivo JSON
#     with open(filename, "w", encoding="utf-8") as file:
#         json.dump(existing_data, file, indent=4, ensure_ascii=False)
    
#     print(f"Datos de los parámetros optimizados guardados en {filename}")


# import os
# import json


# def save_optimized_parameters(
#     step_angle_degrees_best_real,
#     lower_point,
#     upper_point,
#     work_section,
#     average_dose,
#     filename="output/movements_parameters_optimized.json"
# ):
#     """
#     Guarda o actualiza los parámetros optimizados en un archivo JSON.

#     Parameters:
#     - step_angle_degrees_best_real: Mejor ángulo en grados encontrado
#       en la optimización.
#     - lower_point: Punto inferior óptimo en mm.
#     - upper_point: Punto superior óptimo en mm.
#     - work_section: Sección de trabajo, por ejemplo 'A', 'B', 'C', etc.
#     - filename: Nombre del archivo donde se guardarán los datos.
#     """

#     script_directory = os.path.dirname(os.path.abspath(__file__))

#     work_sections_file = os.path.join(
#         script_directory,
#         "work_sections.json"
#     )

#     # Verificar que exista work_sections.json
#     if not os.path.exists(work_sections_file):
#         raise FileNotFoundError(
#             f"No se encontró el archivo 'work_sections.json' en:\n"
#             f"{work_sections_file}"
#         )

#     with open(work_sections_file, "r", encoding="utf-8") as file:
#         work_sections_data = json.load(file)

#     # Obtener el diccionario "positions"
#     positions = work_sections_data.get("positions", {})

#     if work_section not in positions:
#         raise ValueError(
#             f"La work_section '{work_section}' no existe en "
#             f"work_sections.json.\n"
#             f"Secciones disponibles: {list(positions.keys())}"
#         )

#     # Obtener la posición correspondiente
#     work_section_position = positions[work_section]

#     data = {
#         "work_section": work_section,
#         "work_section_position": work_section_position,
#         "step_angle_degrees_best_real": step_angle_degrees_best_real,
#         "step_length": [
#             lower_point,
#             upper_point
#         ]
#     }

#     output_directory = os.path.dirname(filename)

#     if output_directory:
#         os.makedirs(output_directory, exist_ok=True)

#     if os.path.exists(filename):

#         with open(filename, "r", encoding="utf-8") as file:

#             try:
#                 existing_data = json.load(file)

#             except json.JSONDecodeError:
#                 existing_data = {}

#     else:
#         existing_data = {}

#     existing_data.update(data)

#     with open(filename, "w", encoding="utf-8") as file:

#         json.dump(
#             existing_data,
#             file,
#             indent=4,
#             ensure_ascii=False
#         )

#     print(
#         f"Datos de los parámetros optimizados guardados en "
#         f"{filename}"
#     )



# import os
# import json


# def save_optimized_parameters(
#     step_angle_degrees_best_real,
#     lower_point,
#     upper_point,
#     work_section,
#     average_dose,
#     filename="output/movements_parameters_optimized.json"
# ):
#     """
#     Guarda o actualiza los parámetros optimizados en un archivo JSON.

#     Parameters:
#     - step_angle_degrees_best_real: Mejor ángulo en grados encontrado
#       en la optimización.
#     - lower_point: Punto inferior óptimo en mm.
#     - upper_point: Punto superior óptimo en mm.
#     - work_section: Sección de trabajo, por ejemplo 'A', 'B', 'C', etc.
#     - average_dose: Dosis máxima acumulada, por ejemplo:
#       accumulated_fluxes_real.max().
#     - filename: Nombre del archivo donde se guardarán los datos.
#     """

#     script_directory = os.path.dirname(os.path.abspath(__file__))

#     work_sections_file = os.path.join(
#         script_directory,
#         "work_sections.json"
#     )

#     # Verificar que exista work_sections.json
#     if not os.path.exists(work_sections_file):
#         raise FileNotFoundError(
#             f"No se encontró el archivo 'work_sections.json' en:\n"
#             f"{work_sections_file}"
#         )

#     # Leer work_sections.json
#     with open(work_sections_file, "r", encoding="utf-8") as file:
#         work_sections_data = json.load(file)

#     # Obtener el diccionario "positions"
#     positions = work_sections_data.get("positions", {})

#     # Verificar que exista la work_section
#     if work_section not in positions:
#         raise ValueError(
#             f"La work_section '{work_section}' no existe en "
#             f"work_sections.json.\n"
#             f"Secciones disponibles: {list(positions.keys())}"
#         )

#     # Obtener la posición correspondiente
#     work_section_position = positions[work_section]

#     # Datos que se van a guardar
#     data = {
#         "work_section": work_section,
#         "work_section_position": work_section_position,
#         "step_angle_degrees_best_real": step_angle_degrees_best_real,
#         "step_length": [
#             lower_point,
#             upper_point
#         ],
#         "average_dose": average_dose
#     }

#     # Crear el directorio de salida si no existe
#     output_directory = os.path.dirname(filename)

#     if output_directory:
#         os.makedirs(output_directory, exist_ok=True)

#     # Leer los datos existentes
#     if os.path.exists(filename):

#         with open(filename, "r", encoding="utf-8") as file:

#             try:
#                 existing_data = json.load(file)

#             except json.JSONDecodeError:
#                 existing_data = {}

#     else:
#         existing_data = {}

#     # Actualizar los datos
#     existing_data.update(data)

#     # Guardar el JSON
#     with open(filename, "w", encoding="utf-8") as file:

#         json.dump(
#             existing_data,
#             file,
#             indent=4,
#             ensure_ascii=False
#         )

#     print(
#         f"Datos de los parámetros optimizados guardados en "
#         f"{filename}"
#     )



import os
import json


def save_optimized_parameters(
    step_angle_degrees_best_real,
    length_points,
    best_length_points,
    work_section,
    average_dose,
    description="Agregar una descripcion de la proceso/carga simulada para su posterior almacenamiento en la base de datos.",
    filename="output/movements_parameters_optimized.json"
):
    """
    Guarda o actualiza los parámetros optimizados en un archivo JSON.

    También guarda una descripción asociada a la trayectoria.
    """

    script_directory = os.path.dirname(os.path.abspath(__file__))

    work_sections_file = os.path.join(
        script_directory,
        "work_sections.json"
    )

    if not os.path.exists(work_sections_file):
        raise FileNotFoundError(
            f"No se encontró el archivo 'work_sections.json' en:\n"
            f"{work_sections_file}"
        )

    with open(work_sections_file, "r", encoding="utf-8") as file:
        work_sections_data = json.load(file)

    positions = work_sections_data.get("positions", {})

    if work_section not in positions:
        raise ValueError(
            f"La work_section '{work_section}' no existe en "
            f"work_sections.json.\n"
            f"Secciones disponibles: {list(positions.keys())}"
        )

    work_section_position = positions[work_section]

    # Construir step_length manteniendo todos los puntos
    step_length = {}

    for i, point in enumerate(length_points, start=1):

        key = (
            f"length_complete_{i}_best"
            if point in best_length_points
            else f"length_complete_{i}"
        )

        step_length[key] = point

    # Datos que se van a guardar
    data = {
        "step_angle_degrees_best_real": step_angle_degrees_best_real,
        "step_length": step_length,
        "work_section": work_section,
        "work_section_position": work_section_position,
        "average_dose": average_dose,
        "description": description
    }

    output_directory = os.path.dirname(filename)

    if output_directory:
        os.makedirs(output_directory, exist_ok=True)

    if os.path.exists(filename):

        with open(filename, "r", encoding="utf-8") as file:

            try:
                existing_data = json.load(file)

            except json.JSONDecodeError:
                existing_data = {}

    else:
        existing_data = {}

    existing_data.update(data)

    with open(filename, "w", encoding="utf-8") as file:

        json.dump(
            existing_data,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(
        f"Datos de los parámetros optimizados guardados en "
        f"{filename}"
    )
