import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from sim_gamma.application.services.case_store import CASE_DIRECTORIES, CaseStore
from sim_gamma.application.services.configuration_store import ConfigurationStore
from sim_gamma.application.services.deterministic_jobs import DeterministicJobs
from sim_gamma.application.services.movement_export import (
    REQUIRED_KEYS,
    build_from_deterministic_case,
    build_movement_document,
    validate_movement_document,
    write_movement_document,
)
from sim_gamma.application.services.monte_carlo_jobs import MonteCarloJobs
from sim_gamma.application.services.source_inventory import SourceInventory
from sim_gamma.simulators.contracts import MonteCarloRequest
from sim_gamma.simulators.openmc.backend import OpenMCBackend


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class DeterministicMemoryTests(unittest.TestCase):
    def test_chunked_flux_matches_a_single_block(self):
        from sim_gamma.physics.deterministic.optimizer import calculate_gamma_flux

        sources = [
            {
                "lower_point": [0.0, 0.0, -1.0],
                "upper_point": [0.0, 0.0, 1.0],
                "intensity": 10.0,
                "initial_reading_date": date.today().isoformat(),
            },
            {
                "lower_point": [2.0, 0.0, -1.0],
                "upper_point": [2.0, 0.0, 1.0],
                "intensity": 5.0,
                "initial_reading_date": date.today().isoformat(),
            },
        ]
        points = np.array(
            [[1.0, 1.0, -2.0], [1.0, 0.5, 0.0], [1.0, 1.5, 2.0], [3.0, 1.0, 0.5]]
        )

        _, chunked = calculate_gamma_flux(
            sources, 0, 0, [0, 0, 0], points, points, 0, point_chunk_size=1
        )
        _, single_block = calculate_gamma_flux(
            sources, 0, 0, [0, 0, 0], points, points, 0, point_chunk_size=100
        )

        np.testing.assert_allclose(chunked, single_block, rtol=1e-12, atol=0)

    def test_vtk_receives_every_configured_interior_point(self):
        from sim_gamma.results.artifacts import write_dose_artifacts

        points = np.zeros((20001, 3), dtype=np.float32)
        values = np.ones(20001, dtype=np.float32)
        with tempfile.TemporaryDirectory() as temporary, patch(
            "sim_gamma.results.artifacts._write_vtk"
        ) as write_vtk, patch(
            "sim_gamma.results.artifacts._write_cuts", return_value={}
        ):
            result = write_dose_artifacts(points, values, temporary, "Prueba")

        self.assertEqual(len(write_vtk.call_args.args[0]), 20001)
        self.assertEqual(result["total_points"], 20001)
        self.assertEqual(result["vtk_points"], 20001)


