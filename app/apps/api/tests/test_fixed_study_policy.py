import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from apps.api.sim_gamma_api import main
from apps.api.sim_gamma_api.route_models import RouteDoseQuery, RouteStudyRequest


class FixedStudyPolicyTests(unittest.TestCase):
    def test_refinement_is_opt_in_and_rejected_while_disabled(self):
        self.assertFalse(RouteStudyRequest(start=0, end=0).adaptive_statistics)
        with patch.object(main, "settings", SimpleNamespace(statistical_refinement_enabled=False)), \
             patch.object(main, "_route_case_root", return_value=Path("unused")), \
             patch.object(main, "case_store", Mock()):
            for options in ({"adaptive_statistics": True}, {"accumulated_error_target": .005}):
                with self.subTest(options=options), self.assertRaises(HTTPException) as caught:
                    main.start_route_study("unused", RouteStudyRequest(start=0, end=0, **options))
                self.assertEqual(caught.exception.status_code, 422)

    def test_fixed_budget_does_not_inherit_an_old_refinement_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").mkdir()
            (root / "input/route.json").write_text(json.dumps({
                "particles": 100_000_000_000, "positions": [
                    {"name": "I1", "center_mm": [0, 0, 0], "yaw_degrees": 0},
                ],
            }))
            jobs = Mock()
            with patch.object(main, "settings", SimpleNamespace(statistical_refinement_enabled=False)), \
                 patch.object(main, "_route_case_root", return_value=root), \
                 patch.object(main, "_route_configuration", return_value=SimpleNamespace(particles=100_000_000)), \
                 patch.object(main, "_validate_route_variance"), \
                 patch.object(main, "_route_minimum_date", return_value=None), \
                 patch.object(main, "_require_phits_if_selected"), \
                 patch.object(main, "case_store", Mock()), \
                 patch.object(main, "monte_carlo_jobs", jobs), \
                 patch("sim_gamma.application.services.route_resources.estimate_study", return_value={"feasible": True}):
                for requested, expected in ((None, 100_000_000), (80_000, 80_000)):
                    main.start_route_study("test", RouteStudyRequest(start=0, end=0, particles=requested))
                    submitted = jobs.submit.call_args.kwargs["route_study"]
                    self.assertEqual(submitted["particles"], expected)
                    self.assertFalse(submitted["adaptive_statistics"])
                    self.assertIsNone(submitted["accumulated_error_target"])

    def test_stopped_partial_maps_can_be_integrated_without_claiming_convergence(self):
        result = {
            "run_id": "stopped", "complete": False, "accumulated_error_target": .005,
            "positions": [{"mean_kgy_h": 2, "precision": {"meets_initial": True, "meets_recommended": True}}],
        }
        jobs = Mock(ACTIVE_STATES=("queued", "running", "cancelling"))
        query = RouteDoseQuery(start=0, end=0, mode="uniform", value=30, run_id="stopped")
        with patch.object(main, "get_route_results", return_value=result), patch.object(main, "monte_carlo_jobs", jobs):
            for state in ("disk-limited", "cancelled", "time-limited"):
                jobs.status.return_value = {"run_id": "stopped", "state": state}
                self.assertEqual(main.calculate_route_dose("test", query)["dose_kgy"], 1)
                self.assertFalse(result["complete"])
            jobs.status.return_value = {"run_id": "stopped", "state": "running"}
            with self.assertRaises(HTTPException) as caught:
                main.calculate_route_dose("test", query)
            self.assertEqual(caught.exception.status_code, 409)
            jobs.status.return_value = {"run_id": "other", "state": "running"}
            self.assertEqual(main.calculate_route_dose("test", query)["dose_kgy"], 1)
