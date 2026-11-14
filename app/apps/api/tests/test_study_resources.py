import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from apps.api.sim_gamma_api.route_models import RouteStudyRequest
from sim_gamma.application.services.route_resources import estimate_study
from sim_gamma.application.services.route_analysis import position_analysis
from sim_gamma.workers.study_guard import run_guarded, study_slot
from sim_gamma.workers.transport_budget import configure_checkpoints, run_checkpointed_transport, valid_checkpoints


class StudyResourceTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {"IRRADIUM_ROOT": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_heavy_studies_wait_for_the_application_slot(self):
        import fcntl
        from threading import Event, Thread
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "monte_carlo"
            run_root.mkdir()
            lock_path = root / ".local" / "state" / "route-study.lock"
            lock_path.parent.mkdir(parents=True)
            acquired = Event()
            def worker():
                with study_slot(root, run_root, {"case_id": "queued", "run_id": "test"}):
                    acquired.set()
            with lock_path.open("a") as current:
                fcntl.flock(current, fcntl.LOCK_EX)
                thread = Thread(target=worker)
                thread.start()
                try:
                    self.assertFalse(acquired.wait(.1))
                    self.assertEqual(json.loads((run_root / "status.json").read_text())["state"], "queued")
                finally:
                    fcntl.flock(current, fcntl.LOCK_UN)
                self.assertTrue(acquired.wait(2))
                thread.join(2)

    def test_large_transport_keeps_small_batches_and_a_final_checkpoint(self):
        settings = SimpleNamespace(particles=4_000_000, batches=25, trigger_active=False)
        configure_checkpoints(settings)
        self.assertEqual(settings.particles, 200_000)
        self.assertEqual(settings.batches, 500)
        self.assertIn(500, settings.statepoint["batches"])
        self.assertIn(2, settings.statepoint["batches"])
        self.assertEqual(settings.particles * settings.batches, 100_000_000)

    def test_automatic_mode_can_check_precision_before_spending_the_budget(self):
        settings = SimpleNamespace(particles=320_000, batches=25, trigger_max_batches=25, trigger_active=True)
        configure_checkpoints(settings)
        self.assertEqual(settings.particles, 100_000)
        self.assertEqual(settings.batches, 25)
        self.assertEqual(settings.trigger_max_batches, 80)
        self.assertIn(25, settings.statepoint["batches"])

    def test_soft_deadline_recovers_complete_checkpoint_and_ignores_unfinished_file(self):
        import h5py
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            complete = root / "statepoint.2.h5"
            with h5py.File(complete, "w") as file:
                file["n_realizations"] = 2
            (root / "statepoint.3.h5").write_bytes(b"unfinished")
            spawn = subprocess.Popen
            with patch("sim_gamma.workers.transport_budget.subprocess.Popen",
                       side_effect=lambda *args, **kwargs: spawn([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)):
                limited, selected = run_checkpointed_transport("unused", root, 1, False, time.monotonic() + 29)
            self.assertTrue(limited)
            self.assertEqual(selected, complete)
            self.assertEqual(valid_checkpoints(root), [complete])

    def test_hard_budgets_and_new_variance_methods_are_validated(self):
        for overrides in ({"time_limit_seconds": 901}, {"time_limit_seconds": 0},
                          {"memory_limit_mb": 511}, {"memory_limit_mb": 8193}):
            with self.assertRaises(ValidationError):
                RouteStudyRequest(start=0, end=0, **overrides)
        for method in ("source_box", "source_box_windows", "source_box_magic", "source_scene"):
            request = RouteStudyRequest(start=0, end=0, variance_reduction=method)
            self.assertEqual(request.time_limit_seconds, 900)

    def test_grid_estimate_allows_two_and_one_cm_but_rejects_excessive_refinement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = root / "part.stl"
            mesh.write_bytes(bytes(80) + struct.pack("<I", 1) + struct.pack(
                "<12fH", 0, 0, 1, 0, 0, 0, 400, 500, 0, 0, 500, 1000, 0))
            preview = SimpleNamespace(
                derive_component_meshes=lambda _: {"digest": "test", "parts": [{"material": "water", "file": mesh.name}]},
                component_mesh_path=lambda *_: mesh,
            )
            with patch("sim_gamma.application.services.route_resources.available_memory_mb", return_value=8192):
                coarse = estimate_study(root, preview, 2, 4096)
                fine = estimate_study(root, preview, 1, 4096)
                excessive = estimate_study(root, preview, .1, 4096)
                with self.assertRaises(ValueError):
                    estimate_study(root, preview, 1e-300, 4096)
            self.assertEqual(coarse["dimensions"], [20, 25, 50])
            self.assertEqual(coarse["cells"], 25_000)
            self.assertTrue(coarse["feasible"])
            self.assertEqual(fine["cells"], 200_000)
            self.assertTrue(fine["feasible"])
            self.assertFalse(excessive["feasible"])

    def test_occupied_scene_accounts_for_all_neighbors_without_multiplying_tally_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = root / "complex.stl"
            triangle = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 500, 400, 0, 0, 400, 1000, 0)
            mesh.write_bytes(bytes(80) + struct.pack("<I", 100000) + triangle * 100000)
            preview = SimpleNamespace(
                derive_component_meshes=lambda _: {"digest": "test", "parts": [{"material": "water", "file": mesh.name}]},
                component_mesh_path=lambda *_: mesh,
            )
            with patch("sim_gamma.application.services.route_resources.available_memory_mb", return_value=8192):
                isolated = estimate_study(root, preview, 1, 2050)
                occupied = estimate_study(root, preview, 1, 2050, geometry_instances=56)
            self.assertEqual(occupied["cells"], isolated["cells"])
            self.assertEqual(occupied["geometry_instances"], 56)
            self.assertGreater(occupied["estimated_geometry_ram_mb"], 50 * isolated["estimated_geometry_ram_mb"])
            self.assertTrue(isolated["feasible"])
            self.assertFalse(occupied["feasible"])