class MonteCarloPostprocessTests(unittest.TestCase):
    def test_adaptive_batch_plan_keeps_thirty_statistical_realizations(self):
        from sim_gamma.simulators.openmc.engine import adaptive_fixed_source_batch_plan

        minimum, maximum, particles = adaptive_fixed_source_batch_plan(
            1_000_000_000,
            250_000,
            preferred_max_batches=100,
        )

        self.assertEqual((minimum, maximum, particles), (30, 100, 10_000_000))

    def test_adaptive_batch_plan_honors_a_smaller_configured_maximum(self):
        from sim_gamma.simulators.openmc.engine import adaptive_fixed_source_batch_plan

        minimum, maximum, particles = adaptive_fixed_source_batch_plan(
            20_000_000,
            10_000,
            preferred_max_batches=20,
        )

        self.assertEqual((minimum, maximum, particles), (20, 20, 1_000_000))

    def test_openmc_sources_accept_one_longitudinal_position(self):
        from sim_gamma.simulators.openmc.source_model import (
            generate_rotated_translated_sources_range,
        )

        with patch(
            "sim_gamma.simulators.openmc.source_model.generate_rotated_sources_range",
            return_value=["source"],
        ) as generate:
            sources = generate_rotated_translated_sources_range(
                [(0.0, 0.0, [-1.0, 1.0])],
                [1.0],
                [0.0],
                translated_length=[0.0],
            )

        self.assertEqual(sources, ["source"])
        generate.assert_called_once()

    def test_operational_dur_uses_p90_over_p10(self):
        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            summarize_dose,
        )

        mesh = RegularMeshSpec((10, 1, 1), (0, 0, 0), (10, 1, 1))
        summary = summarize_dose(np.arange(1, 11, dtype=np.float32), mesh)

        self.assertAlmostEqual(summary["dose_p10"], 1.9)
        self.assertAlmostEqual(summary["dose_p90"], 9.1)
        self.assertAlmostEqual(
            summary["operational_dose_uniformity_ratio"], 9.1 / 1.9
        )
        self.assertAlmostEqual(summary["dose_p05"], 1.45)
        self.assertAlmostEqual(summary["dose_p95"], 9.55)
        self.assertAlmostEqual(
            summary["robust_dose_uniformity_ratio"], 9.55 / 1.45
        )

    def test_alanine_response_uses_complete_nist_table_and_mass_normalization(self):
        from sim_gamma.simulators.openmc.structured_postprocess import (
            ALANINE_ENERGY_MEV,
            ALANINE_MU_EN_OVER_RHO_CM2_G,
            alanine_energy_response,
        )

        energy_ev, response = alanine_energy_response()
        self.assertEqual(energy_ev.size, 36)
        self.assertEqual(response.size, 36)
        one_mev = int(np.flatnonzero(ALANINE_ENERGY_MEV == 1.0)[0])
        self.assertAlmostEqual(
            response[one_mev] / energy_ev[one_mev],
            ALANINE_MU_EN_OVER_RHO_CM2_G[one_mev],
        )
        self.assertAlmostEqual(ALANINE_MU_EN_OVER_RHO_CM2_G[one_mev], 0.03012)

    def test_sparse_material_bins_only_store_occupied_pairs(self):
        import openmc

        from sim_gamma.simulators.openmc.structured_postprocess import (
            sparse_mesh_material_bins,
        )

        water = openmc.Material(name="water")
        aluminum = openmc.Material(name="aluminum")
        fractions = np.asarray(
            [[1.0, 0.5, 0.0, 0.0], [0.0, 0.5, 1.0, 0.0]], dtype=np.float32
        )
        result = sparse_mesh_material_bins(
            fractions,
            [{"id": 1, "name": "water"}, {"id": 2, "name": "aluminum"}],
            {"water": water, "aluminum": aluminum},
        )
        np.testing.assert_array_equal(result["water"]["bins"][:, 0], [0, 1])
        np.testing.assert_array_equal(result["aluminum"]["bins"][:, 0], [1, 2])

    def test_alternative_tallies_use_energy_function_on_both_meshes(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import create_alanine_response_tallies
        from sim_gamma.simulators.openmc.structured_postprocess import (
            alanine_energy_response,
        )

        fine = openmc.RegularMesh()
        fine.dimension = (2, 2, 2)
        fine.lower_left = (-1, -1, -1)
        fine.upper_right = (1, 1, 1)
        evaluation = openmc.RegularMesh()
        evaluation.dimension = (1, 1, 1)
        evaluation.lower_left = (-1, -1, -1)
        evaluation.upper_right = (1, 1, 1)
        energy, response = alanine_energy_response()
        tallies = create_alanine_response_tallies(
            fine,
            evaluation,
            energy,
            response,
            relative_error_target=0.01,
        )
        self.assertEqual([tally.name for tally in tallies], ["AlanineDose", "AlanineDoseEvaluation"])
        for tally in tallies:
            self.assertEqual(tally.scores, ["flux"])
            self.assertTrue(
                any(isinstance(item, openmc.EnergyFunctionFilter) for item in tally.filters)
            )
            self.assertAlmostEqual(tally.triggers[0].threshold, 0.01)

    def test_directional_source_bias_covers_load_and_preserves_isotropic_density(self):
        import openmc

        from sim_gamma.simulators.openmc.source_model import focus_sources_on_target

        source = openmc.IndependentSource()
        source.space = openmc.stats.Box(
            (-150.0, 0.0, -20.0),
            (-150.0, 0.0, 20.0),
        )
        source.angle = openmc.stats.Isotropic()
        source.strength = 2.0e12
        original_strength = source.strength
        diagnostics = focus_sources_on_target(
            [source],
            [[-60.0, -210.0, -75.0], [60.0, -110.0, 75.0]],
            safety_margin_degrees=0.5,
        )

        fraction = diagnostics[0]["solid_angle_fraction"]
        self.assertTrue(diagnostics[0]["enabled"])
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 1.0)
        self.assertAlmostEqual(source.strength, original_strength * fraction)
        # strength/Omega debe coincidir con la densidad isotrópica A/(4*pi).
        solid_angle = 4.0 * np.pi * fraction
        self.assertAlmostEqual(
            source.strength / solid_angle,
            original_strength / (4.0 * np.pi),
        )

    def test_geometry_builds_fine_and_direct_evaluation_tallies(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import create_geometry_tally

        dagmc = Mock()
        dagmc.bounding_region.return_value = Mock()
        geometry = Mock()
        with patch(
            "sim_gamma.simulators.openmc.engine.openmc.DAGMCUniverse",
            return_value=dagmc,
        ), patch(
            "sim_gamma.simulators.openmc.engine.openmc.Cell",
            return_value=Mock(),
        ), patch(
            "sim_gamma.simulators.openmc.engine.openmc.Geometry",
            return_value=geometry,
        ), patch("openmc.Tallies.export_to_xml"):
            _, tallies, fine_mesh, evaluation_mesh = create_geometry_tally(
                h5m_filename="synthetic.h5m",
                cell_size=1.0,
                mesh_bounds=[[-6.0, -5.0, -3.0], [6.0, 5.0, 3.0]],
                evaluation_cell_size=3.0,
                return_evaluation_mesh=True,
                evaluation_relative_error_target=0.05,
            )

        self.assertEqual([tally.name for tally in tallies], ["Flux", "FluxEvaluation"])
        self.assertEqual([tally.scores for tally in tallies], [["flux"], ["flux"]])
        self.assertTrue(
            all(
                any(isinstance(item, openmc.ParticleFilter) for item in tally.filters)
                for tally in tallies
            )
        )
        self.assertEqual(tuple(fine_mesh.dimension), (12, 10, 6))
        self.assertEqual(tuple(evaluation_mesh.dimension), (4, 4, 2))
        self.assertEqual(tuple(evaluation_mesh.width), (3.0, 3.0, 3.0))
        self.assertTrue(
            all(
                len(tally.triggers) == 1
                and tally.triggers[0].trigger_type == "rel_err"
                and tally.triggers[0].threshold == 0.05
                for tally in tallies
            )
        )

    def test_new_transport_clears_statepoints_but_preserves_dagmc_geometry(self):
        from sim_gamma.workers.monte_carlo_cli import clear_previous_transport_outputs

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = [
                root / "statepoint.100.h5",
                root / "source.010.h5",
                root / "surface_source.020.h5",
                root / "summary.h5",
                root / "weight_windows.h5",
            ]
            for path in stale:
                path.write_bytes(b"old")
            geometry = root / "world.h5m"
            geometry.write_bytes(b"geometry")

            clear_previous_transport_outputs(root)

            self.assertFalse(any(path.exists() for path in stale))
            self.assertEqual(geometry.read_bytes(), b"geometry")

    def test_convergence_requires_an_error_estimate_for_every_material_cell(self):
        from sim_gamma.workers.monte_carlo_cli import tally_converged

        summary = {
            "relative_error_maximum": 0.049,
            "cells_with_error_estimate": 99,
        }
        self.assertFalse(tally_converged(summary, expected_cells=100, target=0.05))
        summary["cells_with_error_estimate"] = 100
        self.assertTrue(tally_converged(summary, expected_cells=100, target=0.05))

    def test_geometry_selects_heating_for_both_monte_carlo_tallies(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import create_geometry_tally

        dagmc = Mock()
        dagmc.bounding_region.return_value = Mock()
        water = openmc.Material(name="water")
        with patch(
            "sim_gamma.simulators.openmc.engine.openmc.DAGMCUniverse",
            return_value=dagmc,
        ), patch(
            "sim_gamma.simulators.openmc.engine.openmc.Cell",
            return_value=Mock(),
        ), patch(
            "sim_gamma.simulators.openmc.engine.openmc.Geometry",
            return_value=Mock(),
        ), patch("openmc.Tallies.export_to_xml"):
            _, tallies, _, _ = create_geometry_tally(
                h5m_filename="synthetic.h5m",
                mesh_bounds=[[-3.0, -3.0, -3.0], [3.0, 3.0, 3.0]],
                evaluation_cell_size=3.0,
                return_evaluation_mesh=True,
                tally_score="heating",
                heating_materials=[water],
                evaluation_relative_error_target=0.01,
            )

        self.assertEqual(
            [tally.name for tally in tallies],
            [
                "HybridFlux",
                "HybridFluxEvaluation",
                "HeatingByMaterial",
                "FluxByMaterial",
            ],
        )
        self.assertEqual(tallies[0].scores, ["flux"])
        self.assertEqual(tallies[1].scores, ["flux"])
        self.assertEqual(tallies[2].scores, ["heating"])
        self.assertEqual(tallies[3].scores, ["flux"])
        self.assertTrue(tallies[0].triggers)
        self.assertTrue(tallies[1].triggers)
        self.assertAlmostEqual(tallies[0].triggers[0].threshold, 0.0098)
        self.assertAlmostEqual(tallies[1].triggers[0].threshold, 0.0098)
        self.assertAlmostEqual(tallies[2].triggers[0].threshold, 0.001)
        self.assertAlmostEqual(tallies[3].triggers[0].threshold, 0.001)

    def test_heating_conversion_uses_each_material_density_and_cell_volume(self):
        from sim_gamma.simulators.openmc.structured_postprocess import (
            JOULE_PER_EV,
            RegularMeshSpec,
            heating_dose_rate_scale,
        )

        mesh = RegularMeshSpec(
            dimension=(2, 1, 1),
            lower_left=(0.0, 0.0, 0.0),
            upper_right=(4.0, 2.0, 2.0),
        )
        scale = heating_dose_rate_scale(
            np.array([1, 2], dtype=np.uint16),
            [{"id": 1, "name": "water"}, {"id": 2, "name": "product"}],
            [
                {"name": "water", "density": 1.0},
                {"name": "product", "density": 2.0},
            ],
            mesh,
            photon_yield=2.0,
        )
        base = JOULE_PER_EV * 3600.0 * 2.0 / mesh.cell_volume
        np.testing.assert_allclose(scale, [base, base / 2.0], rtol=1e-14)

    def test_hybrid_heating_conversion_uses_openmc_response_for_each_material(self):
        from sim_gamma.simulators.openmc.structured_postprocess import (
            JOULE_PER_EV,
            RegularMeshSpec,
            hybrid_heating_dose_rate_scale,
        )

        mesh = RegularMeshSpec(
            dimension=(2, 1, 1),
            lower_left=(0.0, 0.0, 0.0),
            upper_right=(4.0, 2.0, 2.0),
        )
        scale, response_error = hybrid_heating_dose_rate_scale(
            np.array([1, 2], dtype=np.uint16),
            [{"id": 1, "name": "water"}, {"id": 2, "name": "product"}],
            [
                {"name": "water", "density": 1.0},
                {"name": "product", "density": 2.0},
            ],
            mesh,
            {
                "water": {
                    "heating_per_tracklength_ev_per_cm": 10.0,
                    "relative_error": 0.003,
                },
                "product": {
                    "heating_per_tracklength_ev_per_cm": 20.0,
                    "relative_error": 0.004,
                },
            },
            photon_yield=2.0,
        )
        base = JOULE_PER_EV * 3600.0 * 2.0 / mesh.cell_volume
        # La segunda celda tiene el doble de respuesta y el doble de densidad.
        np.testing.assert_allclose(scale, [base * 10.0, base * 10.0], rtol=1e-14)
        np.testing.assert_allclose(response_error, [0.003, 0.004], rtol=1e-14)

    def test_photon_yield_preserves_multiple_gamma_emissions(self):
        from sim_gamma.physics.parameters import load_photon_yield

        with tempfile.TemporaryDirectory() as temporary:
            source_file = Path(temporary) / "sources.json"
            source_file.write_text(
                json.dumps({
                    "radionuclides": {
                        "Co-60": {
                            "emissions": [
                                {"type": "gamma", "probability": 0.999},
                                {"type": "gamma", "probability": 0.998},
                                {"type": "beta", "probability": 1.0},
                            ]
                        }
                    }
                }),
                encoding="utf-8",
            )

            photon_yield = load_photon_yield("Co-60", source_file)

        self.assertAlmostEqual(photon_yield, 1.997)

    def test_historical_flux_factor_respects_gamma_emission_probabilities(self):
        from sim_gamma.physics.parameters import load_physical_parameters

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_file = root / "sources.json"
            materials_file = root / "materials.json"
            source_file.write_text(
                json.dumps({
                    "radionuclides": {
                        "Co-60": {
                            "half_life": 10.0,
                            "emissions": [
                                {"value": 1.0, "probability": 1.0},
                                {"value": 2.0, "probability": 0.25},
                            ],
                        }
                    }
                }),
                encoding="utf-8",
            )
            materials_file.write_text(
                json.dumps({
                    "materials": [{
                        "name": "air",
                        "mu_en_over_rho": {"1.000": 0.02, "2.000": 0.03},
                    }]
                }),
                encoding="utf-8",
            )

            _decay, dose_factor = load_physical_parameters(
                "Co-60", "air", source_file, materials_file
            )

        expected = (1.0 * 1.0 * 0.02 + 0.25 * 2.0 * 0.03) * 3.6 * 1.6022e-10
        self.assertAlmostEqual(dose_factor, expected)

    def test_step_conversion_falls_back_to_the_isolated_cad_environment(self):
        from sim_gamma.simulators.openmc import engine

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "world.step").write_text("STEP", encoding="utf-8")

            def generate_h5m(directory, file_name, **kwargs):
                Path(directory, f"{file_name}.h5m").write_bytes(b"H5M")

            with patch.object(
                engine,
                "_run_isolated_cad_converter",
                side_effect=generate_h5m,
            ) as converter:
                engine.generate_openmc_files(root, "world")

            converter.assert_called_once()
            self.assertEqual(converter.call_args.args[1], "world")
            self.assertEqual(converter.call_args.kwargs, {"material_tags": None, "implicit_air": False})
            self.assertEqual((root / "world.h5m").read_bytes(), b"H5M")

    def test_step_conversion_reports_a_missing_step_as_an_error(self):
        from sim_gamma.simulators.openmc.engine import generate_openmc_files

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                generate_openmc_files(temporary, "world")

    def test_load_mesh_bounds_preserve_cell_size_and_cover_only_the_requested_volume(self):
        from sim_gamma.simulators.openmc.engine import regular_mesh_parameters

        dimensions, lower, upper = regular_mesh_parameters(
            [[-6.1, -4.0, 1.0], [6.1, 5.1, 16.0]],
            3.0,
        )

        self.assertEqual(dimensions, [5, 4, 5])
        np.testing.assert_allclose(np.subtract(upper, lower), [15.0, 12.0, 15.0])
        self.assertLessEqual(lower[0], -6.1)
        self.assertGreaterEqual(upper[1], 5.1)
        self.assertEqual(np.prod(dimensions), 100)

    def test_openmc_disables_the_multi_gigabyte_text_tally(self):
        from sim_gamma.simulators.openmc.engine import configure_simulation

        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch(
                "openmc.Settings.export_to_xml"
            ) as export_settings:
                settings = configure_simulation(
                    [0], [0], [0, 0, 0], sources, particles=100_000_000
                )

            self.assertEqual(settings.output, {"summary": True, "tallies": False})
            self.assertEqual(settings.batches, 20)
            self.assertEqual(settings.inactive, 0)
            self.assertEqual(settings.particles, 5_000_000)
            self.assertEqual(settings.batches * settings.particles, 100_000_000)
            export_settings.assert_called_once_with()

    def test_openmc_accepts_a_configurable_particle_count(self):
        from sim_gamma.simulators.openmc.engine import configure_simulation

        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0], [0], [0, 0, 0], sources, particles=1_234_560
                )

        self.assertEqual(settings.batches * settings.particles, 1_234_560)

    def test_cpu_limit_uses_the_linux_affinity_available_to_the_job(self):
        from sim_gamma.compute_resources import resolve_cpu_threads

        with patch("sim_gamma.compute_resources.os.sched_getaffinity", return_value=set(range(12))):
            self.assertEqual(resolve_cpu_threads(-1), 12)
            self.assertEqual(resolve_cpu_threads(4), 4)
            self.assertEqual(resolve_cpu_threads(24), 12)

        with patch("sim_gamma.compute_resources.os.sched_getaffinity", return_value=set(range(4))):
            self.assertEqual(resolve_cpu_threads(-1), 4)

    def test_load_focused_variance_reduction_preserves_the_total_particle_count(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        dose_mesh = openmc.RegularMesh(mesh_id=81)
        dose_mesh.dimension = (60, 50, 75)
        dose_mesh.lower_left = (-60.0, -210.0, -75.0)
        dose_mesh.upper_right = (60.0, -110.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=100_000_000,
                    variance_reduction_mesh=dose_mesh,
                )

        self.assertEqual(settings.batches, 20)
        self.assertEqual(settings.particles, 5_000_000)
        self.assertEqual(settings.batches * settings.particles, 100_000_000)
        self.assertEqual(len(settings.weight_windows), 0)
        self.assertEqual(len(settings.weight_window_generators), 0)
        self.assertTrue(settings.survival_biasing)
        self.assertEqual(
            settings.cutoff,
            {"weight": 0.05, "weight_avg": 0.5},
        )

    def test_fine_openmc_mesh_uses_fewer_tally_reductions(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        dose_mesh = openmc.RegularMesh(mesh_id=82)
        dose_mesh.dimension = (120, 100, 150)
        dose_mesh.lower_left = (-60.0, -210.0, -75.0)
        dose_mesh.upper_right = (60.0, -110.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=100_000_000,
                    variance_reduction_mesh=dose_mesh,
                )

        self.assertEqual(settings.batches, 5)
        self.assertEqual(settings.particles, 20_000_000)
        self.assertEqual(settings.batches * settings.particles, 100_000_000)

    def test_adaptive_heating_uses_particle_cap_and_local_electron_deposition(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        dose_mesh = openmc.RegularMesh(mesh_id=83)
        dose_mesh.dimension = (60, 50, 75)
        dose_mesh.lower_left = (-60.0, -210.0, -75.0)
        dose_mesh.upper_right = (60.0, -110.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=1_000_000_000,
                    variance_reduction_mesh=dose_mesh,
                    adaptive_statistics=True,
                    electron_treatment="led",
                )

        self.assertTrue(settings.trigger_active)
        self.assertEqual(settings.batches, 30)
        self.assertEqual(settings.trigger_max_batches, 1_000)
        self.assertEqual(settings.particles, 1_000_000)
        self.assertEqual(settings.trigger_max_batches * settings.particles, 1_000_000_000)
        self.assertEqual(settings.electron_treatment, "led")

    def test_adaptive_openmc_honors_configured_maximum_batches(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        dose_mesh = openmc.RegularMesh(mesh_id=184)
        dose_mesh.dimension = (60, 50, 75)
        dose_mesh.lower_left = (-60.0, -210.0, -75.0)
        dose_mesh.upper_right = (60.0, -110.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=1_000_000_000,
                    variance_reduction_mesh=dose_mesh,
                    adaptive_statistics=True,
                    adaptive_max_batches=100,
                )

        self.assertEqual(settings.batches, 30)
        self.assertEqual(settings.trigger_max_batches, 100)
        self.assertEqual(settings.particles, 10_000_000)
        self.assertEqual(settings.trigger_max_batches * settings.particles, 1_000_000_000)

    def test_magic_weight_windows_target_the_evaluation_mesh_for_photons(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        fine_mesh = openmc.RegularMesh(mesh_id=83)
        fine_mesh.dimension = (60, 50, 75)
        fine_mesh.lower_left = (-60.0, -210.0, -75.0)
        fine_mesh.upper_right = (60.0, -110.0, 75.0)
        evaluation_mesh = openmc.RegularMesh(mesh_id=84)
        evaluation_mesh.dimension = (40, 34, 50)
        evaluation_mesh.lower_left = (-60.0, -211.0, -75.0)
        evaluation_mesh.upper_right = (60.0, -109.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=100_000_000,
                    variance_reduction_mesh=fine_mesh,
                    adaptive_statistics=True,
                    use_survival_biasing=True,
                    weight_window_mesh=evaluation_mesh,
                    weight_window_method="magic",
                    weight_window_value="rel_err",
                    weight_window_threshold=0.65,
                    weight_window_ratio=7.0,
                )

        self.assertFalse(settings.survival_biasing)
        self.assertEqual(len(settings.weight_window_generators), 1)
        generator = settings.weight_window_generators[0]
        self.assertIs(generator.mesh, evaluation_mesh)
        self.assertEqual(str(generator.particle_type), "photon")
        self.assertEqual(generator.method, "magic")
        self.assertEqual(generator.max_realizations, 20)
        self.assertEqual(generator.update_interval, 2)
        self.assertEqual(generator.update_parameters["value"], "rel_err")
        self.assertEqual(generator.update_parameters["threshold"], 0.65)
        self.assertEqual(generator.update_parameters["ratio"], 7.0)
        self.assertEqual(settings.batches, 30)
        self.assertEqual(settings.trigger_max_batches * settings.particles, 100_000_000)

    def test_magic_training_cost_does_not_scale_with_particle_budget(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        fine_mesh = openmc.RegularMesh(mesh_id=85)
        fine_mesh.dimension = (60, 50, 75)
        fine_mesh.lower_left = (-60.0, -210.0, -75.0)
        fine_mesh.upper_right = (60.0, -110.0, 75.0)
        evaluation_mesh = openmc.RegularMesh(mesh_id=86)
        evaluation_mesh.dimension = (40, 34, 50)
        evaluation_mesh.lower_left = (-60.0, -211.0, -75.0)
        evaluation_mesh.upper_right = (60.0, -109.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=400_000_000,
                    variance_reduction_mesh=fine_mesh,
                    adaptive_statistics=True,
                    weight_window_mesh=evaluation_mesh,
                    weight_window_method="magic",
                )

        generator = settings.weight_window_generators[0]
        self.assertEqual(settings.particles, 1_000_000)
        self.assertEqual(generator.max_realizations, 20)
        self.assertEqual(generator.max_realizations * settings.particles, 20_000_000)

    def test_default_intensive_budget_keeps_magic_learning_batches_small(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import configure_simulation

        mesh = openmc.RegularMesh(mesh_id=87)
        mesh.dimension = (60, 50, 75)
        mesh.lower_left = (-60.0, -210.0, -75.0)
        mesh.upper_right = (60.0, -110.0, 75.0)
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with patch(
                "sim_gamma.simulators.openmc.engine.add_sources_with_decay",
                return_value=([], []),
            ), patch(
                "sim_gamma.simulators.openmc.engine.generate_rotated_translated_sources_range",
                return_value=[],
            ), patch("openmc.Settings.export_to_xml"):
                settings = configure_simulation(
                    [0],
                    [0],
                    [0, 0, 0],
                    sources,
                    particles=10_000_000_000,
                    variance_reduction_mesh=mesh,
                    adaptive_statistics=True,
                    weight_window_mesh=mesh,
                    weight_window_method="magic",
                )

        self.assertEqual(settings.trigger_max_batches, 10_000)
        self.assertEqual(settings.particles, 1_000_000)
        self.assertEqual(settings.trigger_batch_interval, 40)
        self.assertEqual(settings.weight_window_generators[0].max_realizations, 20)

    def test_material_volume_fraction_corrects_partial_voxel_mass(self):
        import trimesh

        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            build_material_volume_fractions,
            heating_dose_rate_scale,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cube = trimesh.creation.box(extents=(1.0, 2.0, 2.0))
            cube.apply_translation((-0.5, 0.0, 0.0))
            surface = root / "water.stl"
            cube.export(surface)
            mesh = RegularMeshSpec((1, 1, 1), (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))
            fractions, labels = build_material_volume_fractions(
                {"water": surface},
                mesh,
                root / "cache",
                samples_per_axis=4,
            )

            self.assertAlmostEqual(float(fractions[0, 0]), 0.5, places=6)
            scale = heating_dose_rate_scale(
                np.array([1], dtype=np.uint16),
                labels,
                [{"name": "water", "density": 1.0}],
                mesh,
                material_fractions=fractions,
            )
            expected_mass_g = 4.0
            expected = 1.602176634e-19 * 3600.0 / expected_mass_g
            self.assertAlmostEqual(float(scale[0]), expected, places=24)

    def test_chunked_tally_statistics_match_openmc_relative_error_formula(self):
        import h5py

        from sim_gamma.simulators.openmc.structured_postprocess import (
            read_tally_statistics_chunked,
            summarize_relative_error,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            statepoint = root / "statepoint.statistics.h5"
            batch_values = np.array(
                [
                    [1.0, 2.0, 3.0, 4.0],
                    [2.0, 2.0, 2.0, 2.0],
                    [0.0, 0.0, 0.0, 0.0],
                ]
            )
            with h5py.File(statepoint, "w") as output:
                results = output.create_dataset("tallies/tally 7/results", (3, 1, 2))
                results[:, 0, 0] = batch_values.sum(axis=1)
                results[:, 0, 1] = np.square(batch_values).sum(axis=1)

            mean, relative_error = read_tally_statistics_chunked(
                statepoint,
                tally_id=7,
                num_realizations=4,
                expected_cells=3,
                mean_destination=root / "mean.float32",
                relative_error_destination=root / "relative-error.float32",
                scale=2.0,
                chunk_cells=2,
            )
            expected_mean = batch_values.mean(axis=1) * 2.0
            expected_standard_error = batch_values.std(axis=1, ddof=1) / np.sqrt(4)
            expected_relative_error = np.divide(
                expected_standard_error,
                np.abs(batch_values.mean(axis=1)),
                out=np.full(3, np.nan),
                where=batch_values.mean(axis=1) != 0,
            )

            np.testing.assert_allclose(mean, expected_mean, rtol=1e-6)
            np.testing.assert_allclose(
                relative_error[:2], expected_relative_error[:2], rtol=1e-6
            )
            self.assertTrue(np.isnan(relative_error[2]))
            summary = summarize_relative_error(
                relative_error,
                inside_mask=np.array([True, True, True]),
            )
            self.assertEqual(summary["cells_with_error_estimate"], 2)
            self.assertEqual(summary["cells_relative_error_below_1_percent"], 1)
            self.assertAlmostEqual(
                summary["relative_error_median"],
                float(np.mean(expected_relative_error[:2])),
            )
            del mean
            del relative_error

    def test_chunked_tally_applies_a_distinct_heating_scale_per_cell(self):
        import h5py

        from sim_gamma.simulators.openmc.structured_postprocess import (
            read_tally_statistics_chunked,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            statepoint = root / "statepoint.heating.h5"
            batch_values = np.array([[8.0, 10.0], [18.0, 22.0]])
            with h5py.File(statepoint, "w") as output:
                results = output.create_dataset("tallies/tally 8/results", (2, 1, 2))
                results[:, 0, 0] = batch_values.sum(axis=1)
                results[:, 0, 1] = np.square(batch_values).sum(axis=1)

            mean, relative_error = read_tally_statistics_chunked(
                statepoint,
                tally_id=8,
                num_realizations=2,
                expected_cells=2,
                mean_destination=root / "heating-mean.float32",
                relative_error_destination=root / "heating-error.float32",
                scale=np.array([2.0, 3.0]),
                chunk_cells=1,
            )

            np.testing.assert_allclose(mean, [18.0, 60.0], rtol=1e-6)
            self.assertTrue(np.all(np.isfinite(relative_error)))
            del mean
            del relative_error

    def test_direct_evaluation_tally_produces_stable_statistics_separately(self):
        import h5py

        from sim_gamma.simulators.openmc.structured_postprocess import (
            summarize_statepoint_tally,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            statepoint = root / "statepoint.evaluation.h5"
            batch_values = np.array(
                [
                    [9.0, 10.0, 11.0, 10.0],
                    [18.0, 20.0, 22.0, 20.0],
                ]
            )
            with h5py.File(statepoint, "w") as output:
                results = output.create_dataset("tallies/tally 9/results", (2, 1, 2))
                results[:, 0, 0] = batch_values.sum(axis=1)
                results[:, 0, 1] = np.square(batch_values).sum(axis=1)
                tally = output["tallies/tally 9"]
                tally.create_dataset("name", data=np.bytes_("FluxEvaluation"))
                tally.create_dataset("n_realizations", data=4)
                tally.create_dataset("filters", data=np.array([4], dtype=np.int32))
                mesh_filter = output.create_group("tallies/filters/filter 4")
                mesh_filter.create_dataset("type", data=np.bytes_("mesh"))
                mesh_filter.create_dataset("bins", data=12)
                mesh = output.create_group("tallies/meshes/mesh 12")
                mesh.create_dataset("type", data=np.bytes_("regular"))
                mesh.create_dataset("dimension", data=np.array([2, 1, 1]))
                mesh.create_dataset("lower_left", data=np.array([0.0, 0.0, 0.0]))
                mesh.create_dataset("upper_right", data=np.array([6.0, 3.0, 3.0]))

            summary = summarize_statepoint_tally(
                statepoint,
                root,
                dose_coeff=27.0,
                tally_name="FluxEvaluation",
                inside_mask=np.array([True, True]),
                chunk_cells=1,
            )

            self.assertEqual(summary["method"], "direct-coarse-mesh-tally")
            self.assertEqual(summary["cell_size_cm"], [3.0, 3.0, 3.0])
            self.assertAlmostEqual(summary["minimum_dose"], 10.0)
            self.assertAlmostEqual(summary["maximum_dose"], 20.0)
            self.assertAlmostEqual(summary["average_dose"], 15.0)
            self.assertAlmostEqual(summary["dose_uniformity_ratio"], 2.0)
            self.assertFalse(list(root.glob(".*evaluation*.tmp")))

    def test_load_translation_follows_the_selected_work_section(self):
        from sim_gamma.workers.monte_carlo_cli import translation_to_target_center

        native_bounds = [[-60.0, -210.0, -75.0], [60.0, -110.0, 75.0]]
        np.testing.assert_allclose(
            translation_to_target_center(native_bounds, [1.5, 2.5, 3.5]),
            [1.5, 162.5, 3.5],
        )

    def test_reads_only_the_tally_sum_in_chunks_and_preserves_every_cell(self):
        import h5py

        from sim_gamma.simulators.openmc.structured_postprocess import read_tally_mean_chunked
        from sim_gamma.simulators.openmc.structured_postprocess import _statepoint_metadata

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            statepoint = root / "statepoint.synthetic.h5"
            sums = np.arange(1, 13, dtype=np.float64)
            with h5py.File(statepoint, "w") as output:
                results = output.create_dataset("tallies/tally 7/results", (12, 1, 2))
                results[:, 0, 0] = sums
                results[:, 0, 1] = sums * sums
                tally = output["tallies/tally 7"]
                tally.create_dataset("name", data=np.bytes_("Flux"))
                tally.create_dataset("n_realizations", data=4)
                tally.create_dataset("filters", data=np.array([3], dtype=np.int32))
                mesh_filter = output.create_group("tallies/filters/filter 3")
                mesh_filter.create_dataset("type", data=np.bytes_("mesh"))
                mesh_filter.create_dataset("bins", data=11)
                mesh = output.create_group("tallies/meshes/mesh 11")
                mesh.create_dataset("type", data=np.bytes_("regular"))
                mesh.create_dataset("dimension", data=np.array([2, 3, 2]))
                mesh.create_dataset("lower_left", data=np.array([-2.0, -3.0, -1.0]))
                mesh.create_dataset("upper_right", data=np.array([2.0, 3.0, 1.0]))

            dose = read_tally_mean_chunked(
                statepoint,
                tally_id=7,
                num_realizations=4,
                expected_cells=12,
                destination=root / "dose.float32",
                scale=2.5,
                chunk_cells=3,
            )

            self.assertEqual(dose.dtype, np.float32)
            self.assertEqual(dose.size, 12)
            np.testing.assert_allclose(dose, sums * 2.5 / 4, rtol=1e-6)
            tally_id, realizations, mesh_spec = _statepoint_metadata(statepoint, "Flux")
            self.assertEqual((tally_id, realizations), (7, 4))
            self.assertEqual(mesh_spec.dimension, (2, 3, 2))
            del dose

    def test_compact_mesh_filter_does_not_materialize_cell_indices(self):
        import openmc

        from sim_gamma.simulators.openmc.engine import CompactRegularMeshFilter

        mesh = openmc.RegularMesh(mesh_id=91)
        mesh.dimension = (1000, 1000, 100)
        mesh.lower_left = (-1.0, -1.0, -1.0)
        mesh.upper_right = (1.0, 1.0, 1.0)
        mesh_filter = CompactRegularMeshFilter(mesh, filter_id=92)

        self.assertEqual(mesh_filter.num_bins, 100_000_000)
        self.assertFalse(hasattr(mesh_filter, "_bins"))
        element = mesh_filter.to_xml_element()
        self.assertEqual(element.get("type"), "mesh")
        self.assertEqual(element.find("bins").text, "91")

    def test_writes_compressed_vti_and_cuts_without_delaunay(self):
        import vtk
        from vtk.util.numpy_support import vtk_to_numpy

        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            write_structured_dose_artifacts,
        )

        mesh = RegularMeshSpec(
            dimension=(2, 3, 2),
            lower_left=(-2.0, -3.0, -1.0),
            upper_right=(2.0, 3.0, 1.0),
        )
        values = np.arange(1, mesh.cell_count + 1, dtype=np.float32)
        material_ids = np.resize(np.array([1, 2], dtype=np.uint16), mesh.cell_count)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = write_structured_dose_artifacts(
                values,
                mesh,
                root,
                material_ids=material_ids,
                material_labels=[{"id": 1, "name": "product"}, {"id": 2, "name": "package"}],
            )

            reader = vtk.vtkXMLImageDataReader()
            reader.SetFileName(str(root / "dose_distribution.vti"))
            reader.Update()
            image = reader.GetOutput()
            restored = vtk_to_numpy(image.GetCellData().GetScalars())
            restored_materials = vtk_to_numpy(image.GetCellData().GetArray("material_id"))

            self.assertEqual(image.GetDimensions(), (3, 4, 3))
            np.testing.assert_array_equal(restored, values)
            np.testing.assert_array_equal(restored_materials, material_ids)
            self.assertEqual(result["total_cells"], 12)
            self.assertEqual(result["active_cells"], 12)
            self.assertEqual(result["vtk_format"], "vti")
            self.assertEqual(result["materials"][1]["name"], "package")
            self.assertTrue((root / "isodose_xy.png").is_file())
            self.assertTrue((root / "isodose_yz.png").is_file())
            self.assertIn(b"vtkZLibDataCompressor", (root / "dose_distribution.vti").read_bytes()[:512])

    def test_classifies_material_cells_independently(self):
        import trimesh

        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            build_material_ids,
        )

        mesh = RegularMeshSpec((4, 2, 2), (-2.0, -1.0, -1.0), (2.0, 1.0, 1.0))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
            left.apply_translation((-1.0, 0.0, 0.0))
            right = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
            right.apply_translation((1.0, 0.0, 0.0))
            left_path = root / "left.stl"
            right_path = root / "right.stl"
            left.export(left_path)
            right.export(right_path)

            material_ids, labels = build_material_ids(
                {"left": left_path, "right": right_path},
                mesh,
                root / "cache",
                chunk_cells=3,
            )

            self.assertEqual(labels, [{"id": 1, "name": "left"}, {"id": 2, "name": "right"}])
            self.assertEqual(int(np.count_nonzero(material_ids == 1)), 8)
            self.assertEqual(int(np.count_nonzero(material_ids == 2)), 8)

    def test_surface_mask_is_chunked_and_reused_from_cache(self):
        import trimesh

        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            build_or_load_surface_mask,
        )

        mesh = RegularMeshSpec((4, 4, 4), (-2.0, -2.0, -2.0), (2.0, 2.0, 2.0))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            surface_path = root / "step-triangulated.stl"
            trimesh.creation.box(extents=(2.0, 2.0, 2.0)).export(surface_path)

            first = build_or_load_surface_mask(
                surface_path,
                mesh,
                root / "cache",
                chunk_cells=5,
            )
            with patch("trimesh.load_mesh", side_effect=AssertionError("No debe recalcularse")):
                second = build_or_load_surface_mask(
                    surface_path,
                    mesh,
                    root / "cache",
                    chunk_cells=7,
                )

            self.assertEqual(int(first.sum()), 8)
            np.testing.assert_array_equal(second, first)
            self.assertEqual(len(list((root / "cache").glob("load-mask-*.npz"))), 1)


class SourceInventoryTests(unittest.TestCase):
    def test_reads_every_configured_source_and_applies_decay(self):
        inventory = SourceInventory(PROJECT_ROOT / "data" / "config" / "sources.json")
        result = inventory.sources(date(2026, 8, 27), "Co-60")
        configured = json.loads(
            (PROJECT_ROOT / "data" / "config" / "sources.json").read_text(
                encoding="utf-8"
            )
        )["sources"]

        self.assertEqual(result["count"], len(configured))
        self.assertGreater(result["count"], 0)
        self.assertEqual(result["half_life_days"], 1925.23)
        self.assertLess(
            result["sources"][0]["current_activity_bq"],
            result["sources"][0]["initial_activity_bq"],
        )

    def test_lists_only_configured_radionuclides(self):
        inventory = SourceInventory(PROJECT_ROOT / "data" / "config" / "sources.json")

        self.assertEqual(set(inventory.radionuclides()), {"Co-60", "Cs-137"})

    def test_configuration_changes_are_versioned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_file = root / "sources.json"
            source_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "lower_point": [0, 0, 0],
                                "upper_point": [0, 0, 1],
                                "intensity": 10,
                                "initial_reading_date": "2022-01-01",
                            }
                        ],
                        "radionuclides": {"Co-60": {"half_life": 1925.23}},
                    }
                ),
                encoding="utf-8",
            )
            history = root / "history"
            inventory = SourceInventory(source_file, history)
            inventory.add(
                {
                    "identifier": "SRC-NUEVA",
                    "lower_point": [1, 0, 0],
                    "upper_point": [1, 0, 1],
                    "intensity": 20,
                    "initial_reading_date": "2023-01-01",
                }
            )

            self.assertEqual(len(list(history.glob("sources-*.json"))), 1)
            result = inventory.sources(date(2026, 8, 27))
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["sources"][0]["id"], "SRC-001")
            self.assertEqual(result["sources"][1]["id"], "SRC-NUEVA")

    def test_rejects_duplicate_source_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_file = Path(temporary) / "sources.json"
            source_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "identifier": "FUENTE-A",
                                "lower_point": [0, 0, 0],
                                "upper_point": [0, 0, 1],
                                "intensity": 10,
                                "initial_reading_date": "2022-01-01",
                            }
                        ],
                        "radionuclides": {"Co-60": {"half_life": 1925.23}},
                    }
                ),
                encoding="utf-8",
            )
            inventory = SourceInventory(source_file)

            with self.assertRaisesRegex(ValueError, "Ya existe"):
                inventory.add(
                    {
                        "identifier": "fuente-a",
                        "lower_point": [1, 0, 0],
                        "upper_point": [1, 0, 1],
                        "intensity": 20,
                        "initial_reading_date": "2023-01-01",
                    }
                )


