#!/usr/bin/env python3
import numpy as np
import openmc


def focus_sources_on_target(sources, target_bounds, safety_margin_degrees=0.5):
    """Restringe cada fuente al cono que contiene toda la carga.

    El cono conserva exactamente la densidad angular isotrópica dentro del
    ángulo sólido útil: la distribución condicional se compensa multiplicando
    ``strength`` por ``Omega/(4*pi)``. La caja completa de la carga y ambos
    extremos de cada fuente lineal participan en el cálculo del semiángulo.
    """

    bounds = np.asarray(target_bounds, dtype=float)
    if bounds.shape != (2, 3) or np.any(bounds[1] <= bounds[0]):
        raise ValueError("Los límites de la carga deben tener forma (2, 3)")
    corners = np.asarray(
        [
            [x, y, z]
            for x in bounds[:, 0]
            for y in bounds[:, 1]
            for z in bounds[:, 2]
        ],
        dtype=float,
    )
    target_center = np.mean(bounds, axis=0)
    margin = np.radians(float(safety_margin_degrees))
    diagnostics = []
    for source in sources:
        lower = np.asarray(source.space.lower_left, dtype=float)
        upper = np.asarray(source.space.upper_right, dtype=float)
        source_midpoint = 0.5 * (lower + upper)
        axis = target_center - source_midpoint
        axis_norm = float(np.linalg.norm(axis))
        if axis_norm == 0.0 or np.all((source_midpoint >= bounds[0]) & (source_midpoint <= bounds[1])):
            diagnostics.append({"enabled": False, "solid_angle_fraction": 1.0})
            continue
        axis /= axis_norm
        maximum_angle = 0.0
        for source_point in (lower, upper):
            directions = corners - source_point
            norms = np.linalg.norm(directions, axis=1)
            if np.any(norms == 0.0):
                maximum_angle = np.pi
                break
            cosines = np.clip((directions @ axis) / norms, -1.0, 1.0)
            maximum_angle = max(maximum_angle, float(np.max(np.arccos(cosines))))
        half_angle = min(np.pi, maximum_angle + margin)
        if half_angle >= np.radians(175.0):
            diagnostics.append({"enabled": False, "solid_angle_fraction": 1.0})
            continue
        minimum_mu = float(np.cos(half_angle))
        solid_angle_fraction = 0.5 * (1.0 - minimum_mu)
        source.angle = openmc.stats.PolarAzimuthal(
            mu=openmc.stats.Uniform(minimum_mu, 1.0),
            phi=openmc.stats.Uniform(0.0, 2.0 * np.pi),
            reference_uvw=axis,
        )
        source.strength = float(source.strength) * solid_angle_fraction
        diagnostics.append(
            {
                "enabled": True,
                "half_angle_degrees": float(np.degrees(half_angle)),
                "solid_angle_fraction": solid_angle_fraction,
            }
        )
    return diagnostics


def rotate_sources(positions, angle_degrees, rotation_point=np.array([0, 0, 0])):
    """
    Rota las posiciones de las fuentes en el plano XY respecto a un punto de rotación dado.

    Parámetros:
    -----------
    positions : list of tuples
        Una lista de tuplas (x, y, [z1, z2]) que define las posiciones de las fuentes.
    angle_degrees : float
        El ángulo de rotación en grados.
    rotation_point : np.ndarray, opcional
        El punto de rotación (x0, y0, z0) en el plano XY. Por defecto es (0, 0, 0).

    Retorna:
    --------
    list of tuples
        Una lista de las posiciones rotadas de las fuentes.
    """
    # Convertir el ángulo de grados a radianes
    angle_radians = np.radians(angle_degrees)
    
    # Matriz de rotación
    rotation_matrix = np.array([[np.cos(angle_radians), -np.sin(angle_radians)],
                                [np.sin(angle_radians), np.cos(angle_radians)]])
    
    rotated_positions = []
    
    for pos in positions:
        x, y, z = pos
        x0, y0, z0 = rotation_point
        
        # Traslación para que el punto de rotación sea el origen
        translated_position = np.array([x - x0, y - y0])
        
        # Rotación
        rotated_position = np.dot(rotation_matrix, translated_position)
        
        # Volver a trasladar la posición al punto original
        x_rot, y_rot = rotated_position + np.array([x0, y0])
        
        # Guardar las nuevas posiciones con los mismos valores de z
        rotated_positions.append((x_rot, y_rot, z))
    
    return rotated_positions