    def test_time_guard_stops_worker_and_preserves_completed_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "results" / "studies" / "bounded.json"
            archive.parent.mkdir(parents=True)
            checkpoint = {"run_id": "bounded", "positions": [{"name": "I1"}], "complete": False,
                          "requested_position_count": 56}
            archive.write_text(json.dumps(checkpoint))
            request = root / "request.json"
            request.write_text(json.dumps({"case_root": str(root), "case_id": "test", "run_id": "bounded",
                                           "route_study": {"time_limit_seconds": 1, "memory_limit_mb": 512}}))
            spawn = subprocess.Popen
            processes = []

            def sleeping_worker(*args, **kwargs):
                process = spawn([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
                processes.append(process)
                return process

            started = time.monotonic()
            with patch("sim_gamma.workers.study_guard.subprocess.Popen", side_effect=sleeping_worker):
                self.assertEqual(run_guarded(request), 0)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIsNotNone(processes[0].poll())
            status = json.loads((root / "monte_carlo" / "status.json").read_text())
            result = json.loads(archive.read_text())
            self.assertEqual(status["state"], "time-limited")
            self.assertEqual(result["positions"], checkpoint["positions"])
            self.assertFalse(result["complete"])
            self.assertEqual(result["stop_reason"], "time-limited")
            self.assertEqual(result["resources"]["time_limit_seconds"], 1)

    def test_memory_guard_preserves_previous_success_when_no_new_position_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = root / "results" / "route_rates.json"
            latest.parent.mkdir(parents=True)
            latest.write_text('{"run_id":"previous"}')
            request = root / "request.json"
            request.write_text(json.dumps({"case_root": str(root), "case_id": "test", "run_id": "bounded",
                                           "route_study": {"memory_limit_mb": 512}}))
            spawn = subprocess.Popen
            with patch("sim_gamma.workers.study_guard.subprocess.Popen",
                       side_effect=lambda *args, **kwargs: spawn([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)), \
                 patch("sim_gamma.workers.study_guard.process_tree_rss_mb", return_value=513), \
                 patch("sim_gamma.workers.study_guard.available_memory_mb", return_value=8192):
                self.assertEqual(run_guarded(request), 0)
            self.assertEqual(json.loads(latest.read_text())["run_id"], "previous")
            status = json.loads((root / "monte_carlo" / "status.json").read_text())
            self.assertEqual(status["state"], "memory-limited")

    def test_disk_guard_preserves_previous_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = root / 'results/route_rates.json'
            latest.parent.mkdir()
            latest.write_text('{"run_id":"previous"}')
            request = root / 'request.json'
            request.write_text(json.dumps({'case_root': str(root), 'case_id': 'test', 'run_id': 'new',
                                           'route_study': {'time_limit_seconds': 60, 'memory_limit_mb': 4096}}))
            spawn = subprocess.Popen
            with patch('sim_gamma.workers.study_guard.subprocess.Popen', side_effect=lambda *args, **kwargs: spawn([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)), \
                 patch('sim_gamma.workers.study_guard.shutil.disk_usage', return_value=SimpleNamespace(free=128 * 1024**2)):
                self.assertEqual(run_guarded(request), 0)
            self.assertEqual(json.loads(latest.read_text())['run_id'], 'previous')
            status = json.loads((root / 'monte_carlo/status.json').read_text())
            self.assertEqual(status['state'], 'disk-limited')

    def test_position_catalogue_uses_output_mesh_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = {"average_dose": 1, "statistical_evaluation": {"relative_error_maximum": .01},
                       "visualization": {"relative_error_maximum": .2, "active_cells": 25_000}}
            (root / "result.json").write_text(json.dumps(summary))
            (root / "dose_distribution.vti").touch()
            response, path = position_analysis(root, {"summary": "result.json"})
            self.assertEqual(path, root)
            self.assertTrue(response["artifacts"]["vtk"])
            self.assertEqual(response["summary"]["statistical_evaluation"]["relative_error_maximum"], .2)


if __name__ == "__main__":
    unittest.main()