class CaseStoreTests(unittest.TestCase):
    def test_creates_an_isolated_case_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            for filename in ("sources.json", "materials.json", "work_sections.json"):
                (config_root / filename).write_text("{}", encoding="utf-8")
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)
            manifest = store.create("Carga de prueba")
            root = Path(temporary) / "output" / "runs" / manifest["case_id"]

            self.assertTrue((root / "manifest.json").is_file())
            for directory in CASE_DIRECTORIES:
                self.assertTrue((root / directory).is_dir())
            self.assertEqual(manifest["step_input"]["original_name"], "load.step")
            updated = store.set_step_input(manifest["case_id"], "carga-piloto.step", 4)
            self.assertEqual(updated["step_input"]["original_name"], "carga-piloto.step")

            store.delete(manifest["case_id"])

            self.assertFalse(root.exists())
            self.assertEqual(list(store.list()), [])
            with self.assertRaises(FileNotFoundError):
                store.get(manifest["case_id"])

    def test_step_materials_are_exact_and_keep_air_calibration_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            (config_root / "sources.json").write_text("{}", encoding="utf-8")
            (config_root / "work_sections.json").write_text("{}", encoding="utf-8")
            (config_root / "materials.json").write_text(
                json.dumps({"materials": [{
                    "name": "air",
                    "elements": {"C": 0.000124, "N": 0.755267, "O": 0.231781, "Ar": 0.012827},
                    "density": 0.00120479,
                    "fraction_type": "weight",
                    "mu_en_over_rho": {"1.173": 0.02921},
                    "dosimeter_medium": "alanine",
                }]}),
                encoding="utf-8",
            )
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)
            case_id = store.create("Multimaterial")["case_id"]

            updated = store.set_step_materials(
                case_id,
                [
                    {"name": "air", "elements": {"C": 0.000124, "N": 0.755267, "O": 0.231781, "Ar": 0.012827}, "density": 0.00120479, "fraction_type": "weight"},
                    {"name": "water", "elements": {"H": 0.111894, "O": 0.888106}, "density": 1.0},
                    {"name": "aluminum", "elements": {"Al": 1.0}, "density": 2.7},
                ],
                ["air", "water", "aluminum"],
            )
            snapshot = json.loads(
                (store.root(case_id) / "input" / "materials.json").read_text(encoding="utf-8")
            )

            self.assertEqual(updated["step_material_tags"], ["air", "aluminum", "water"])
            self.assertEqual(
                {item["name"] for item in updated["step_materials"]},
                {"air", "water", "aluminum"},
            )
            self.assertEqual(
                {item["name"]: item["fraction_type"] for item in updated["step_materials"]},
                {"air": "weight", "water": "weight", "aluminum": "weight"},
            )
            self.assertEqual(snapshot["materials"][0]["dosimeter_medium"], "alanine")
            self.assertEqual(snapshot["materials"][0]["mu_en_over_rho"]["1.173"], 0.02921)
            self.assertEqual(
                {item["name"]: item["density"] for item in snapshot["materials"]},
                {"air": 0.00120479, "water": 1.0, "aluminum": 2.7},
            )

            with self.assertRaisesRegex(ValueError, "faltan: aluminum, water"):
                store.set_step_materials(
                    case_id,
                    [{"name": "air", "elements": {"N": 1.0}, "density": 0.001}],
                    ["air", "water", "aluminum"],
                )