# def add_linear_sources(positions, activities, energies=None):
#     """
#     Crea una lista de fuentes lineales para una simulación OpenMC.

#     Cada fuente se coloca en una posición específica en el espacio, con una actividad 
#     definida y energías especificadas (o energías por defecto si no se proporciona).

#     Parámetros:
#     -----------
#     positions : list of tuples
#         Una lista de tuplas (x, y, [z1, z2]) que define las posiciones de las fuentes,
#         donde x y y son fijas y z varía entre z1 y z2.
#     activities : list of floats
#         Una lista de valores de actividad para cada fuente.
#     energies : list of lists, optional
#         Una lista de listas, donde cada sublista contiene las energías (en eV) 
#         y las probabilidades correspondientes. Si no se proporciona, se usa el valor por defecto.

#     Retorna:
#     --------
#     list of openmc.Source
#         Una lista de objetos Source para cada fuente definida.
#     """
    
#     if energies is None:
#         # Energía por defecto: dos valores de energía con probabilidades 1:1
#         energies = ([1.173228e6, 1.332492e6], [0.5, 0.5])
    
#     sources = []
#     for pos, act in zip(positions, activities):
#         source = openmc.Source()
        
#         # Definimos la caja para la fuente lineal
#         source.space = openmc.stats.Box(lower_left=(pos[0], pos[1], pos[2][0]),
#                                        upper_right=(pos[0], pos[1], pos[2][1]))
        
#         # Definimos la distribución isotrópica de ángulos
#         source.angle = openmc.stats.Isotropic()
        
#         # Establecemos las energías de la fuente
#         source.energy = openmc.stats.Discrete(energies[0], energies[1])
        
#         # Establecemos la actividad de la fuente
#         source.strength = act
        
#         # Definimos el tipo de partícula
#         source.particle = 'photon'
        
#         # Agregamos la fuente a la lista
#         sources.append(source)
    
#     return sources



import openmc
import json

def add_linear_sources(
    positions,
    activities,
    radionuclide=None,
    radionuclide_data=None,
    sources_file="sources.json",
):
    """
    Crea una lista de fuentes lineales para una simulación OpenMC cargando
    las energías desde un JSON según el radionúclido especificado.

    Parámetros:
    -----------
    positions : list of tuples
        Lista de tuplas (x, y, [z1, z2]) con posiciones de las fuentes.
    activities : list of floats
        Lista de actividades en Bq para cada fuente.
    radionuclide : str, optional
        Nombre del radionúclido definido en el JSON (ej: 'Co-60', 'Cs-137').

    Retorna:
    --------
    list of openmc.Source
        Lista de objetos Source configurados con las energías del radionúclido.
    
    Lanza:
    ------
    ValueError: Si el archivo JSON no existe o el radionúclido no se encuentra.
    """
    
    if radionuclide_data is None and radionuclide:
        try:
            with open(sources_file) as f:
                config = json.load(f)
        except FileNotFoundError as error:
            raise ValueError(
                "Archivo de configuración de fuentes no encontrado: %s" % sources_file
            ) from error
        radionuclide_data = config.get("radionuclides", {}).get(radionuclide)
    
    if radionuclide and not radionuclide_data:
        raise ValueError(f"Radionúclido '{radionuclide}' no encontrado en el JSON")
    
    # Valores por defecto si no se especifica radionúclido
    if not radionuclide:
        energies_ev = [1.173228e6, 1.332492e6]  # Co-60 en eV (valor por defecto)
        probabilities = [0.5, 0.5]
    else:
        # Extraer y procesar datos
        energies_mev = [e["value"] for e in radionuclide_data["emissions"]]
        probabilities = [e["probability"] for e in radionuclide_data["emissions"]]
        
        # Conversión a eV y normalización
        energies_ev = [e * 1e6 for e in energies_mev]
        total_prob = sum(probabilities)
        probabilities = [p/total_prob for p in probabilities]

    # Crear fuentes
    sources = []
    for pos, act in zip(positions, activities):
        source = openmc.Source()
        
        # Configuración geométrica
        source.space = openmc.stats.Box(
            lower_left=(pos[0], pos[1], pos[2][0]),
            upper_right=(pos[0], pos[1], pos[2][1])
        )
        
        # Configuración física
        source.angle = openmc.stats.Isotropic()
        source.energy = openmc.stats.Discrete(energies_ev, probabilities)
        source.strength = act
        source.particle = 'photon'
        
        sources.append(source)
    
    return sources



