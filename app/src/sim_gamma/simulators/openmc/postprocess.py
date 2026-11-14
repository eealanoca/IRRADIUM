import re

def process_and_translate_tallies(cell_size, output_file, dose_coeff=1, file_path = 'tallies.out'):
    """
    Procesa el archivo tallies.out para extraer los datos de flujo, convertirlos en tasa de dosis absorbida,
    aplicar la traslación y guardar los datos traducidos en un archivo CSV.
    """
    # Leer el archivo
    with open(file_path, 'r') as file:
        lines = file.readlines()

    mesh_data = []
    mesh_index_pattern = r"Mesh Index \((\d+), (\d+), (\d+)\)"
    flux_rate_pattern = r"Flux\s+([\d.eE+-]+)"
    current_mesh_index = None

    for line in lines:
        mesh_index_match = re.search(mesh_index_pattern, line)
        if mesh_index_match:
            current_mesh_index = tuple(map(int, mesh_index_match.groups()))
            continue

        flux_rate_match = re.search(flux_rate_pattern, line)
        if flux_rate_match and current_mesh_index:
            flux_rate = float(flux_rate_match.group(1))
            x, y, z = current_mesh_index   
            mesh_data.append((x * cell_size, y * cell_size, z * cell_size, flux_rate / cell_size**3)) # Se escalan los puntos al mesh y se escala el flujo al volumen de la celda
            current_mesh_index = None
    
    # Calcular traslación
    x_values = [x for x, _, _, _ in mesh_data]
    y_values = [y for _, y, _, _ in mesh_data]
    z_values = [z for _, _, z, _ in mesh_data]
    
    x_center = ((max(x_values) + min(x_values)) / 2)
    y_center = ((max(y_values) + min(y_values)) / 2)
    z_center = ((max(z_values) + min(z_values)) / 2)
    
    # Aplicar traslación y conversión
    mesh_data_translate = []
    for x, y, z, flux in mesh_data:
        x_new = x - x_center
        y_new = y - y_center
        z_new = z - z_center
        flux = flux * dose_coeff # Conversión de flujo a tasa de dosis absorbida en kGy/h
        mesh_data_translate.append((x_new, y_new, z_new, flux))
    
    # Guardar en CSV
    with open(output_file, 'w') as file:
        file.write("x,y,z,absorbed_dose_rate\n")
        for x, y, z, flux in mesh_data_translate:
            file.write(f"{x},{y},{z},{flux}\n")


import pandas as pd
import numpy as np
from stl import mesh
import trimesh

def process_mesh_and_filter(csv_file, stl_file, output_csv, target_position, cell_size=2):
    """
    Carga un archivo CSV con puntos y un archivo STL con una malla, 
    mueve la malla a una posición específica en el espacio, filtra los puntos dentro del volumen de la malla
    y calcula el ratio de flujo.
    
    Parámetros:
        csv_file (str): Ruta del archivo CSV con los datos de puntos.
        stl_file (str): Ruta del archivo STL de la malla.
        output_csv (str): Ruta donde se guardará el archivo CSV filtrado.
        target_position (tuple): Coordenadas (x, y, z) donde se ubicará el centro de la malla.
        cell_size (float): Tamaño de la celda en cm (por defecto 4 cm).
    
    Retorna:
        float: Ratio de flujo (máximo/mínimo).
    """
    
    # Cargar datos del archivo CSV
    data = pd.read_csv(csv_file)
    
    # Cargar el archivo STL
    solid_mesh = mesh.Mesh.from_file(stl_file)
    vertices = solid_mesh.vectors.reshape(-1, 3) / 10  # Convertir a cm
    
    # Calcular el centro de la malla original
    mesh_center = vertices.mean(axis=0)
    
    # Calcular la traslación necesaria para mover la malla al target_position
    translation = np.array(target_position) - mesh_center
    
    # Aplicar la traslación a los vértices
    vertices += translation
    
    # Crear el objeto de malla con trimesh
    tri_mesh = trimesh.Trimesh(vertices=vertices, faces=np.arange(len(vertices)).reshape(-1, 3))
    
    # Filtrar puntos dentro del volumen
    points = data[['x', 'y', 'z']].to_numpy()
    inside_mask = tri_mesh.contains(points)
    
    # Filtrar los puntos dentro del volumen
    filtered_data = data[inside_mask]
    
    # Extraer coordenadas y flujos del DataFrame
    points_inside_moved = filtered_data[['x', 'y', 'z']].to_numpy() # Coordenadas x, y, z
    fluxes = filtered_data['absorbed_dose_rate'].to_numpy().reshape(-1, 1) #/ cell_size**3
    
    # Calcular flux_ratio
    flux_ratio = fluxes.max() / fluxes.min() if len(fluxes) > 0 and fluxes.min() > 0 else None
    
    # Guardar los resultados en un nuevo archivo CSV
    filtered_data.to_csv(output_csv, index=False)
    print(f"Filtrado completo. Los puntos dentro del volumen se guardaron en '{output_csv}'.")
    
    return points_inside_moved, fluxes, flux_ratio


import pandas as pd

def find_extreme_dose_rates(csv_file):
    # Cargar el CSV
    df = pd.read_csv(csv_file)
    
    # Encontrar la fila con el valor máximo de absorbed_dose_rate
    max_index = df["absorbed_dose_rate"].idxmax()
    max_value = df.loc[max_index, "absorbed_dose_rate"]
    max_position = df.loc[max_index, ["x", "y", "z"]].to_list()
    
    # Encontrar la fila con el valor mínimo de absorbed_dose_rate
    min_index = df["absorbed_dose_rate"].idxmin()
    min_value = df.loc[min_index, "absorbed_dose_rate"]
    min_position = df.loc[min_index, ["x", "y", "z"]].to_list()
    
    # Imprimir resultados
    print(f"Valor más alto: {max_value} [kGy/h], Posición: {max_position} [cm]")
    print(f"Valor más bajo: {min_value} [kGy/h], Posición: {min_position} [cm]")
    
    return {
        "max_value": max_value,
        "max_position": max_position,
        "min_value": min_value,
        "min_position": min_position
    }