class CadPreviewMaterialTests(unittest.TestCase):
    def test_material_tag_matches_the_dagmc_component_prefix(self):
        from sim_gamma.application.services.cad_preview import CadPreviewService

        self.assertEqual(CadPreviewService.material_tag("water_001"), "water")
        self.assertEqual(CadPreviewService.material_tag("AIR_004"), "air")
        with self.assertRaises(ValueError):
            CadPreviewService.material_tag("_001")


class CadConverterMaterialTests(unittest.TestCase):
    def test_isolated_converter_receives_material_tags_in_component_order(self):
        from sim_gamma.simulators.openmc import engine

        completed = Mock(stdout="", returncode=0)
        with patch.object(engine, "_environment_runner", return_value="/usr/bin/conda"), patch(
            "sim_gamma.simulators.openmc.engine.subprocess.run",
            return_value=completed,
        ) as run:
            engine._run_isolated_cad_converter(
                "/tmp/two-material-case",
                "world",
                material_tags=["air", "water", "aluminum"],
            )

        command = run.call_args.args[0]
        self.assertEqual(
            json.loads(command[-1]),
            ["air", "water", "aluminum"],
        )


class DeterministicJobControlTests(unittest.TestCase):
    def _jobs_and_case(self, root: Path):
        config_root = root / "config"
        config_root.mkdir()
        for filename in ("sources.json", "materials.json", "work_sections.json"):
            (config_root / filename).write_text("{}", encoding="utf-8")
        step = root / "load.step"
        step.write_text("STEP", encoding="utf-8")
        store = CaseStore(root / "output", config_root, step)
        manifest = store.create("Control de proceso")
        return DeterministicJobs(store, root), manifest["case_id"], store.root(manifest["case_id"])

    def test_repairs_running_status_when_result_already_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs, case_id, case_root = self._jobs_and_case(Path(temporary))
            deterministic = case_root / "deterministic"
            (deterministic / "status.json").write_text(
                json.dumps({"case_id": case_id, "run_id": "run-current", "state": "running", "progress": 10}),
                encoding="utf-8",
            )
            (deterministic / "result.json").write_text(
                json.dumps({"run_id": "run-current"}), encoding="utf-8"
            )

            status = jobs.status(case_id)

            self.assertEqual(status["state"], "completed")
            self.assertEqual(status["progress"], 100)

    def test_previous_result_does_not_complete_a_new_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs, case_id, case_root = self._jobs_and_case(Path(temporary))
            deterministic = case_root / "deterministic"
            (deterministic / "status.json").write_text(
                json.dumps({"case_id": case_id, "run_id": "new", "state": "running", "progress": 10}),
                encoding="utf-8",
            )
            (deterministic / "result.json").write_text(
                json.dumps({"run_id": "old"}), encoding="utf-8"
            )

            status = jobs.status(case_id)

            self.assertEqual(status["state"], "interrupted")

    def test_marks_orphaned_running_status_as_interrupted(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs, case_id, case_root = self._jobs_and_case(Path(temporary))
            (case_root / "deterministic" / "status.json").write_text(
                json.dumps({"case_id": case_id, "state": "running", "progress": 10}),
                encoding="utf-8",
            )

            status = jobs.status(case_id)

            self.assertEqual(status["state"], "interrupted")
            self.assertIn("ya no está activo", status["error"])

    def test_cancel_stops_the_entire_worker_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs, case_id, case_root = self._jobs_and_case(Path(temporary))
            deterministic = case_root / "deterministic"
            request_path = deterministic / "request.json"
            request_path.write_text("{}", encoding="utf-8")
            (deterministic / "status.json").write_text(
                json.dumps({"case_id": case_id, "state": "running", "progress": 28}),
                encoding="utf-8",
            )
            (deterministic / "worker.json").write_text(
                json.dumps({
                    "case_id": case_id,
                    "pid": 43210,
                    "process_group_id": 43210,
                    "request_path": str(request_path.resolve()),
                }),
                encoding="utf-8",
            )

            with patch.object(jobs, "_worker_is_active", return_value=True), patch.object(
                jobs, "_process_group_exists", return_value=False
            ), patch("sim_gamma.application.services.deterministic_jobs.os.killpg") as kill_group:
                status = jobs.cancel(case_id)

            kill_group.assert_called_once_with(43210, 15)
            self.assertEqual(status["state"], "cancelled")
            self.assertEqual(status["progress"], 28)
            self.assertFalse((deterministic / "worker.json").exists())

    def test_deterministic_job_is_not_started_implicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            for filename in ("sources.json", "materials.json", "work_sections.json"):
                (config_root / filename).write_text("{}", encoding="utf-8")
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)
            manifest = store.create("Sin ejecución")
            jobs = DeterministicJobs(store, root)
            status = jobs.status(manifest["case_id"])

            self.assertEqual(status["state"], "not-started")
            self.assertFalse(status["monte_carlo_started"])

    def test_case_keeps_a_snapshot_of_the_selected_simulation_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            for filename in ("sources.json", "materials.json", "work_sections.json"):
                (config_root / filename).write_text("{}", encoding="utf-8")
            parameters = {
                "radionuclide": "Co-60",
                "movement": {
                    "default_work_section": "A",
                    "machine_angle_start_degrees": 0,
                    "machine_angle_stop_degrees": 360,
                    "machine_angle_step_degrees": 10,
                    "angular_positions_range": [2, 9],
                    "cad_rotation_degrees": 0,
                    "length_complete_lower_range_mm": [-800, 200],
                    "length_complete_upper_range_mm": [200, 700],
                    "resolution_length_mm": 500,
                },
                "deterministic": {"num_points": 12000},
                "compute": {"cpu_cores": 4},
                "monte_carlo": {
                    "cell_size_cm": 3,
                    "particles": 100000000,
                    "dose_tally": "heating",
                },
            }
            alternate_parameters = json.loads(json.dumps(parameters))
            alternate_parameters["deterministic"]["num_points"] = 24000
            alternate_parameters["compute"]["cpu_cores"] = 8
            (config_root / "simulation_profiles.json").write_text(
                json.dumps({
                    "profiles": [
                        {
                            "profile_id": "perfil-cuatro-cpu",
                            "name": "Perfil cuatro CPU",
                            "parameters": parameters,
                        },
                        {
                            "profile_id": "perfil-ocho-cpu",
                            "name": "Perfil ocho CPU",
                            "parameters": alternate_parameters,
                        },
                    ]
                }),
                encoding="utf-8",
            )
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)

            manifest = store.create(
                "Caso con perfil",
                simulation_profile_id="perfil-cuatro-cpu",
            )

            self.assertEqual(manifest["simulation_profile"]["name"], "Perfil cuatro CPU")
            self.assertEqual(manifest["simulation_parameters"]["compute"]["cpu_cores"], 4)
            self.assertEqual(
                manifest["simulation_parameters"]["monte_carlo"]["dose_tally"],
                "heating",
            )
            self.assertEqual(manifest["deterministic_parameters"]["cores"], 4)

            # Editar el perfil global no modifica la instantánea de un caso ya creado.
            profiles_path = config_root / "simulation_profiles.json"
            profiles_document = json.loads(profiles_path.read_text(encoding="utf-8"))
            profiles_document["profiles"][0]["parameters"]["monte_carlo"]["cell_size_cm"] = 1
            profiles_document["profiles"][0]["parameters"]["monte_carlo"]["dose_tally"] = "flux"
            profiles_path.write_text(json.dumps(profiles_document), encoding="utf-8")
            persisted = store.get(manifest["case_id"])
            self.assertEqual(persisted["simulation_parameters"]["monte_carlo"]["cell_size_cm"], 3)
            self.assertEqual(
                persisted["simulation_parameters"]["monte_carlo"]["dose_tally"],
                "heating",
            )

            updated = store.update(
                manifest["case_id"],
                "Caso editado",
                "Nueva descripción",
                {"work_section": "B"},
                "perfil-ocho-cpu",
            )
            snapshot = json.loads(
                (
                    root
                    / "output"
                    / "runs"
                    / manifest["case_id"]
                    / "input"
                    / "simulation_parameters.json"
                ).read_text(encoding="utf-8")
            )

            self.assertEqual(updated["name"], "Caso editado")
            self.assertEqual(updated["simulation_profile"]["name"], "Perfil ocho CPU")
            self.assertEqual(updated["deterministic_parameters"]["work_section"], "B")
            self.assertEqual(updated["deterministic_parameters"]["num_points"], 24000)
            self.assertEqual(snapshot["compute"]["cpu_cores"], 8)
            self.assertEqual(snapshot["movement"]["default_work_section"], "B")


