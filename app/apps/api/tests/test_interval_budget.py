import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from apps.api.sim_gamma_api.route_models import RouteStudyRequest
from sim_gamma.domain.interval_budget import plan_interval_histories, reference_budgets
from sim_gamma.simulators.openmc.structured_postprocess import (
    RegularMeshSpec, summarize_relative_error, write_compressed_vti,
)
from sim_gamma.workers import monte_carlo_cli


class IntervalBudgetTests(unittest.TestCase):
    def test_allocation_uses_absolute_variances_for_observed_zeros(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (2, 1, 1))
            positions = []
            for index, (rates, errors) in enumerate((([0, 10], [0, 1]), ([10, 0], [1, 0]))):
                folder = root / str(index)
                folder.mkdir()
                write_compressed_vti(rates, mesh, folder / 'dose_distribution.vti',
                                     relative_error=[np.nan, np.nan], absolute_std_dev=errors)
                (folder / 'result.json').write_text(json.dumps({'transport_wall_seconds': 1}))
                positions.append({'summary': f'{index}/result.json', 'simulated_particles': 4_000_000})
            plan = plan_interval_histories(root, positions, .02, 1_000_000_000, return_projection=True)
            self.assertLessEqual(plan['projected_relative_error_maximum'], .02)
            self.assertGreater(min(plan['histories']), 8_000_000)

    def test_allocation_prioritizes_the_position_that_dominates_variance(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (4, 2, 2))
            positions = []
            for index, dose in enumerate(([10, 10], [1, 1])):
                output = root / str(index)
                output.mkdir()
                write_compressed_vti(dose, mesh, output / "dose_distribution.vti", relative_error=[.03, .03])
                summary = output / "result.json"
                summary.write_text(json.dumps({"transport_wall_seconds": 10}))
                positions.append({"summary": str(summary.relative_to(root)), "simulated_particles": 4_000_000})
            planned = plan_interval_histories(root, positions, .02, 100_000_000)
            self.assertGreater(planned[0], planned[1])
            predicted = np.sqrt((10 * .03)**2 * 4e6 / planned[0] + (.03)**2 * 4e6 / planned[1]) / 11
            self.assertLessEqual(predicted, .02)
            self.assertEqual(plan_interval_histories(root, positions, .02, 4_000_000), [4_000_000] * 2)

    def test_previous_variances_guide_budgets_without_reusing_samples(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "results/studies"
            output.mkdir(parents=True)
            mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (4, 2, 2))
            write_compressed_vti([1, 1], mesh, output / "dose_distribution.vti", relative_error=[.03, .03])
            summary = output / "result.json"
            summary.write_text(json.dumps({"transport_wall_seconds": 1}))
            position = {"name": "I1", "yaw_degrees": 0, "center_mm": [0, 0, 0],
                        "summary": str(summary.relative_to(root)), "simulated_particles": 4_000_000}
            source = output / "previous.json"
            reference = {"complete": True, "positions": [position], "transport_geometry_revision": 2}
            source.write_text(json.dumps(reference))
            request = {"budget_reference_run_id": "previous"}
            plan = reference_budgets(root, request, [position], .01, 100_000_000)
            self.assertGreater(plan[0], 4_000_000)
            self.assertEqual(reference_budgets(root, request, [position], .01, 2_000_000), [2_000_000])
            self.assertIsNone(reference_budgets(root, request, [{**position, "yaw_degrees": -180}], .01, 100_000_000))
            source.write_text(json.dumps({**reference, "quick": True}))
            self.assertIsNone(reference_budgets(root, request, [position], .01, 100_000_000))
            # A more precise guide must not force its large sample budget onto
            # the new study when fewer histories can meet the new objective.
            write_compressed_vti([1, 1], mesh, output / "dose_distribution.vti", relative_error=[.005, .005])
            position["simulated_particles"] = 100_000_000
            source.write_text(json.dumps(reference))
            smaller = reference_budgets(root, request, [position], .01, 1_000_000_000)
            self.assertGreater(smaller[0], 4_000_000)
            self.assertLess(smaller[0], 100_000_000)

    def test_interval_uses_available_threads_and_preserves_an_explicit_limit(self):
        from sim_gamma.domain.route_study import study_simulation
        with patch("sim_gamma.domain.route_study.available_cpu_threads", return_value=12):
            automatic = study_simulation({}, {"accumulated_error_target": .02})
            self.assertEqual(automatic["compute"]["cpu_cores"], 12)
            explicit = study_simulation({}, {"accumulated_error_target": .02, "cpu_cores": 4})
            self.assertEqual(explicit["compute"]["cpu_cores"], 4)
            self.assertEqual(study_simulation({}, {})["compute"]["cpu_cores"], 12)

    def test_interval_request_uses_its_own_convergence_mode(self):
        request = RouteStudyRequest(start=0, end=1, accumulated_error_target=.02, adaptive_statistics=False)
        self.assertEqual(request.accumulated_error_target, .02)
        self.assertEqual(RouteStudyRequest(start=0, end=55, accumulated_error_target=.01, adaptive_statistics=False).accumulated_error_target, .01)
        with self.assertRaises(ValueError):
            RouteStudyRequest(start=0, end=1, accumulated_error_target=.01, adaptive_statistics=False, budget_reference_run_id="../outside")
        for options in ({"adaptive_statistics": True}, {"adaptive_statistics": False, "quick": True}):
            with self.assertRaises(ValueError):
                RouteStudyRequest(start=0, end=1, accumulated_error_target=.02, **options)

    def test_refinement_preserves_previous_maps_and_checks_the_combined_maximum(self):
        for maximum, meets_target, early_stop in ((100_000_000, True, False), (4_000_000, False, False), (100_000_000, True, True)):
            with self.subTest(maximum=maximum, early_stop=early_stop), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "monte_carlo").mkdir()
                route = {"positions": [{"name": "I7", "center_mm": [0, 0, 0], "yaw_degrees": 0},
                                        {"name": "I8", "center_mm": [0, 20, 0], "yaw_degrees": -180}],
                         "particles": maximum, "batches": 100}
                route_path = root / "route.json"
                route_path.write_text(json.dumps(route))
                request = {"case_id": "test", "run_id": "run", "case_root": str(root), "irradiator_type": "IV",
                           "route_study": {"start": 0, "end": 1, "irradiation_date": "2026-11-06",
                                           "accumulated_error_target": .02, "adaptive_statistics": False}}
                mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (4, 2, 2))

                def fake_run(request_path, *, route_context):
                    index, _, _, _, configuration = route_context
                    refinement = configuration["refinement_pass"]
                    suffix = f"-r{refinement}" if refinement else ""
                    output = root / "monte_carlo/positions/run" / f"{index + 1:03d}{suffix}"
                    output.mkdir(parents=True)
                    count = configuration["particles"]
                    errors = np.full(2, .05 * np.sqrt(4_000_000 / count))
                    value = 10 if early_stop and index == 0 else 1
                    write_compressed_vti([value, value], mesh, output / "dose_distribution.vti", relative_error=errors)
                    summary = {"average_dose": value, "minimum_dose": value, "maximum_dose": value,
                               "simulated_particles": count, "transport_wall_seconds": count / 4e5,
                               "reused_histories": 4_000_000 if refinement else 0,
                               "active_cells": 2, **summarize_relative_error(errors)}
                    (output / "result.json").write_text(json.dumps(summary))

                with patch.object(monte_carlo_cli, "run", side_effect=fake_run), patch(
                    "sim_gamma.domain.interval_budget.plan_interval_histories",
                    side_effect=lambda *args: [40_000_000] * 2 if early_stop else plan_interval_histories(*args),
                ):
                    monte_carlo_cli.run_route(root / "request.json", request, route_path)
                result = json.loads((root / "results/studies/run.json").read_text())
                self.assertTrue(result["complete"])
                self.assertEqual(result["total_simulated_histories"], sum(p["simulated_particles"] for p in result["positions"]))
                self.assertEqual(result["interval_precision"]["meets_target"], meets_target)
                if meets_target:
                    self.assertLessEqual(result["interval_precision"]["relative_error_maximum"], .02)
                    self.assertGreater(result["completed_calculations"], 2)
                    if early_stop:
                        self.assertEqual(result["completed_calculations"], 3)
                        self.assertEqual(result["positions"][1]["simulated_particles"], 4_000_000)
                    self.assertTrue((root / "monte_carlo/positions/run/001/dose_distribution.vti").is_file())
                    self.assertIn("-r1", result["positions"][0]["summary"])
                else:
                    self.assertEqual(result["stop_reason"], "precision-limited")
                self.assertEqual(json.loads((root / "monte_carlo/status.json").read_text())["final_state"], "completed")


if __name__ == "__main__":
    unittest.main()