def generate_rotated_sources(positions, activities, angle_range=(0, 360), step_range_angle=45, rotation_point=[0, 0, 0], radionuclide=None):
    """
    Genera fuentes rotadas en un intervalo de ángulos con un paso específico.

    Parámetros:
    -----------
    positions : list of tuples
        Una lista de tuplas (x, y, [z1, z2]) que define las posiciones de las fuentes.
    activities : list of floats
        Una lista de valores de actividad para cada fuente.
    angle_range : tuple, opcional
        Un intervalo de ángulos en grados (inicio, fin). Por defecto es (0, 180).
    step_range_angle : float, opcional
        El paso de rotación en grados. Por defecto es 45.
    rotation_point : opcional
        El punto de rotación (x0, y0, z0) en el plano XY. Por defecto es (0, 0, 0).

    Retorna:
    --------
    list of openmc.Source
        Una lista de objetos Source para cada fuente rotada.
    """
    
    rotation_point = np.array(rotation_point)
    
    sources = []
    
    # Generar ángulos dentro del rango especificado
    angles = np.arange(angle_range[0], angle_range[1] + step_range_angle, step_range_angle)
    
    for angle in angles:
        # Rotar las posiciones de las fuentes
        rotated_positions = rotate_sources(positions, angle, rotation_point)
        
        # Crear las fuentes lineales con las posiciones rotadas
        rotated_sources = add_linear_sources(rotated_positions, activities, radionuclide)
        
        # Agregar las fuentes rotadas a la lista de fuentes
        sources.extend(rotated_sources)
    
    return sources


def generate_rotated_sources_steps(positions, activities, angle_range=[0, 360], step_angle=4, rotation_point=[0, 0, 0], radionuclide=None):
    """
    Genera fuentes rotadas en un intervalo de ángulos con un paso específico.
    Utiliza un np.ndarray para angle_range y la operación step_range_angle = angle_range - (angle_range / step_angle).

    Parámetros:
    -----------
    positions : list of tuples
        Una lista de tuplas (x, y, [z1, z2]) que define las posiciones de las fuentes.
    activities : list of floats
        Una lista de valores de actividad para cada fuente.
    angle_range : np.ndarray, opcional
        Un array de NumPy que define el intervalo de ángulos en grados (inicio, fin). Por defecto es [0, 360].
    step_angle : float, opcional
        El paso de rotación en grados. Por defecto es 4.
    rotation_point : list, opcional
        El punto de rotación (x0, y0, z0) en el plano XY. Por defecto es [0, 0, 0].

    Retorna:
    --------
    list of openmc.Source
        Una lista de objetos Source para cada fuente rotada.
    """
    # Convertir el punto de rotación a un array de numpy
    rotation_point = np.array(rotation_point)
    
    # Convertir a array de NumPy para compatibilidad
    angle_range = np.array(angle_range)  
    
    # Calcular el rango total de ángulos
    start_angle, end_angle = angle_range
    total_range = end_angle - start_angle
    
    # Calcular step_range_angle usando la operación deseada
    step_range_angle = (total_range / step_angle)
    
    # Calcular el ángulo final relativo para el rango total para que no se realice una doble irradiación
    end_angle_relative = total_range - (total_range / step_angle)
    
    # Llamar a generate_rotated_sources con los parámetros adecuados
    sources = generate_rotated_sources(
        positions=positions,
        activities=activities,
        angle_range=(start_angle, end_angle_relative),  # Convertir a tupla para compatibilidad
        step_range_angle=step_range_angle,
        rotation_point=rotation_point,
        radionuclide = radionuclide
    )
    
    return sources