class MovementExportTests(unittest.TestCase):
    def test_preserves_the_external_contract_exactly(self):
        document = build_movement_document(
            angles=[0, 180],
            length_points=[-800, -300, 200, 700],
            best_length_points=[-800, 700],
            work_section="A",
            work_section_position=[0, -1600, 0],
            average_dose=6.83,
            description="Trayectoria de prueba.",
        )
        validate_movement_document(document)
        self.assertEqual(set(document), set(REQUIRED_KEYS))

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "movements_parameters_optimized.json"
            write_movement_document(destination, document)
            with destination.open("r", encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), document)

    def test_case_description_is_included_in_the_published_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "deterministic").mkdir()
            (root / "input").mkdir()
            (root / "manifest.json").write_text(
                json.dumps({"description": "Irradiación del lote de validación."}),
                encoding="utf-8",
            )
            (root / "deterministic" / "request.json").write_text(
                json.dumps({"parameters": {"work_section": "A"}}),
                encoding="utf-8",
            )
            (root / "deterministic" / "result.json").write_text(
                json.dumps(
                    {
                        "step_angle_degrees_best_real": [0, 180],
                        "length_points_mm": [-800, -300, 200, 700],
                        "best_length_points_mm": [-800, 700],
                        "average_dose": 6.83,
                    }
                ),
                encoding="utf-8",
            )
            (root / "input" / "work_sections.json").write_text(
                json.dumps({"positions": {"A": [0, -1600, 0]}}),
                encoding="utf-8",
            )

            document = build_from_deterministic_case(root)

            self.assertEqual(
                document["description"], "Irradiación del lote de validación."
            )
            self.assertEqual(document["work_section_position"], [0, -1600, 0])


