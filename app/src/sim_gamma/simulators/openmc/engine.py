#!/usr/bin/env python3

# import os

# def generate_openmc_files(directory_openmc, file_name):
#     """
#     Esta función genera los archivos .h5m y .step para OpenMC a partir de un archivo STEP 
#     utilizando el módulo CAD_to_OpenMC. Si el archivo .h5m ya existe, no se genera de nuevo.

#     Parámetros:
#     directory_openmc (str): El directorio donde se encuentran los archivos de entrada 
#                             y donde se guardarán los archivos generados.
#     file_name (str): El nombre base del archivo (sin extensión), utilizado para los 
#                      archivos .h5m y .step.

#     Procedimiento:
#     La función verifica si el archivo .h5m ya existe en el directorio especificado. Si no existe, 
#     la función carga el archivo STEP correspondiente y genera el archivo .h5m utilizando 
#     el backend 'stl2' y la opción de fusionar los archivos.
#     """
    
#     # Generar las rutas completas para los archivos .h5m y .step
#     h5m_file = os.path.join(directory_openmc, f'{file_name}.h5m')
#     step_file = os.path.join(directory_openmc, f'{file_name}.step')

#     try:
#         import CAD_to_OpenMC.assembly as ab
        
#         # Verificar si el archivo .h5m ya existe
#         if not os.path.exists(h5m_file):
#             try:
#                 WORLD = ab.Assembly([step_file])
#                 WORLD.run(backend='stl2', merge=True, h5m_filename=h5m_file)
#                 print(f"Archivo '{h5m_file}' creado exitosamente.")
#             except Exception as e:
#                 print(f"Error al generar '{h5m_file}': {e}")
#         else:
#             print(f"El archivo '{h5m_file}' ya existe. No es necesario generarlo nuevamente.")
    
#     except ImportError as e:
#         print(f"Error al importar CAD_to_OpenMC: {e}")

import os
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np


CAD_PYTHON = Path(os.environ.get(
    "IRRADIUM_CAD_PYTHON",
    str(Path(__file__).resolve().parents[5] / "runtime/cad/bin/python"),
))


def _environment_runner():
    """Use IRRADIUM's private CAD interpreter without a system environment manager."""
    if not CAD_PYTHON.is_file():
        raise RuntimeError("Falta el runtime CAD privado de IRRADIUM; ejecuta irradium install")
    return str(CAD_PYTHON)