def generate_rotated_sources_steps_rev(positions, activities, revolutions=1, step_angle=4, rotation_point=[0, 0, 0], radionuclide=None):
    """
    Genera fuentes rotadas en un intervalo de ángulos que cubre múltiples revoluciones (vueltas completas).

    Parámetros:
    -----------
    positions : list of tuples
        Una lista de tuplas (x, y, [z1, z2]) que define las posiciones de las fuentes.
    activities : list of floats
        Una lista de valores de actividad para cada fuente.
    revolutions : int, opcional
        El número de revoluciones (vueltas completas) que se deben cubrir. Por defecto es 1.
    step_angle : float, opcional
        El paso de rotación en grados. Por defecto es 4.
    rotation_point : list, opcional
        El punto de rotación (x0, y0, z0) en el plano XY. Por defecto es [0, 0, 0].

    Retorna:
    --------
    list of openmc.Source
        Una lista de objetos Source para cada fuente rotada.
    """
    # Calcular el rango de ángulos en función del número de revoluciones
    angle_range = [0, 360 * revolutions]
    
    # Llamar a generate_rotated_sources_steps para generar las fuentes rotadas
    sources = generate_rotated_sources_steps(
        positions=positions,
        activities=activities,
        angle_range=angle_range,
        step_angle=step_angle,
        rotation_point=rotation_point,
        radionuclide=radionuclide
    )
    
    return sources


def generate_rotated_translated_sources_steps_rev(positions, activities, revolutions=1, step_angle=4, translated_length=[0], rotation_point=[0, 0, 0], radionuclide=None):
    """
    Crea fuentes rotadas y trasladadas en el espacio 3D.

    Esta función toma una lista de posiciones base, las traslada en el eje z según los valores especificados
    en `translated_length`, y luego genera fuentes rotadas alrededor de un punto de rotación dado.

    Parámetros:
    -----------
    positions : list of tuples
        Lista de posiciones de las fuentes en formato (x, y, [z_inicio, z_fin]).

    activities : list of float
        Lista de actividades asociadas a cada posición base.

    revolutions : int, opcional
        Número de revoluciones completas para la rotación (por defecto 1).

    step_angle : float, opcional
        Paso de rotación en grados (por defecto 4).

    translated_length : list of float, opcional
        Valores de traslación en el eje z (por defecto [0]).
        La función aplicará la traslación solo en los valores especificados en esta lista.

    rotation_point : list of float, opcional
        Punto de rotación en el espacio 3D (por defecto [0, 0, 0]).

    Retorna:
    --------
    sources : list of dict
        Lista de fuentes generadas, donde cada fuente es un diccionario con las claves:
        - 'position': Tupla (x, y, z) que representa la posición de la fuente.
        - 'activity': Actividad asociada a la fuente.
    """
    sources = []
    
    # Aplicar la traslación en el eje z (solo en los valores especificados)
    for z_translate in translated_length:
        translated_positions = []
        for pos in positions:
            x, y, z_range = pos
            # Trasladar ambos valores de z
            z_new_start = z_range[0] + z_translate
            z_new_end = z_range[1] + z_translate
            translated_positions.append((x, y, [z_new_start, z_new_end]))  # Nuevo rango en z
        
        # Generar las fuentes rotadas después de la traslación
        rotated_sources = generate_rotated_sources_steps_rev(translated_positions, activities, revolutions, step_angle, rotation_point=rotation_point, radionuclide=radionuclide)
        
        sources.extend(rotated_sources)
    
    return sources