class ConfigurationStoreTests(unittest.TestCase):
    def test_materials_and_work_sections_are_editable_and_versioned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            config.mkdir()
            (config / "materials.json").write_text(
                json.dumps({"materials": []}), encoding="utf-8"
            )
            (config / "work_sections.json").write_text(
                json.dumps({"positions": {}}), encoding="utf-8"
            )
            store = ConfigurationStore(config, root / "history")

            store.save_material(
                {
                    "name": "product",
                    "elements": {"H": 0.1, "O": 0.9},
                    "density": 1.0,
                    "mu_en_over_rho": {"1.173": 0.03},
                }
            )
            store.save_work_section("A", [0, -1600, 0])

            self.assertEqual(store.materials()[0]["name"], "product")
            self.assertEqual(store.work_sections()["A"], [0, -1600, 0])
            self.assertEqual(len(list((root / "history" / "materials").glob("*.json"))), 1)
            self.assertEqual(len(list((root / "history" / "work_sections").glob("*.json"))), 1)

    def test_simulation_profiles_can_be_saved_and_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            config.mkdir()
            (config / "simulation_profiles.json").write_text(
                json.dumps({"profiles": []}), encoding="utf-8"
            )
            store = ConfigurationStore(config, root / "history")
            parameters = {
                "radionuclide": "Co-60",
                "movement": {},
                "deterministic": {"num_points": 10000},
                "compute": {"cpu_cores": 8},
                "monte_carlo": {
                    "cell_size_cm": 3,
                    "particles": 100000000,
                    "dose_tally": "heating",
                },
            }

            profile = store.save_simulation_profile("Configuración HPC", parameters)

            self.assertEqual(profile["profile_id"], "configuracion-hpc")
            self.assertEqual(store.simulation_profiles()[0]["parameters"], parameters)

            alternative_parameters = json.loads(json.dumps(parameters))
            alternative_parameters["monte_carlo"]["dose_tally"] = "alanine"
            alternative_parameters["monte_carlo"]["particles"] = 10_000_000_000
            alternative = store.save_simulation_profile(
                "Configuración alternativa", alternative_parameters
            )
            persisted_alternative = next(
                item
                for item in store.simulation_profiles()
                if item["profile_id"] == alternative["profile_id"]
            )
            self.assertEqual(
                persisted_alternative["parameters"]["monte_carlo"],
                {
                    "cell_size_cm": 3,
                    "particles": 10_000_000_000,
                    "dose_tally": "alanine",
                },
            )

            store.delete_simulation_profile(profile["profile_id"])
            store.delete_simulation_profile(alternative["profile_id"])

            self.assertEqual(store.simulation_profiles(), [])
            self.assertEqual(
                len(list((root / "history" / "simulation_profiles").glob("*.json"))),
                4,
            )
            with self.assertRaises(KeyError):
                store.delete_simulation_profile(profile["profile_id"])