def _run_isolated_cad_converter(directory_openmc, file_name, material_tags=None, implicit_air=False):
    runner = _environment_runner()
    project_root = Path(__file__).resolve().parents[4]
    converter = project_root / "src" / "sim_gamma" / "workers" / "cad_converter_cli.py"
    command = [
        runner,
        str(converter),
        str(Path(directory_openmc).resolve()),
        file_name,
    ]
    if material_tags or implicit_air:
        command.append(json.dumps(list(material_tags or [])))
    if implicit_air:
        command.append("air")
    environment = os.environ.copy()
    prefix = CAD_PYTHON.parent.parent
    environment["CONDA_PREFIX"] = str(prefix)
    environment["PATH"] = str(prefix / "bin") + os.pathsep + "/usr/local/bin:/usr/bin:/bin"
    environment["LD_LIBRARY_PATH"] = str(prefix / "lib")
    completed = subprocess.run(
        command,
        env=environment,
        cwd=str(Path(directory_openmc).resolve()),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise RuntimeError(
            "No se pudo convertir el STEP a DAGMC en el entorno "
            f"{CAD_PYTHON} (código {completed.returncode})"
        )


def generate_openmc_files(directory_openmc, file_name, material_tags=None, implicit_air=False):
    """Build atomically; reuse only a mesh matching the STEP and exterior medium."""
    from tempfile import TemporaryDirectory
    from .geometry_contract import geometry_signature, cached_geometry_matches, require_air_complement

    directory = Path(directory_openmc).resolve()
    step = next((directory / f"{file_name}{suffix}" for suffix in (".step", ".STEP")
                 if (directory / f"{file_name}{suffix}").is_file()), None)
    if step is None:
        raise FileNotFoundError(f"No se encontró '{file_name}' con extensión .step o .STEP en {directory}")
    destination = directory / f"{file_name}.h5m"
    signature = geometry_signature(step, material_tags, implicit_air)
    if cached_geometry_matches(destination, signature):
        return
    with TemporaryDirectory(prefix=".geometry-", dir=directory) as temporary:
        staging = Path(temporary)
        staged_step = staging / f"{file_name}.step"
        shutil.copyfile(step, staged_step)
        try:
            import CAD_to_OpenMC.assembly as ab
            from pymoab import core as _pymoab_core  # noqa: F401
        except ImportError:
            _run_isolated_cad_converter(
                staging, file_name, material_tags=material_tags, implicit_air=implicit_air,
            )
        else:
            world = ab.Assembly([str(staged_step)], implicit_complement="air" if implicit_air else None)
            options = {"sequential_tags": list(material_tags)} if material_tags else {}
            world.run(backend="stl2", merge=True, h5m_filename=str(staging / destination.name), **options)
        generated = staging / destination.name
        if not generated.is_file():
            raise FileNotFoundError(f"El conversor CAD finalizó sin generar '{destination.name}'")
        if implicit_air:
            require_air_complement(generated)
        generated.replace(destination)
        record = destination.with_suffix(".geometry.json")
        pending = record.with_suffix(".json.tmp")
        pending.write_text(json.dumps(signature, sort_keys=True))
        pending.replace(record)


# import openmc
# import json
# import os

# def create_materials(json_file_path):
#     """
#     Esta función carga los datos de materiales desde un archivo JSON, crea los materiales 
#     y los guarda en un archivo XML en el directorio especificado.

#     Parámetros:
#     directory_openmc (str): El directorio donde se exportarán los archivos XML 
#                             de los materiales creados.
#     json_file_path (str): La ruta al archivo JSON que contiene la información de los materiales. 
#                           El archivo debe tener una estructura similar a la siguiente:
#                           [
#                               {
#                                   "name": "air",
#                                   "elements": {"C": 0.000124, "N": 0.755268, "O": 0.231781, "Ar": 0.012827},
#                                   "density": 0.001205
#                               },
#                               {
#                                   "name": "water",
#                                   "elements": {"H": 2, "O": 1},
#                                   "density": 1.0
#                               }
#                           ]

#     Procedimiento:
#     La función carga los datos de materiales desde el archivo JSON, crea un material 
#     para cada entrada y luego exporta todos los materiales a un archivo XML en el directorio especificado.
#     """
    
#     # Cargar los datos desde el archivo JSON
#     try:
#         with open(json_file_path, 'r') as f:
#             materials_data = json.load(f)
#     except FileNotFoundError:
#         print(f"Error: El archivo '{json_file_path}' no fue encontrado.")
#         return
#     except json.JSONDecodeError:
#         print("Error: El archivo JSON tiene un formato incorrecto.")
#         return
    
#     # Crear los materiales
#     materials = openmc.Materials()
    
#     for material_data in materials_data:
#         material = openmc.Material(name=material_data['name'])
        
#         for element, fraction in material_data['elements'].items():
#             material.add_element(element, fraction)
        
#         material.set_density('g/cm3', material_data['density'])
#         materials.append(material)
    
#     # Exportar los materiales a un archivo XML
#     materials.export_to_xml()
    
#     return materials


import openmc
import math

from sim_gamma.compute_resources import resolve_cpu_threads


class CompactRegularMeshFilter(openmc.MeshFilter):
    """MeshFilter equivalente que no materializa millones de tuplas Python."""

    def __init__(self, mesh, filter_id=None):
        if not isinstance(mesh, openmc.RegularMesh):
            raise TypeError("CompactRegularMeshFilter requiere openmc.RegularMesh")
        self._mesh = mesh
        self.id = filter_id
        self._translation = None

    @property
    def num_bins(self):
        return int(self.mesh.num_mesh_cells)

    def to_xml_element(self):
        from lxml import etree as ET

        element = ET.Element("filter")
        element.set("id", str(self.id))
        element.set("type", "mesh")
        bins = ET.SubElement(element, "bins")
        bins.text = str(self.mesh.id)
        if self.translation is not None:
            element.set("translation", " ".join(map(str, self.translation)))
        return element
import json
import os

def create_materials(json_file_path):
    """
    Esta función carga los datos de materiales desde un archivo JSON, crea los materiales 
    y los guarda en un archivo XML.

    Parámetros:
    json_file_path (str): La ruta al archivo JSON que contiene la información de los materiales. 
                          El archivo debe tener una estructura similar a la siguiente:
                          {
                              "materials": [
                                  {
                                      "name": "air",
                                      "elements": {"C": 0.000124, "N": 0.755268, "O": 0.231781, "Ar": 0.012827},
                                      "density": 0.001205,
                                      "mu_en_over_rho": {"0.662": 0.0296, "1.173": 0.0266, "1.332": 0.0258}
                                  },
                                  {
                                      "name": "water",
                                      "elements": {"H": 0.111894, "O": 0.888106},
                                      "density": 1.0,
                                      "mu_en_over_rho": {"0.662": 0.0320, "1.173": 0.0290, "1.332": 0.0280}
                                  }
                              ]
                          }

    Procedimiento:
    La función carga los datos de materiales desde el archivo JSON, crea un material 
    para cada entrada y luego exporta todos los materiales a un archivo XML.
    """
    
    # Cargar los datos desde el archivo JSON
    try:
        with open(json_file_path, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: El archivo '{json_file_path}' no fue encontrado.")
        return
    except json.JSONDecodeError:
        print("Error: El archivo JSON tiene un formato incorrecto.")
        return
    
    # Crear los materiales
    materials = openmc.Materials()
    
    for material_data in data.get("materials", []):
        material = openmc.Material(name=material_data['name'])
        
        for element, fraction in material_data['elements'].items():
            material.add_element(element, fraction)
        
        material.set_density('g/cm3', material_data['density'])
        
        materials.append(material)
    
    # Exportar los materiales a un archivo XML
    materials.export_to_xml()
    
    return materials




import openmc

def regular_mesh_parameters(bounds, cell_size):
    """Ajusta una malla regular al volumen solicitado conservando el tamaño de celda."""
    if cell_size <= 0:
        raise ValueError("El tamaño de celda debe ser mayor que cero")
    lower = [float(value) for value in bounds[0]]
    upper = [float(value) for value in bounds[1]]
    if len(lower) != 3 or len(upper) != 3 or any(b <= a for a, b in zip(lower, upper)):
        raise ValueError("Los límites de la malla deben definir un volumen tridimensional")
    dimensions = [max(1, math.ceil((b - a) / cell_size)) for a, b in zip(lower, upper)]
    center = [(a + b) / 2.0 for a, b in zip(lower, upper)]
    half_extent = [dimension * cell_size / 2.0 for dimension in dimensions]
    adjusted_lower = [value - half for value, half in zip(center, half_extent)]
    adjusted_upper = [value + half for value, half in zip(center, half_extent)]
    return dimensions, adjusted_lower, adjusted_upper


def fixed_source_batch_plan(total_particles, preferred_batches=20):
    """Divide el total de historias en lotes exactos para estimar varianza."""

    total_particles = int(total_particles)
    if total_particles < 1:
        raise ValueError("El número total de partículas debe ser positivo")
    maximum = min(int(preferred_batches), total_particles)
    for batches in range(maximum, 0, -1):
        if total_particles % batches == 0:
            return batches, total_particles // batches
    return 1, total_particles


def adaptive_fixed_source_batch_plan(
    total_particles,
    mesh_cells,
    preferred_max_batches=None,
):
    """Planifica un máximo de historias con puntos de control estadístico.

    El número solicitado conserva su significado de presupuesto máximo. Las
    mallas grandes usan menos lotes para limitar el coste de reducir tallies
    entre hilos. Se conservan al menos 30 realizaciones cuando el presupuesto
    lo permite para que la estimación de varianza no dependa de una muestra
    estadística demasiado corta.
    """

    # MAGIC necesita varias actualizaciones tempranas independientemente del
    # presupuesto máximo. Un millón de historias por realización permitió que
    # la ventana fina aprendiera las regiones profundas antes de consumir el
    # presupuesto, sin una penalización apreciable por reducción de tallies.
    if preferred_max_batches is None:
        base_batches = 50 if int(mesh_cells) >= 1_000_000 else 100
        preferred_max_batches = max(
            base_batches,
            int(math.ceil(int(total_particles) / 1_000_000)),
        )
    preferred_max_batches = min(max(2, int(preferred_max_batches)), 10_000)
    maximum_batches, particles_per_batch = fixed_source_batch_plan(
        total_particles,
        preferred_max_batches,
    )
    minimum_batches = min(30, maximum_batches)
    return minimum_batches, maximum_batches, particles_per_batch


def create_geometry_tally(
    h5m_filename='world.h5m',
    universe_id=1,
    cell_size=2,
    mesh_bounds=None,
    geometry_translation=None,
    evaluation_cell_size=None,
    return_evaluation_mesh=False,
    tally_score='flux',
    evaluation_relative_error_target=None,
    heating_materials=None,
    include_direct_heating_diagnostic=False,
    transport_bounds=None,
    load_instances=None,
    surrounding_material=None,
    geometry_rotation_degrees=0,
    analytic_load_material=None,
    exact_mesh_bounds=False,
):
    """
    Esta función configura la geometría de OpenMC a partir de un archivo H5M y exporta la geometría a un archivo XML.

    Parámetros:
    h5m_filename (str): El nombre del archivo H5M que contiene el modelo de geometría.
    universe_id (int): El ID del universo que se utilizará.
    output_directory (str): El directorio donde se exportará el archivo XML de la geometría creada.
    """
    
    if analytic_load_material is not None:
        from .analytic_box import rectangular_geometry
        if mesh_bounds is None or transport_bounds is None or load_instances is not None:
            raise ValueError("La geometría analítica requiere una única caja y límites explícitos.")
        geometry = rectangular_geometry(mesh_bounds, transport_bounds, analytic_load_material, surrounding_material)
    else:
        # Definición del mundo creado en el archivo h5m
        world_dagmc = openmc.DAGMCUniverse(filename=h5m_filename, auto_geom_ids=True, universe_id=universe_id)

        # Definición de la región
        world_region = world_dagmc.bounding_region()  # Región dentro del CAD
        translation_values = (0, 0, 0) if geometry_translation is None else geometry_translation
        translation = [float(value) for value in translation_values]
        if len(translation) != 3:
            raise ValueError("La traslación DAGMC debe contener X, Y y Z")
        if transport_bounds is not None:
            world_region = None
            for axis, plane in enumerate((openmc.XPlane, openmc.YPlane, openmc.ZPlane)):
                coordinate = ("x0", "y0", "z0")[axis]
                lower = plane(**{coordinate: transport_bounds[0][axis]}, boundary_type="vacuum")
                upper = plane(**{coordinate: transport_bounds[1][axis]}, boundary_type="vacuum")
                slab = +lower & -upper
                world_region = slab if world_region is None else world_region & slab
        elif any(value != 0 for value in translation):
            world_region = world_region.translate(translation)

        # Definición de las celdas
        world_cell = openmc.Cell(fill=world_dagmc, region=world_region)
        if any(value != 0 for value in translation):
            world_cell.translation = translation
        if geometry_rotation_degrees:
            world_cell.rotation = (0, 0, geometry_rotation_degrees)

        # Definición de la geometría
        if load_instances is None:
            geometry = openmc.Geometry([world_cell])
        else:
            cells = []
            uncovered = world_region
            for instance in load_instances:
                box = None
                for axis, plane in enumerate((openmc.XPlane, openmc.YPlane, openmc.ZPlane)):
                    coordinate = ("x0", "y0", "z0")[axis]
                    # Pad the clipping box so it does not coincide with the CAD skin.
                    lower = plane(**{coordinate: instance["low"][axis] - 1e-5})
                    upper = plane(**{coordinate: instance["high"][axis] + 1e-5})
                    slab = +lower & -upper
                    box = slab if box is None else box & slab
                cell = openmc.Cell(name=instance["name"], fill=world_dagmc, region=box)
                cell.translation = instance["translation"]
                cell.rotation = (0, 0, instance["angle"])
                cells.append(cell)
                uncovered &= ~box
            cells.append(openmc.Cell(name="Aire exterior a las cargas", fill=surrounding_material, region=uncovered))
            geometry = openmc.Geometry(cells)

    # Exportar la geometría a un archivo XML
    geometry.export_to_xml()
    
    # Configuracion del mesh

    bbox = mesh_bounds if mesh_bounds is not None else world_dagmc.bounding_box

    mesh = openmc.RegularMesh()

    # cell_size = 4  # Tamaño de las celdas en cm (AUMENTA EL ERROR)

    mesh_dimensions, mesh_lower_left, mesh_upper_right = regular_mesh_parameters(
        bbox, cell_size
    )
    if exact_mesh_bounds:
        mesh_lower_left, mesh_upper_right = bbox
    mesh.dimension = mesh_dimensions

    mesh.lower_left = mesh_lower_left
    mesh.upper_right = mesh_upper_right
    
    # Configuracion de los Tallies
    mesh_filter = CompactRegularMeshFilter(mesh)

    tally_score = str(tally_score).strip().lower()
    if tally_score not in {'flux', 'heating'}:
        raise ValueError("El score del tally debe ser 'flux' o 'heating'")
    if evaluation_relative_error_target is not None:
        target = float(evaluation_relative_error_target)
        if not 0 < target < 1:
            raise ValueError("El objetivo de error relativo debe estar entre cero y uno")

    tallies = openmc.Tallies()
    if tally_score == 'flux':
        flux = openmc.Tally(name='Flux')
        flux.filters = [mesh_filter, openmc.ParticleFilter(['photon'])]
        flux.scores = ['flux']
        if evaluation_relative_error_target is not None:
            # El criterio principal se aplica a la resolución que el usuario
            # seleccionó para el VTI, no únicamente a una malla auxiliar gruesa.
            flux.triggers = [openmc.Trigger("rel_err", target, ignore_zeros=True)]
        tallies.append(flux)
    elif include_direct_heating_diagnostic:
        # Esta malla solo se habilita en pruebas de validación. Mantenerla en
        # cada corrida intensiva duplica almacenamiento y reducciones sin
        # contribuir al resultado híbrido publicado.
        direct_heating = openmc.Tally(name='Heating')
        direct_heating.filters = [mesh_filter]
        direct_heating.scores = ['heating']
        tallies.append(direct_heating)

    evaluation_mesh = None
    if evaluation_cell_size is not None:
        evaluation_cell_size = float(evaluation_cell_size)
        if evaluation_cell_size <= 0:
            raise ValueError("El tamaño de celda de evaluación debe ser positivo")
        evaluation_mesh = openmc.RegularMesh()
        (
            evaluation_mesh.dimension,
            evaluation_mesh.lower_left,
            evaluation_mesh.upper_right,
        ) = regular_mesh_parameters(bbox, evaluation_cell_size)
        evaluation_filter = CompactRegularMeshFilter(evaluation_mesh)
        evaluation_flux = openmc.Tally(name='FluxEvaluation')
        evaluation_flux.filters = [evaluation_filter, openmc.ParticleFilter(['photon'])]
        evaluation_flux.scores = ['flux']
        if evaluation_relative_error_target is not None and tally_score == 'flux':
            evaluation_flux.triggers = [
                openmc.Trigger("rel_err", target, ignore_zeros=True)
            ]
        if tally_score == 'flux':
            tallies.append(evaluation_flux)
        elif include_direct_heating_diagnostic:
            direct_heating_evaluation = openmc.Tally(name='HeatingEvaluation')
            direct_heating_evaluation.filters = [evaluation_filter]
            direct_heating_evaluation.scores = ['heating']
            tallies.append(direct_heating_evaluation)

    if tally_score == 'heating':
        load_materials = list(heating_materials or [])
        if not load_materials:
            raise ValueError("El cálculo heating requiere materiales de carga")
        photon_filter = openmc.ParticleFilter(['photon'])
        hybrid_flux = openmc.Tally(name='HybridFlux')
        hybrid_flux.filters = [mesh_filter, photon_filter]
        hybrid_flux.scores = ['flux']
        if evaluation_relative_error_target is not None:
            hybrid_flux.triggers = [
                openmc.Trigger("rel_err", target * 0.98, ignore_zeros=True)
            ]
        tallies.append(hybrid_flux)
        if evaluation_mesh is not None:
            hybrid_evaluation_flux = openmc.Tally(name='HybridFluxEvaluation')
            hybrid_evaluation_flux.filters = [evaluation_filter, photon_filter]
            hybrid_evaluation_flux.scores = ['flux']
            if evaluation_relative_error_target is not None:
                hybrid_evaluation_flux.triggers = [
                    openmc.Trigger("rel_err", target * 0.98, ignore_zeros=True)
                ]
            tallies.append(hybrid_evaluation_flux)

        material_filter = openmc.MaterialFilter(load_materials)
        heating_by_material = openmc.Tally(name='HeatingByMaterial')
        heating_by_material.filters = [material_filter]
        heating_by_material.scores = ['heating']
        if evaluation_relative_error_target is not None:
            heating_by_material.triggers = [
                openmc.Trigger("rel_err", target * 0.1, ignore_zeros=True)
            ]
        tallies.append(heating_by_material)
        flux_by_material = openmc.Tally(name='FluxByMaterial')
        flux_by_material.filters = [material_filter, photon_filter]
        flux_by_material.scores = ['flux']
        if evaluation_relative_error_target is not None:
            flux_by_material.triggers = [
                openmc.Trigger("rel_err", target * 0.1, ignore_zeros=True)
            ]
        tallies.append(flux_by_material)
    
    tallies.export_to_xml()
    
    if return_evaluation_mesh:
        return geometry, tallies, mesh, evaluation_mesh
    return geometry, tallies, mesh


def create_material_energy_response_tallies(materials, energy_bounds):
    """Tallies for the short spectral-response pilot of intensive mode."""

    load_materials = list(materials)
    if not load_materials:
        raise ValueError("El piloto espectral requiere materiales de carga")
    material_filter = openmc.MaterialFilter(load_materials)
    energy_filter = openmc.EnergyFilter(energy_bounds)
    photon_filter = openmc.ParticleFilter(["photon"])

    flux = openmc.Tally(name="MaterialEnergyFlux")
    flux.filters = [material_filter, photon_filter, energy_filter]
    flux.scores = ["flux"]

    heating = openmc.Tally(name="MaterialEnergyHeating")
    # Do not add ParticleFilter here. OpenMC attributes charged-particle energy
    # to the parent photon collision for heating, and filtering the score as a
    # photon discards that deposited component in 0.15.3.
    heating.filters = [material_filter, energy_filter]
    heating.scores = ["heating"]

    total_flux = openmc.Tally(name="MaterialTotalFlux")
    total_flux.filters = [material_filter, photon_filter]
    total_flux.scores = ["flux"]
    total_heating = openmc.Tally(name="MaterialTotalHeating")
    total_heating.filters = [material_filter]
    total_heating.scores = ["heating"]
    return openmc.Tallies([flux, heating, total_flux, total_heating])


def create_sparse_spectral_heating_tallies(
    mesh,
    evaluation_mesh,
    mesh_material_bins,
    evaluation_mesh_material_bins,
    material_responses,
    relative_error_target=None,
):
    """Create low-variance, energy-aware heating estimators per material."""

    target = None if relative_error_target is None else float(relative_error_target)
    if target is not None and not 0 < target < 1:
        raise ValueError("El objetivo de error relativo debe estar entre cero y uno")
    photon_filter = openmc.ParticleFilter(["photon"])
    tallies = openmc.Tallies()
    fine_names = {}
    evaluation_names = {}
    for ordinal, (name, details) in enumerate(sorted(mesh_material_bins.items()), start=1):
        response = material_responses.get(name)
        if response is None:
            raise KeyError("Falta la respuesta espectral para %s" % name)
        energy_response = openmc.EnergyFunctionFilter(
            response["energy_ev"],
            response["heating_per_tracklength_ev_per_cm"],
            interpolation="histogram",
        )
        fine_name = "SpectralHeatingMaterial%03d" % ordinal
        fine = openmc.Tally(name=fine_name)
        fine.filters = [
            openmc.MeshMaterialFilter(mesh, details["bins"]),
            photon_filter,
            energy_response,
        ]
        fine.scores = ["flux"]
        if target is not None:
            fine.triggers = [openmc.Trigger("rel_err", target, ignore_zeros=True)]
        tallies.append(fine)
        fine_names[name] = fine_name

        evaluation_details = evaluation_mesh_material_bins.get(name)
        if evaluation_mesh is not None and evaluation_details is not None:
            evaluation_name = "SpectralHeatingEvaluationMaterial%03d" % ordinal
            evaluation = openmc.Tally(name=evaluation_name)
            evaluation.filters = [
                openmc.MeshMaterialFilter(
                    evaluation_mesh, evaluation_details["bins"]
                ),
                photon_filter,
                energy_response,
            ]
            evaluation.scores = ["flux"]
            if target is not None:
                evaluation.triggers = [
                    openmc.Trigger("rel_err", target, ignore_zeros=True)
                ]
            tallies.append(evaluation)
            evaluation_names[name] = evaluation_name
    return tallies, fine_names, evaluation_names


def create_alanine_response_tallies(
    mesh,
    evaluation_mesh,
    energy_ev,
    response,
    relative_error_target=None,
):
    """Score dose-to-alanine response without adding pellet geometry."""

    target = None if relative_error_target is None else float(relative_error_target)
    if target is not None and not 0 < target < 1:
        raise ValueError("El objetivo de error relativo debe estar entre cero y uno")
    photon_filter = openmc.ParticleFilter(["photon"])
    energy_response = openmc.EnergyFunctionFilter(
        energy_ev, response, interpolation="log-log"
    )
    fine = openmc.Tally(name="AlanineDose")
    fine.filters = [CompactRegularMeshFilter(mesh), photon_filter, energy_response]
    fine.scores = ["flux"]
    if target is not None:
        fine.triggers = [openmc.Trigger("rel_err", target, ignore_zeros=True)]
    tallies = openmc.Tallies([fine])
    if evaluation_mesh is not None:
        evaluation = openmc.Tally(name="AlanineDoseEvaluation")
        evaluation.filters = [
            CompactRegularMeshFilter(evaluation_mesh),
            photon_filter,
            energy_response,
        ]
        evaluation.scores = ["flux"]
        if target is not None:
            evaluation.triggers = [
                openmc.Trigger("rel_err", target, ignore_zeros=True)
            ]
        tallies.append(evaluation)
    return tallies


def create_sparse_material_response_tallies(
    mesh,
    evaluation_mesh,
    mesh_material_bins,
    evaluation_mesh_material_bins,
    relative_error_target=None,
    energy_ev=None,
    response=None,
    name_prefix="LoadResponse",
    interpolation="log-log",
    response_multiplier_by_material=None,
):
    """Create material-restricted response tallies for load cells only.

    With no energy response this scores photon track length for the fast
    estimator.  With ``energy_ev`` and ``response`` it scores the virtual
    alanine response.  Restricting bins to occupied mesh/material pairs keeps
    world cells out of convergence triggers and substantially reduces tally
    memory for irregular loads.
    """

    target = None if relative_error_target is None else float(relative_error_target)
    if target is not None and not 0 < target < 1:
        raise ValueError("El objetivo de error relativo debe estar entre cero y uno")
    if (energy_ev is None) != (response is None):
        raise ValueError("La energía y la respuesta deben definirse juntas")
    photon_filter = openmc.ParticleFilter(["photon"])
    tallies = openmc.Tallies()
    fine_names = {}
    evaluation_names = {}
    for ordinal, (name, details) in enumerate(sorted(mesh_material_bins.items()), start=1):
        energy_response = None
        if energy_ev is not None:
            multiplier = float(
                (response_multiplier_by_material or {}).get(name, 1.0)
            )
            if not math.isfinite(multiplier) or multiplier <= 0:
                raise ValueError("El factor de respuesta no es válido para %s" % name)
            energy_response = openmc.EnergyFunctionFilter(
                energy_ev,
                np.asarray(response, dtype=float) * multiplier,
                interpolation=interpolation,
            )
        fine_name = "%sMaterial%03d" % (name_prefix, ordinal)
        fine = openmc.Tally(name=fine_name)
        fine.filters = [openmc.MeshMaterialFilter(mesh, details["bins"]), photon_filter]
        if energy_response is not None:
            fine.filters.append(energy_response)
        fine.scores = ["flux"]
        if target is not None:
            fine.triggers = [openmc.Trigger("rel_err", target, ignore_zeros=True)]
        tallies.append(fine)
        fine_names[name] = fine_name

        evaluation_details = evaluation_mesh_material_bins.get(name)
        if evaluation_mesh is not None and evaluation_details is not None:
            evaluation_name = "%sEvaluationMaterial%03d" % (name_prefix, ordinal)
            evaluation = openmc.Tally(name=evaluation_name)
            evaluation.filters = [
                openmc.MeshMaterialFilter(evaluation_mesh, evaluation_details["bins"]),
                photon_filter,
            ]
            if energy_response is not None:
                evaluation.filters.append(energy_response)
            evaluation.scores = ["flux"]
            if target is not None:
                evaluation.triggers = [
                    openmc.Trigger("rel_err", target, ignore_zeros=True)
                ]
            tallies.append(evaluation)
            evaluation_names[name] = evaluation_name
    return tallies, fine_names, evaluation_names


def create_material_dose_calibration_tallies(materials, energy_ev, response):
    """Total heating and alanine-response tallies for a short fast-mode pilot."""

    load_materials = list(materials)
    if not load_materials:
        raise ValueError("La calibración rápida requiere materiales de carga")
    material_filter = openmc.MaterialFilter(load_materials)
    photon_filter = openmc.ParticleFilter(["photon"])
    energy_response = openmc.EnergyFunctionFilter(
        energy_ev, response, interpolation="log-log"
    )
    heating = openmc.Tally(name="FastCalibrationHeating")
    heating.filters = [material_filter]
    heating.scores = ["heating"]
    alanine = openmc.Tally(name="FastCalibrationAlanine")
    alanine.filters = [material_filter, photon_filter, energy_response]
    alanine.scores = ["flux"]
    return openmc.Tallies([heating, alanine])


def create_sparse_direct_heating_tallies(
    mesh,
    mesh_material_bins,
    relative_error_target=None,
    name_prefix="DirectHeating",
):
    """Create direct material-resolved heating tallies on occupied cells."""

    target = None if relative_error_target is None else float(relative_error_target)
    if target is not None and not 0 < target < 1:
        raise ValueError("El objetivo de error relativo debe estar entre cero y uno")
    tallies = openmc.Tallies()
    names = {}
    for ordinal, (name, details) in enumerate(sorted(mesh_material_bins.items()), start=1):
        tally_name = "%sMaterial%03d" % (name_prefix, ordinal)
        tally = openmc.Tally(name=tally_name)
        tally.filters = [openmc.MeshMaterialFilter(mesh, details["bins"])]
        tally.scores = ["heating"]
        if target is not None:
            tally.triggers = [openmc.Trigger("rel_err", target, ignore_zeros=True)]
        tallies.append(tally)
        names[name] = tally_name
    return tallies, names


import openmc
import json
from .source_model import *

def configure_simulation(
    step_angle_degrees_best_real,
    translated_length,
    geometric_center,
    sources_json,
    particles=1e9,
    decay_factor=0.1,
    radionuclide=None,
    verbose=False,
    variance_reduction_mesh=None,
    variance_reduction_batches=20,
    adaptive_max_batches=None,
    adaptive_statistics=False,
    adaptive_relative_error_target=0.05,
    electron_treatment=None,
    use_survival_biasing=True,
    weight_window_mesh=None,
    weight_window_method=None,
    weight_window_value="mean",
    weight_window_threshold=0.7,
    weight_window_ratio=5.0,
    directional_source_bounds=None,
    random_seed=None,
    source_distributions=None,
):
    """
    Esta función configura la simulación de OpenMC a partir de un archivo JSON con las fuentes 
    y el número de partículas proporcionado, y luego exporta la configuración a un archivo XML.

    Parámetros:
    sources_json (str): Ruta al archivo JSON que contiene los datos de las fuentes.
    particles (int): El número de partículas a usar en la simulación.
    output_directory (str): El directorio donde se exportará el archivo XML de configuración.
    """
    
    # Configuración de OpenMC
    settings = openmc.Settings()
    total_particles = int(particles)
    preferred_batches = int(variance_reduction_batches)
    mesh_cells = None
    if variance_reduction_mesh is not None:
        mesh_cells = math.prod(
            int(value) for value in variance_reduction_mesh.dimension
        )
        # En mallas finas, reducir millones de acumuladores de todos los hilos
        # al terminar cada lote puede costar más que el propio transporte. Se
        # conservan cinco realizaciones independientes para estimar el error,
        # pero se evita repetir veinte veces esa reducción global.
        if mesh_cells >= 1_000_000:
            preferred_batches = min(preferred_batches, 5)
        elif mesh_cells >= 250_000:
            preferred_batches = min(preferred_batches, 10)
    if adaptive_statistics and mesh_cells is not None:
        adaptive_relative_error_target = float(adaptive_relative_error_target)
        if not 0 < adaptive_relative_error_target < 1:
            raise ValueError("El objetivo de error relativo debe estar entre cero y uno")
        minimum_batches, maximum_batches, particles_per_batch = adaptive_fixed_source_batch_plan(
            total_particles,
            mesh_cells,
            preferred_max_batches=adaptive_max_batches,
        )
        settings.batches = minimum_batches
        settings.trigger_active = True
        settings.trigger_max_batches = maximum_batches
        settings.trigger_batch_interval = max(
            1,
            int(math.ceil(maximum_batches / 250)),
        )
    else:
        batches, particles_per_batch = fixed_source_batch_plan(
            total_particles,
            preferred_batches,
        )
        settings.batches = batches
    settings.inactive = 0
    settings.particles = particles_per_batch
    settings.run_mode = 'fixed source'
    if random_seed is not None:
        settings.seed = int(random_seed)
    if electron_treatment is not None:
        normalized_electron_treatment = str(electron_treatment).strip().lower()
        if normalized_electron_treatment not in {'led', 'ttb'}:
            raise ValueError("electron_treatment debe ser led o ttb")
        settings.electron_treatment = normalized_electron_treatment
    if variance_reduction_mesh is not None and use_survival_biasing:
        # La malla del tally ya está recortada al volumen de la carga. Para
        # mejorar penetración en materiales absorbentes sin crear árboles de
        # partículas, se usa supervivencia implícita con ruleta rusa al llegar
        # a pesos pequeños. A diferencia de las ventanas de peso, esta técnica
        # no divide historias y mantiene predecible el uso de CPU y memoria.
        settings.survival_biasing = True
        settings.cutoff = {
            "weight": 0.05,
            "weight_avg": 0.5,
        }
    normalized_weight_window_method = str(weight_window_method or "none").strip().lower()
    if normalized_weight_window_method not in {"none", "magic"}:
        raise ValueError("weight_window_method debe ser none o magic")
    if normalized_weight_window_method == "magic":
        if weight_window_mesh is None:
            raise ValueError("MAGIC requiere una malla para generar ventanas de peso")
        maximum_realizations = int(
            settings.trigger_max_batches if settings.trigger_active else settings.batches
        )
        normalized_weight_window_value = str(weight_window_value).strip().lower()
        if normalized_weight_window_value not in {"mean", "rel_err"}:
            raise ValueError("weight_window_value debe ser mean o rel_err")
        # El aprendizaje debe consumir aproximadamente el mismo número de
        # historias aunque cambie el presupuesto total. Si dependiera de un
        # número fijo de lotes, una corrida de 10^10 entrenaría cien veces más
        # que una de 10^8 antes de usar ventanas estables.
        training_histories = min(total_particles, 20_000_000)
        training_realizations = max(
            1,
            min(
                maximum_realizations,
                int(math.ceil(training_histories / settings.particles)),
            ),
        )
        if settings.trigger_active:
            settings.batches = min(
                maximum_realizations,
                max(int(settings.batches), training_realizations),
            )
        generator = openmc.WeightWindowGenerator(
            mesh=weight_window_mesh,
            particle_type="photon",
            method="magic",
            max_realizations=training_realizations,
            update_interval=max(1, min(2, training_realizations)),
            on_the_fly=True,
        )
        generator.update_parameters = {
            "value": normalized_weight_window_value,
            "threshold": float(weight_window_threshold),
            "ratio": float(weight_window_ratio),
        }
        settings.survival_biasing = False
        settings.cutoff = {}
        settings.weight_window_generators = [generator]
    # El resultado completo ya se conserva en ``statepoint.*.h5``.  Permitir que
    # OpenMC imprima cada celda del tally de malla genera un ``tallies.out`` de
    # varios gigabytes (más de 4.5 GB para la malla de referencia) sin aportar
    # información adicional al posprocesamiento estructurado.
    settings.output = {'summary': True, 'tallies': False}
    
    if source_distributions is not None:
        sources = source_distributions
    else:
        # Leer el archivo JSON fuera de la función
        try:
            with open(sources_json, 'r') as f:
                source_configuration = json.load(f)
                sources_data = source_configuration['sources']
        except FileNotFoundError:
            print(f"Error: El archivo '{sources_json}' no fue encontrado.")
            return
        except json.JSONDecodeError:
            print("Error: El archivo JSON tiene un formato incorrecto.")
            return
    
        # Llamar a la función pasando los datos de las fuentes
        positions, activities = add_sources_with_decay(sources_data, decay_factor)
        angle_range = step_angle_degrees_best_real
        translated_length = translated_length  # Solo traslación en z en los elementos de la lista
        rotation_point = geometric_center  # Punto de rotación personalizado

        # Crear fuentes rotadas y trasladadas
        sources = generate_rotated_translated_sources_range(
            positions=positions,
            activities=activities,
            angle_range=angle_range,
            translated_length=translated_length,
            rotation_point=rotation_point,
            radionuclide=radionuclide,
            radionuclide_data=source_configuration.get("radionuclides", {}).get(radionuclide),
        )

    if directional_source_bounds is not None:
        source_biasing = focus_sources_on_target(sources, directional_source_bounds)
    else:
        source_biasing = [
            {"enabled": False, "solid_angle_fraction": 1.0}
            for _source in sources
        ]

    if verbose:
        for i, source in enumerate(sources):
            print(f"Fuente {i + 1}:")
            print(f"  Posición (lower_left): {source.space.lower_left}")
            print(f"  Posición (upper_right): {source.space.upper_right}")
            print(f"  Actividad: {source.strength}")
            print(f"  Energías: {source.energy.x} eV con probabilidades {source.energy.p}")
            print(f"  Ángulo: {source.angle}")
            print(f"  Partícula: {source.particle}")
            print("-" * 40)

    # Configurar las fuentes en OpenMC
    settings.source = sources

    settings._sim_gamma_source_biasing = source_biasing

    # Exportar la configuración a un archivo XML
    settings.export_to_xml()
    
    return settings


import openmc

def define_and_export_model(geometry, materials, settings, tallies):
    """
    Esta función define un modelo de simulación en OpenMC y exporta la configuración a un archivo XML.

    Parámetros:
    geometry (openmc.Geometry): El objeto de geometría de la simulación.
    materials (openmc.Materials): El conjunto de materiales de la simulación.
    settings (openmc.Settings): La configuración de la simulación.
    tallies (openmc.Tallies): El objeto que contiene las tallas para la simulación.
    output_directory (str): El directorio donde se exportará el archivo XML de modelo.
    """
    # Definición del modelo con los componentes proporcionados
    model = openmc.Model(geometry, materials, settings, tallies)

    # Exportar el modelo a un archivo XML
    model.export_to_model_xml()
    
    return model


import glob
from pathlib import Path

from .structured_postprocess import process_statepoint_to_artifacts


def extract_and_write_mesh_tally(
    mesh=None,
    tally_name='Flux',
    output_vtk='output/dose_distribution.vti',
    dose_coeff=1.0,
):
    """Convierte el tally más reciente directamente a artefactos estructurados.

    ``mesh`` se conserva en la firma para compatibilidad con las llamadas
    existentes, pero la geometría exacta se lee del propio statepoint. No se
    generan ``tallies.out``, CSV ni tetraedros Delaunay.
    """

    statepoint_files = sorted(
        glob.glob("statepoint*.h5"),
        key=lambda path: int(Path(path).stem.rsplit(".", 1)[-1]),
    )
    if not statepoint_files:
        raise FileNotFoundError("No statepoint files found.")
    output_path = Path(output_vtk)
    result = process_statepoint_to_artifacts(
        statepoint_files[-1],
        output_path.parent,
        dose_coeff=dose_coeff,
        tally_name=tally_name,
        vtk_filename=output_path.with_suffix(".vti").name,
    )
    return result



# Leer los archivos JSON de configuración
def load_config(config_file):
    try:
        with open(config_file, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        print(f"Error: El archivo {config_file} no se encuentra.")
        raise
    except json.JSONDecodeError:
        print(f"Error: El archivo {config_file} tiene un formato inválido.")
        raise

def load_simulation_params(params_file):
    try:
        with open(params_file, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        print(f"Error: El archivo {params_file} no se encuentra.")
        raise
    except json.JSONDecodeError:
        print(f"Error: El archivo {params_file} tiene un formato inválido.")
        raise


import os
import shutil

def delete_files_and_folders(file_name):
    """Elimina archivos .h5m y carpetas que comiencen con file_name."""
    for item in os.listdir():
        if item.startswith(file_name):
            if os.path.isdir(item):
                shutil.rmtree(item)  # Eliminar carpeta
            # elif item.endswith('.h5m'):
            #     os.remove(item)  # Eliminar archivo .h5m

def delete_folders(file_name):
    """Elimina carpetas que comiencen con file_name."""
    for item in os.listdir():
        if item.startswith(file_name) and os.path.isdir(item):
            shutil.rmtree(item)  # Eliminar carpeta

def delete_h5m_files():
    """Elimina todos los archivos .h5m en el directorio actual."""
    for item in os.listdir():
        if item.endswith('.h5m'):
            os.remove(item)  # Eliminar archivo .h5m

def delete_pkl_files():
    """Elimina todos los archivos .pkl en el directorio actual."""
    for item in os.listdir():
        if item.endswith('.pkl'):
            os.remove(item)  # Eliminar archivo .pkl


def run_openmc_simulation(
    decay_factor=0.1,
    radionuclide=None,
    cell_size=2,
    cpu_cores=-1,
    dose_factor=1.0,
    output_root='output/monte_carlo',
):
    """
    Esta función ejecuta la simulación de OpenMC utilizando los archivos generados y configurados previamente.
    
    Args:
        decay_factor (float): El factor de decaimiento para las fuentes.
        cell_size (int): El tamaño de las celdas en cm para el mesh, para grandes dimensiones de la geometria se recomienda valores mayores 4 para usar menos memoria RAM.
        cpu_cores (int): Límite general de núcleos; -1 permite usar todos los disponibles.
        dose_factor (float): Conversión de flujo a tasa de dosis absorbida.
        output_root (str): Carpeta del caso donde se guardan VTI, cortes y JSON.
    """
    # Eliminar archivos .h5m y .pkl anteriores a la simulacion
    delete_h5m_files()
    delete_pkl_files()
    
    # Cargar los parámetros desde los archivos JSON
    config = load_config('config.json') #Ubicacion respecto al main ejecutable y no desde este archivo
    sim_params = load_simulation_params('simulations_params.json') #Ubicacion respecto al main ejecutable

    # Extraer parámetros del JSON
    directory_openmc = config['directory_openmc']
    file_name = config['file_name']
    json_file_path = config['json_file_path']
    sources_json = config['sources_json']
    particles = config['particles']
    
    # Extraer parámetros de simulación específicos
    step_angle_degrees_best_real = sim_params['step_angle_degrees_best_real']
    translated_length = sim_params['translated_length']
    geometric_center = sim_params['geometric_center']
    
    # Generar los archivos OpenMC
    generate_openmc_files(directory_openmc, file_name)
    
    # Crear los materiales
    materials = create_materials(json_file_path)
    
    # Crear la geometría y tallies
    geometry, tallies, mesh = create_geometry_tally(cell_size = cell_size)
    
    # Configurar la simulación
    settings = configure_simulation(step_angle_degrees_best_real, translated_length, geometric_center, sources_json, particles, decay_factor = decay_factor, radionuclide=radionuclide)
    
    # Definir y exportar el modelo
    model = define_and_export_model(geometry, materials, settings, tallies)
    
    # Aquí puedes ejecutar la simulación si lo deseas
    environment_openmc = Path(sys.executable).resolve().parent / "openmc"
    openmc.run(
        threads=resolve_cpu_threads(cpu_cores),
        openmc_exec=(
            str(environment_openmc)
            if environment_openmc.is_file()
            else "openmc"
        ),
    )
    
    # Eliminar archivos y carpetas después de la simulación
    delete_folders(file_name)

    extract_and_write_mesh_tally(
        mesh=mesh,
        tally_name='Flux',
        output_vtk=str(Path(output_root) / 'dose_distribution.vti'),
        dose_coeff=dose_factor,
    )
    
    
