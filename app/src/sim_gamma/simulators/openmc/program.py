
from .source_model import *
from sim_gamma.physics.deterministic.optimizer import *
# from theoretical_calc_optimizer_graph_gen import *
# Calculo de la tasa de dosis absorbida usando metodos de montecarlo

# step_length_range_mc = [best_params[3]/10, best_params[4]/10]
# print("Rango de longitud de paso en cm para M. Monte Carlo:", step_length_range_mc)

# update_simulation_params(step_angle_degrees_best_real, geometric_center, filename='simulations_params.json', translated_length = step_length_range_mc)

# update_config_json(json_filename='config.json', file_name_stl= file_name_step, particles= particles)

# from exec_openmc import run_openmc_simulation

# cell_size = cell_size

# run_openmc_simulation(decay_factor=decay_factor, radionuclide=radionuclide, cell_size=cell_size)


# from data_processing_montecarlo import *

# # Procesar los valores de tasa de dosis absorbida obtenidos cn el metodo de Monte Carlo y lo convierte a un archivo csv
# process_and_translate_tallies(cell_size, output_file=output_file_mc_csv, dose_coeff=dose_factor)

# # A partir de los valores de tasa de dosis absorbida obtenidos con el metodo de Monte Carlo se filtran los valores que se encuentran dentro del volumen cad_file_stl_load y se convierten a un archivo csv
# points_inside_moved_mc, fluxes_mc, flux_ratio_mc = process_mesh_and_filter(
#     csv_file = output_file_mc_csv, 
#     stl_file = cad_file_stl_load, 
#     output_csv = output_file_mc_csv_load, 
#     target_position = geometric_center, 
#     cell_size=cell_size)


# plot_isodose_map(points_inside_moved=points_inside_moved_mc, 
#                 fluxes=fluxes_mc, 
#                 flux_ratio=flux_ratio_mc,
#                 flux_max=fluxes_mc.max(), 
#                 flux_min=fluxes_mc.min(), 
#                 points_view=True, 
#                 title="Mapa de Isodosis - Simulaciones de Monte Carlo")


# # Convierte el archivo csv de la tasa de dosis absorbida para el volumen a un archivo vtk
# csv_to_vtk_delaunay(output_file_mc_csv_load, output_file_mc_csv_load_vtk)


# # Imprime los valores maximos, minimos y sus posiciones de la tasa de dosis absorbida obtenidos con el metodo de Monte Carlo
# result = find_extreme_dose_rates(output_file_mc_csv_load)

# montecarlo_program.py

import json
from .source_model import *
from sim_gamma.physics.deterministic.optimizer import *
from .engine import run_openmc_simulation

def load_json(filename):
    """Carga los parámetros desde un archivo JSON."""
    with open(filename, 'r') as file:
        return json.load(file)

def setup_openmc_simulation(json_file):
    """Configura y ejecuta la simulación OpenMC."""
    # Cargar los parámetros desde el archivo JSON
    # params = load_json('optimizacion_resultados.json')
    params = load_json(json_file)
    
    radionuclide = params["radionuclide"]
    file_name_step = params["file_name_step"]
    particles = params["particles"]
    cell_size = params["cell_size"]
    step_angle_degrees_best_real = params["step_angle_degrees_best_real"]
    geometric_center = params["geometric_center"]
    best_params = params["best_params"]
    decay_factor = params["decay_factor"]
    cpu_cores = int(params.get("cpu_cores", -1))
    
    step_length_range_mc = [best_params[3] / 10, best_params[4] / 10]
    print("Rango de longitud de paso en cm para M. Monte Carlo:", step_length_range_mc)
    
    update_simulation_params(
        step_angle_degrees_best_real, geometric_center,
        filename='simulations_params.json', translated_length=step_length_range_mc
    )
    
    update_config_json(
        json_filename='config.json', file_name_stl=file_name_step, particles=particles
    )
    
    run_openmc_simulation(
        decay_factor=decay_factor,
        radionuclide=radionuclide,
        cell_size=cell_size,
        cpu_cores=cpu_cores,
    )

def run_simulation(json_file):
    """Ejecuta y valida la simulación."""
    setup_openmc_simulation(json_file)

# if __name__ == "__main__":
#     run_simulation()

if __name__ == "__main__":
    import sys
    json_file = sys.argv[1] if len(sys.argv) > 1 else "optimizacion_resultados.json"
    run_simulation(json_file)
