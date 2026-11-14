import unittest
import tempfile
from pathlib import Path

from pydantic import ValidationError

from apps.api.sim_gamma_api.route_models import RouteConfiguration, RouteStudyRequest
from sim_gamma.domain.route_study import accumulated_precision, active_material_bins, precision_status, selected_position_indices, study_precision, study_simulation


class RouteStudyTests(unittest.TestCase):
    def test_selected_stops_keep_route_order_and_reject_unknown_or_repeated_names(self):
        positions = [{"name": name, "center_mm": [0, 0, 0], "yaw_degrees": yaw}
                     for name, yaw in [("I7", 0), ("I8", 180), ("S1", 180)]]
        self.assertEqual(selected_position_indices(positions, {"position_names": ["S1", "I7"]}), [0, 2])
        self.assertEqual(selected_position_indices(positions, {"start": 1, "end": 2}), [1, 2])
        for names in (["I7", "I7"], ["I7", "otra"], []):
            with self.assertRaises(ValueError):
                selected_position_indices(positions, {"position_names": names})
        with self.assertRaises(ValidationError):
            RouteConfiguration(positions=positions, calculation_positions=["otra"])

    def test_air_reference_line_source_matches_analytic_midplane_and_axial_limits(self):
        import math
        from sim_gamma.physics.deterministic.air_reference import line_source_fluence

        source = {"lower_point_mm": [0, 0, -10], "upper_point_mm": [0, 0, 10], "current_activity_bq": 20}
        self.assertAlmostEqual(line_source_fluence([20, 0, 0], [source], 5.5), 100 * 2 * math.atan(.5) / (4 * math.pi * 20))
        self.assertAlmostEqual(line_source_fluence([0, 0, 30], [source], 5.5), 100 * (1 / 20 - 1 / 40) / (4 * math.pi))
        with self.assertRaises(ValueError):
            line_source_fluence([0, 0, 0], [source], 5.5)

    def test_air_calibration_registers_a_half_turn_in_the_shared_load_mesh(self):
        import json
        from datetime import date

        import numpy as np
        from vtk import vtkXMLImageDataReader
        from vtk.util.numpy_support import vtk_to_numpy

        from sim_gamma.application.services.air_route_calibration import air_calibration_mesh, write_air_calibration

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            inputs.mkdir()
            (inputs / "sources.json").write_text(json.dumps({
                "radionuclides": {"Co-60": {"half_life": 1925, "emissions": [{"value": 1.173}]}},
                "sources": [{"lower_point": [100, 0, 0], "upper_point": [100, 0, 10],
                             "initial_reading_date": "2026-01-01", "intensity": 1e11}],
            }))
            (inputs / "materials.json").write_text(json.dumps({
                "materials": [{"name": "air", "mu_en_over_rho": {"1.173": .02921}}],
            }))
            route = {"radionuclide": "Co-60", "position_reference": "bottom_center", "cad_offset_degrees": 0,
                     "positions": [{"name": name, "center_mm": [0, 0, 0], "yaw_degrees": yaw}
                                   for name, yaw in [("I7", 0), ("I8", 180)]]}
            mesh = air_calibration_mesh((20, 10, 10), 1, 2)
            write_air_calibration(root, "calibration-test", route, mesh, date(2026, 1, 1))
            result = json.loads((root / "results/route_rates.json").read_text())
            self.assertEqual(result["analysis_method"], "deterministic_air")

            def read_map(path):
                reader = vtkXMLImageDataReader()
                reader.SetFileName(str(path))
                reader.Update()
                cells = reader.GetOutput().GetCellData()
                self.assertIsNone(cells.GetArray("relative_error"))
                return vtk_to_numpy(cells.GetScalars()).copy()

            first, turned = [
                read_map((root / position["summary"]).with_name("dose_distribution.vti"))
                for position in result["positions"]
            ]
            self.assertGreater(first[1], first[0])
            np.testing.assert_allclose(turned, first[::-1])
            combined_path, = (root / "results/dose_scenarios").glob("*.vti")
            np.testing.assert_allclose(read_map(combined_path), first + turned)

    def test_accumulated_limit_requires_two_percent_maximum_and_complete_coverage(self):
        self.assertEqual(RouteConfiguration().accumulated_error_target, .02)
        self.assertEqual(RouteConfiguration().particles, 100_000_000)
        summary = {"active_cells": 100, "cells_with_error_estimate": 100,
                   "relative_error_maximum": .02, "relative_error_p98": .005}
        self.assertTrue(accumulated_precision(summary)["meets_accumulated_target"])
        self.assertTrue(accumulated_precision(summary, None)["meets_accumulated_target"])
        self.assertFalse(accumulated_precision(summary | {"relative_error_maximum": .021}, None)["meets_accumulated_target"])
        for changes in ({"relative_error_maximum": .02001}, {"cells_with_error_estimate": 99},
                        {"relative_error_maximum": float("nan")}, {"relative_error_maximum": None}):
            self.assertFalse(accumulated_precision(summary | changes)["meets_accumulated_target"])
        self.assertFalse(accumulated_precision(summary, .01)["meets_accumulated_target"])

    def test_partial_study_cannot_claim_precision_for_missing_positions(self):
        points = [{"precision": {"meets_initial": True, "meets_recommended": True}}]
        result = study_precision(points, False)
        self.assertFalse(result["meets_recommended"])
        self.assertTrue(result["available_positions_meet_recommended"])
        self.assertTrue(study_precision(points, True)["meets_recommended"])

    def test_convergence_ignores_only_unreported_cad_fringes(self):
        import numpy as np
        source = {"water": {"material": "test", "bins": np.array([[0, 1], [1, 1], [2, 1]])}}
        selected = active_material_bins(source, [True, False, True])
        np.testing.assert_array_equal(selected["water"]["bins"], [[0, 1], [2, 1]])
        self.assertEqual(len(source["water"]["bins"]), 3)
        with self.assertRaises(ValueError):
            active_material_bins(source, [False, False, False])
    def test_quality_requires_every_active_voxel_and_strict_limits(self):
        data = {"active_cells": 10, "cells_with_error_estimate": 10}
        self.assertEqual(precision_status(data | {"relative_error_maximum": .049})["state"], "recommended")
        self.assertEqual(precision_status(data | {"relative_error_maximum": .05})["state"], "initial")
        self.assertEqual(precision_status(data | {"relative_error_maximum": .099})["state"], "initial")
        self.assertEqual(precision_status(data | {"relative_error_maximum": .10})["state"], "insufficient")
        self.assertEqual(precision_status(data | {"cells_with_error_estimate": 9, "relative_error_maximum": .01})["state"], "insufficient")
        for error in (None, float("nan"), float("inf")):
            self.assertEqual(precision_status(data | {"relative_error_maximum": error})["state"], "insufficient")
        self.assertEqual(precision_status({})["state"], "insufficient")

    def test_overrides_and_quick_budget_are_shared_without_changing_snapshot(self):
        source = {"monte_carlo": {"particles": 1_000_000, "dose_tally": "phits"}, "compute": {"cpu_cores": 8}}
        route = {
            "particles": 2_000_000, "cell_size_cm": 10, "cpu_cores": 4, "variance_reduction": "phits_windows",
            "quick_parameters": {"monte_carlo": {"particles": 60_000, "batches": 12}, "compute": {"cpu_cores": 2}},
        }
        result = study_simulation(source, route)
        self.assertEqual(result["monte_carlo"]["particles"], 60_000)
        self.assertEqual(result["monte_carlo"]["batches"], 12)
        self.assertEqual(result["monte_carlo"]["cell_size_cm"], 10)
        self.assertEqual(result["compute"]["cpu_cores"], 2)
        self.assertEqual(source["monte_carlo"]["particles"], 1_000_000)
        self.assertEqual(source["compute"]["cpu_cores"], 8)
        combined = study_simulation(source, {"variance_reduction": "source_box_magic", "cpu_cores": 4})
        self.assertEqual(combined["monte_carlo"]["weight_window_method"], "magic")
        automatic = study_simulation(source, {"adaptive_statistics": True})
        self.assertTrue(automatic["monte_carlo"]["adaptive_statistics"])

    def test_study_rejects_invalid_budgets_and_precision_targets(self):
        for data in ({"relative_error_target": .11}, {"relative_error_target": 0}, {"particles": 0},
                     {"cell_size_cm": float("nan")}, {"batches": 1}):
            with self.assertRaises(ValidationError):
                RouteStudyRequest(start=0, end=0, **data)

    def test_extended_budget_and_occupancy_override(self):
        study = RouteStudyRequest(start=0, end=55, time_limit_seconds=28800,
                                  extended_time=True, occupancy="isolated", cpu_cores=12)
        self.assertEqual(study.time_limit_seconds, 28800)
        self.assertEqual(study.occupancy, "isolated")
        with self.assertRaises(ValidationError):
            RouteStudyRequest(start=0, end=55, time_limit_seconds=28800, extended_time=True, quick=True)

    def test_route_maps_sum_matching_local_voxels_and_propagate_uncertainty(self):
        import numpy as np
        import vtk
        from vtk.util.numpy_support import vtk_to_numpy
        from sim_gamma.domain.route_map import write_route_dose_map
        from sim_gamma.simulators.openmc.structured_postprocess import RegularMeshSpec, write_compressed_vti

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (4, 2, 2))
            positions = []
            for index, (rates, errors) in enumerate([([2, 6], [.1, .1]), ([8, 4], [.2, np.nan])]):
                folder = root / str(index)
                folder.mkdir()
                write_compressed_vti(rates, mesh, folder / "dose_distribution.vti", relative_error=errors)
                positions.append({"name": f"I{index + 1}", "yaw_degrees": 180 * index,
                                  "summary": f"{index}/result.json"})
            for sum_rates, times, expected, error in [
                (True, [60, 60], [10, 10], np.hypot(.2, 1.6) / 10),
                (False, [30, 60], [9, 7], np.hypot(.1, 1.6) / 9),
            ]:
                destination = root / "combined.vti"
                result = write_route_dose_map(root, positions, times, destination, sum_rates=sum_rates)
                reader = vtk.vtkXMLImageDataReader()
                reader.SetFileName(str(destination))
                reader.Update()
                data = reader.GetOutput().GetCellData()
                np.testing.assert_allclose(vtk_to_numpy(data.GetScalars()), expected)
                np.testing.assert_allclose(vtk_to_numpy(data.GetArray("relative_error")), [error, np.nan])
                self.assertEqual(result["precision"]["cells_with_error_estimate"], 1)
                self.assertFalse(result["precision"]["meets_initial"])
                self.assertEqual(result["alignment"], {
                    "mesh_frame": "load-local",
                    "reference_position": "I1",
                    "reference_yaw_degrees": 0,
                    "turns": [{"from_position": "I1", "to_position": "I2", "yaw_degrees": -180.0}],
                })
            zero_time = write_route_dose_map(root, positions, [30, 0], root / "first.vti")
            self.assertEqual(zero_time["precision"]["cells_with_error_estimate"], 2)

    def test_route_map_alignment_records_physical_turns_and_interval_reference(self):
        from sim_gamma.domain.route_map import route_map_alignment

        names = ("I7", "I8", "I14", "I15", "I21", "I22", "I28", "S1", "S7", "S8", "S14", "S15", "S21", "S22")
        yaws = (0, -180, -180, -180, -180, -360, -360, -540, -540, -720, -720, -720, -720, -900)
        positions = [{"name": name, "yaw_degrees": yaw} for name, yaw in zip(names, yaws, strict=True)]
        full = route_map_alignment(positions)
        self.assertEqual([(turn["from_position"], turn["to_position"], turn["yaw_degrees"]) for turn in full["turns"]], [
            ("I7", "I8", -180), ("I21", "I22", -180), ("I28", "S1", -180),
            ("S7", "S8", -180), ("S21", "S22", -180),
        ])
        crossing = route_map_alignment(positions[2:4])
        self.assertEqual((crossing["reference_position"], crossing["reference_yaw_degrees"], crossing["turns"]),
                         ("I14", -180, []))

    def test_accumulated_error_percentiles_upgrade_saved_analysis(self):
        import json
        import numpy as np
        from sim_gamma.application.services.route_analysis import accumulated_analysis, map_error_statistics
        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec, summarize_relative_error, write_compressed_vti,
        )

        errors = np.array([.01, .02, .04, .8, np.nan])
        active = np.array([True, True, True, False, True])
        expected = np.percentile(errors[:3], [90, 95, 98])
        summary = summarize_relative_error(errors, active)
        self.assertEqual(summary["cells_with_error_estimate"], 3)
        for percentile, value in zip((90, 95, 98), expected, strict=True):
            self.assertAlmostEqual(summary[f"relative_error_p{percentile}"], value)
            self.assertIsNone(summarize_relative_error(np.array([np.nan]))[f"relative_error_p{percentile}"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "combined.vti"
            mesh = RegularMeshSpec((5, 1, 1), (0, 0, 0), (10, 2, 2))
            write_compressed_vti(np.ones(5), mesh, path, inside_mask=active, relative_error=errors)
            output = path.with_suffix("")
            output.mkdir()
            metadata = output / "analysis.json"
            metadata.write_text(json.dumps({"average_dose": 1, "relative_error_p90": float(expected[0])}))
            result, _ = accumulated_analysis(path)
            stats = map_error_statistics(path, path.stat().st_mtime_ns)
            for percentile, value in zip((95, 98), expected[1:], strict=True):
                self.assertAlmostEqual(stats[f"relative_error_p{percentile}"], value)
                self.assertAlmostEqual(result["summary"][f"relative_error_p{percentile}"], value)
                self.assertAlmostEqual(json.loads(metadata.read_text())[f"relative_error_p{percentile}"], value)
            self.assertEqual(result["summary"]["average_dose"], 1)

    def test_merged_cad_preserves_rigid_placements_and_material_tags(self):
        import cadquery as cq
        import numpy as np
        from sim_gamma.application.services.route_cad import write_occupied_step
        from sim_gamma.application.services.route_examples import example_step
        from sim_gamma.application.services.cad_preview import CadPreviewService

        route = {
            "occupancy": "occupied", "position_reference": "bottom_center",
            "positions": [
                {"name": "I1", "center_mm": [0, 0, 0], "yaw_degrees": 0},
                {"name": "I2", "center_mm": [300, 0, 0], "yaw_degrees": 90},
                {"name": "S1", "center_mm": [0, 0, 200], "yaw_degrees": 180},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = example_step(root, "airload", (40, 50, 100))
            destination = root / "occupied.step"
            count = write_occupied_step(source, destination, route,
                                        np.array([[-2, -2.5, 0], [2, 2.5, 10]]), "airload")
            self.assertEqual(count, 3)
            bounds = cq.importers.importStep(str(destination)).val().BoundingBox()
            np.testing.assert_allclose([bounds.xmin, bounds.xmax, bounds.ymin, bounds.ymax,
                                        bounds.zmin, bounds.zmax], [-20, 325, -25, 25, 0, 300], atol=1e-5)
            parts = CadPreviewService(root / "preview").derive_component_meshes(destination)["parts"]
            self.assertEqual([part["material"] for part in parts], ["airload"] * 3)


if __name__ == "__main__":
    unittest.main()