def generate_rotated_sources_range(
    positions,
    activities,
    angle_range,
    rotation_point=[0, 0, 0],
    radionuclide=None,
    radionuclide_data=None,
):
    """
    Genera fuentes rotadas en un intervalo de ángulos con un paso específico.

    Parámetros:
    -----------
    positions : list of tuples
        Una lista de tuplas (x, y, [z1, z2]) que define las posiciones de las fuentes.
    activities : list of floats
        Una lista de valores de actividad para cada fuente.
    angle_range : list of floats
        Una lista de ángulos en grados. 
    rotation_point : opcional
        El punto de rotación (x0, y0, z0) en el plano XY. Por defecto es (0, 0, 0).

    Retorna:
    --------
    list of openmc.Source
        Una lista de objetos Source para cada fuente rotada.
    """
    
    rotation_point = np.array(rotation_point)
    
    sources = []
    
    # Generar ángulos dentro del rango especificado
    angles = np.array(angle_range)
    
    for angle in angles:
        # Rotar las posiciones de las fuentes
        rotated_positions = rotate_sources(positions, angle, rotation_point)
        
        # Crear las fuentes lineales con las posiciones rotadas
        rotated_sources = add_linear_sources(
            rotated_positions,
            activities,
            radionuclide=radionuclide,
            radionuclide_data=radionuclide_data,
        )
        
        # Agregar las fuentes rotadas a la lista de fuentes
        sources.extend(rotated_sources)
    
    return sources



def generate_rotated_translated_sources_range(
    positions,
    activities,
    angle_range,
    translated_length=[0, 0],
    rotation_point=[0, 0, 0],
    radionuclide=None,
    radionuclide_data=None,
):
    """
    Crea fuentes rotadas y trasladadas en el espacio 3D.

    Esta función toma una lista de posiciones base, las traslada en el eje z según los valores especificados
    en `translated_length`, y luego genera fuentes rotadas alrededor de un punto de rotación dado.

    Parámetros:
    -----------
    positions : list of tuples
        Lista de posiciones de las fuentes en formato (x, y, [z_inicio, z_fin]).

    activities : list of float
        Lista de actividades asociadas a cada posición base.

    angle_range : list of floats
        Una lista de ángulos en grados.

    translated_length : list of float, opcional
        Valores de traslación en el eje z (por defecto [0]).
        La función aplicará la traslación solo en los valores especificados en esta lista.

    rotation_point : list of float, opcional
        Punto de rotación en el espacio 3D (por defecto [0, 0, 0]).

    Retorna:
    --------
    sources : list of dict
        Lista de fuentes generadas, donde cada fuente es un diccionario con las claves:
        - 'position': Tupla (x, y, z) que representa la posición de la fuente.
        - 'activity': Actividad asociada a la fuente.
    """
    sources = []
    
    translated_length_values = np.asarray(translated_length, dtype=float).reshape(-1)
    if translated_length_values.size == 0:
        raise ValueError("Debe existir al menos una posición longitudinal")
    # Una configuración puede contener un solo punto o repetir los extremos
    # cuando el intervalo está colapsado. Cada posición física se simula una
    # sola vez y se conserva el orden definido por el usuario.
    translated_length_values = np.asarray(
        list(dict.fromkeys(float(value) for value in translated_length_values)),
        dtype=float,
    )
    
    # Aplicar la traslación en el eje z (solo en los valores especificados)
    for z_translate in translated_length_values:
        translated_positions = []
        for pos in positions:
            x, y, z_range = pos
            # Trasladar ambos valores de z
            z_new_start = z_range[0] + z_translate
            z_new_end = z_range[1] + z_translate
            translated_positions.append((x, y, [z_new_start, z_new_end]))  # Nuevo rango en z
        
        # Generar las fuentes rotadas después de la traslación
        rotated_sources = generate_rotated_sources_range(
            translated_positions,
            activities,
            angle_range,
            rotation_point=rotation_point,
            radionuclide=radionuclide,
            radionuclide_data=radionuclide_data,
        )
        sources.extend(rotated_sources)
    
    return sources