class MonteCarloGuardTests(unittest.TestCase):
    def test_systemd_unit_names_are_scoped_and_validated(self):
        unit = MonteCarloJobs._systemd_unit_name("ABC.123/unsafe")

        self.assertEqual(unit, "irradium-mc-abc-123-unsafe")
        self.assertEqual(MonteCarloJobs._validated_systemd_unit(unit + ".service"), unit)
        self.assertIsNone(MonteCarloJobs._validated_systemd_unit("unrelated.service"))

    def test_active_systemd_worker_is_not_misreported_as_interrupted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            for filename in ("sources.json", "materials.json", "work_sections.json"):
                (config_root / filename).write_text("{}", encoding="utf-8")
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)
            case_id = store.create("Servicio persistente")['case_id']
            monte_carlo = store.root(case_id) / "monte_carlo"
            (monte_carlo / "request.json").write_text(
                json.dumps({"systemd_unit": "irradium-mc-test123"}),
                encoding="utf-8",
            )
            expected = {"case_id": case_id, "state": "running", "progress": 20}
            (monte_carlo / "status.json").write_text(json.dumps(expected), encoding="utf-8")
            jobs = MonteCarloJobs(store)

            with patch.object(
                DeterministicJobs, "_matching_processes", return_value=[]
            ), patch.object(jobs, "_systemd_unit_active", return_value=True):
                status = jobs.status(case_id)

            self.assertEqual(status, expected)

    def test_status_reaps_finished_worker_handles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            for filename in ("sources.json", "materials.json", "work_sections.json"):
                (config_root / filename).write_text("{}", encoding="utf-8")
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)
            case_id = store.create("Recolección de proceso")['case_id']

            for jobs in (MonteCarloJobs(store), DeterministicJobs(store, root)):
                child = Mock()
                child.poll.return_value = 0
                jobs._children[43210] = child

                jobs.status(case_id)

                child.poll.assert_called_once_with()
                self.assertNotIn(43210, jobs._children)

    def test_monte_carlo_jobs_exposes_a_safe_stop_before_execution_is_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            config_root.mkdir()
            for filename in ("sources.json", "materials.json", "work_sections.json"):
                (config_root / filename).write_text("{}", encoding="utf-8")
            step = root / "load.step"
            step.write_text("STEP", encoding="utf-8")
            store = CaseStore(root / "output", config_root, step)
            case_id = store.create("Monte Carlo control")["case_id"]
            monte_carlo = store.root(case_id) / "monte_carlo"
            (monte_carlo / "request.json").write_text("{}", encoding="utf-8")
            (monte_carlo / "status.json").write_text(
                json.dumps({"case_id": case_id, "state": "running", "progress": 30}),
                encoding="utf-8",
            )
            jobs = MonteCarloJobs(store)

            with patch.object(
                DeterministicJobs, "_matching_processes", return_value=[(54321, 54321)]
            ), patch.object(
                DeterministicJobs, "_process_group_exists", return_value=False
            ), patch("sim_gamma.application.services.monte_carlo_jobs.os.killpg") as kill_group:
                status = jobs.cancel(case_id)

            kill_group.assert_called_once_with(54321, 15)
            self.assertEqual(status["state"], "cancelled")

    def test_prepare_preserves_the_global_cpu_limit_without_running_openmc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            movements = root / "movements.json"
            geometry = root / "load.step"
            movements.write_text("{}", encoding="utf-8")
            geometry.write_text("STEP", encoding="utf-8")
            request = MonteCarloRequest(
                case_directory=root,
                movement_parameters=movements,
                geometry_step=geometry,
                particles=1000,
                cell_size_mm=30,
                cpu_cores=6,
            )

            prepared = OpenMCBackend(execution_enabled=False).prepare(request)

            self.assertEqual(prepared["cpu_cores"], 6)

    def test_execute_is_blocked_without_importing_or_running_openmc(self):
        backend = OpenMCBackend(execution_enabled=False)
        with self.assertRaisesRegex(RuntimeError, "deshabilitada"):
            backend.execute(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