import json
from datetime import datetime
import numpy as np

def add_sources_with_decay(sources_data, decay_factor=0.1):
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
        Retorna dos listas:
        - positions : lista de tuplas con las posiciones (x, y, [z1, z2]).
        - activities : lista de intensidades decaídas de las fuentes.
    """
    # Obtener la fecha actual del sistema
    current_date = datetime.today().strftime('%Y-%m-%d')

    positions = []
    activities = []

    # Calcular el decaimiento para cada fuente
    for source in sources_data:
        lower_point = [coord / 10 for coord in source["lower_point"]] # Convertir a cm
        upper_point = [coord / 10 for coord in source["upper_point"]] # Convertir a cm

        initial_reading_date = datetime.strptime(source["initial_reading_date"], "%Y-%m-%d")
        intensity = source["intensity"]

        # Calcular el tiempo transcurrido desde la fecha de lectura inicial
        time_diff = (datetime.strptime(current_date, "%Y-%m-%d") - initial_reading_date).days

        # Aplicar el decaimiento de la intensidad
        decayed_intensity = intensity * np.exp(-decay_factor * time_diff)

        
        # Crear la posición como una tupla (x, y, [z1, z2])
        position = (lower_point[0], lower_point[1], [lower_point[2], upper_point[2]])

        # Añadir la posición y la intensidad decaída a las listas correspondientes
        positions.append(position)
        activities.append(decayed_intensity)
    
    return positions, activities


import json
import numpy as np

def get_theoretical_montecarlo_params(
    radionuclide="Co-60",
    material_name="water",
    source_file="source.json",
    materials_file="materials.json"
):
    """
    Calcula parámetros para simulaciones Monte Carlo:
    - decay_factor (λ) basado en la vida media del radionúclido
    - Coeficiente de absorción efectivo (μ_en) considerando todas las emisiones

    Args:
        radionuclide (str): Nombre del radionúclido (ej: "Cs-137")
        material_name (str): Material objetivo (ej: "air")
        source_file (str): Ruta al JSON con datos de radionúclidos
        materials_file (str): Ruta al JSON con datos de materiales

    Returns:
        tuple: (decay_factor, dose_factor)
    """
    
    # Leer datos del radionúclido
    with open(source_file, 'r', encoding='utf-8') as f:
        source_data = json.load(f)
    
    # Validar y obtener datos del radionúclido
    nuclide_data = source_data["radionuclides"].get(radionuclide)
    if not nuclide_data:
        raise ValueError(f"Radionúclido {radionuclide} no encontrado en {source_file}")
    
    half_life = nuclide_data["half_life"]
    emissions = nuclide_data["emissions"]
    
    # Calcular decay_factor
    decay_factor = np.log(2) / half_life
    
    # Leer datos del material
    with open(materials_file, 'r', encoding='utf-8') as f:
        materials_data = json.load(f)
    
    # Buscar el material especificado
    material = next(
        (m for m in materials_data["materials"] if m["name"] == material_name), 
        None
    )
    if not material:
        raise ValueError(f"Material {material_name} no encontrado en {materials_file}")
    
    # Extraer propiedades del material
    density = material["density"]
    mu_en_over_rho = material["mu_en_over_rho"]
    
    # Calcular μ_en efectivo
    dose_factor = 0.0
    for emission in emissions:
        energy = emission["value"]
        energy_str = f"{energy:.3f}"  # Formatear a 3 decimales
        
        if energy_str not in mu_en_over_rho:
            raise ValueError(f"Energía {energy} MeV no encontrada en {material_name}")
        
        probability = emission["probability"]
        dose_factor += probability * mu_en_over_rho[energy_str] * energy * 3.6 * 1.6022 * 10**(-10) # Factor de conversion de MeV/s a kGy/h
        # dose_factor += 1 # Resultado eV/s para heating
    
    return decay_factor, dose_factor
