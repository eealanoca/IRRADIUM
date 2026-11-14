import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sim_gamma.application.services.monte_carlo_jobs import MonteCarloJobs
from sim_gamma.domain.study_recovery import input_fingerprints, position_output_name, recover_study
from sim_gamma.workers.monte_carlo_cli import run_route, write_json
from sim_gamma.workers.study_guard import run_guarded, study_slot


class StudyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'input').mkdir()
        (self.root / 'monte_carlo/work').mkdir(parents=True)
        (self.root / 'logs').mkdir()
        self.positions = [{'name': f'I{i + 1}', 'center_mm': [i * 600, 0, 0], 'yaw_degrees': 180 if i else 0} for i in range(3)]
        self.route = {'positions': self.positions, 'particles': 100, 'batches': 10, 'cell_size_cm': 2,
                      'dose_tally': 'flux', 'variance_reduction': 'analog', 'occupancy': 'isolated'}
        write_json(self.root / 'input/route.json', self.route)
        for name in ('sources.json', 'materials.json', 'simulation_parameters.json'):
            (self.root / 'input' / name).write_text('{}')
        (self.root / 'input/load.step').write_text('STEP fixture')
        self.request = {'case_id': 'test', 'case_root': str(self.root), 'project_root': str(self.root / 'app'),
                        'irradiator_type': 'IV', 'run_id': 'same-study', 'route_study': {
                            'start': 0, 'end': 2, 'irradiation_date': '2026-11-08', 'particles': 100,
                            'cell_size_cm': 2, 'time_limit_seconds': 900, 'memory_limit_mb': 4096}}
        self.request_path = self.root / 'monte_carlo/request.json'
        write_json(self.request_path, self.request)
        self.calls = []
        self.pause_after = 1

    def transport(self, path, *, route_context):
        index, _, _, _, route = route_context
        self.calls.append(index)
        request = json.loads(path.read_text())
        output = self.root / 'monte_carlo/positions/same-study' / position_output_name(index, route)
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / 'result.json', {'run_id': 'same-study', 'simulated_particles': route['particles'],
                   'average_dose': index + 1, 'minimum_dose': .5, 'maximum_dose': 3,
                   'active_cells': 1, 'cells_with_error_estimate': 1, 'relative_error_maximum': .01})
        (output / 'dose_distribution.vti').write_bytes(f'published map {index}'.encode())
        if len(self.calls) == self.pause_after:
            write_json(self.root / 'monte_carlo/pause.json', {'run_id': request['run_id']})

    def run_fixture(self):
        with patch('sim_gamma.workers.monte_carlo_cli.run_position', side_effect=self.transport), \
             patch.dict(os.environ, {'IRRADIUM_STUDY_CHILD': ''}):
            run_route(self.request_path, self.request, self.root / 'input/route.json')

    def paused_study(self):
        self.run_fixture()
        return json.loads((self.root / 'results/studies/same-study.json').read_text())

    def jobs(self):
        return MonteCarloJobs(SimpleNamespace(root=lambda _: self.root), self.root / 'app', True)

    def test_queued_status_keeps_original_study_parameters_after_guard_update(self):
        write_json(self.root / 'monte_carlo/status.json', {
            'case_id': 'test', 'run_id': 'same-study', 'state': 'queued', 'stage': 'waiting-for-resources',
        })
        with patch('sim_gamma.application.services.deterministic_jobs.DeterministicJobs._matching_processes', return_value=[123]):
            status = self.jobs().status('test')
        self.assertEqual(status['route_study'], self.request['route_study'])

    def test_pause_and_resume_keeps_run_id_maps_and_does_not_repeat_positions(self):
        saved = self.paused_study()
        self.assertEqual(self.calls, [0])
        self.assertEqual(saved['stop_reason'], 'paused')
        first_map = self.root / saved['positions'][0]['summary']
        original = first_map.read_bytes()
        self.request.update(resume=True, continuation=1, input_fingerprints=input_fingerprints(self.root))
        write_json(self.request_path, self.request)
        (self.root / 'monte_carlo/pause.json').unlink()
        self.pause_after = -1
        self.run_fixture()
        completed = json.loads((self.root / 'results/studies/same-study.json').read_text())
        self.assertEqual(self.calls, [0, 1, 2])
        self.assertEqual(first_map.read_bytes(), original)
        self.assertEqual(completed['run_id'], 'same-study')
        self.assertTrue(completed['complete'])
        self.assertNotIn('stop_reason', completed)
        self.assertEqual(completed['total_simulated_histories'], 300)
        self.assertEqual(completed['completed_calculations'], 3)
        self.assertIn('002-c1', completed['positions'][1]['summary'])

    def test_missing_map_changed_pose_or_inputs_cannot_be_silently_reused(self):
        saved = self.paused_study()
        self.request['input_fingerprints'] = input_fingerprints(self.root)
        (self.root / 'input/sources.json').write_text('{"changed": true}')
        with self.assertRaisesRegex(ValueError, 'entradas'):
            recover_study(self.root, self.request)
        self.request.pop('input_fingerprints')
        self.route['positions'][0]['yaw_degrees'] = 180
        write_json(self.root / 'input/route.json', self.route)
        with self.assertRaisesRegex(ValueError, 'giros'):
            recover_study(self.root, self.request)
        self.route['positions'][0]['yaw_degrees'] = 0
        write_json(self.root / 'input/route.json', self.route)
        (self.root / saved['positions'][0]['summary']).with_name('dose_distribution.vti').unlink()
        with self.assertRaisesRegex(ValueError, 'Falta'):
            recover_study(self.root, self.request)

    def test_service_continuation_preserves_request_budget_and_rejects_double_start(self):
        self.paused_study()
        jobs = self.jobs()
        launched = []
        with patch.object(jobs, '_launch_request', side_effect=lambda *args: launched.append(args)), \
             patch.object(jobs, '_systemd_user_available', return_value=False), \
             patch.object(jobs, '_systemd_unit_active', return_value=False), \
             patch('sim_gamma.application.services.deterministic_jobs.DeterministicJobs._matching_processes', side_effect=lambda *_: [(123, 123)] if launched else []), \
             patch('sim_gamma.application.services.monte_carlo_jobs.shutil.disk_usage', return_value=SimpleNamespace(free=1024**3)):
            resumed = jobs.resume('test', 'same-study')
            self.assertEqual(resumed['state'], 'queued')
            with self.assertRaises(ValueError):
                jobs.resume('test', 'same-study')
        request = json.loads(self.request_path.read_text())
        self.assertEqual(len(launched), 1)
        self.assertEqual(request['route_study'], self.request['route_study'])
        self.assertEqual(request['run_id'], self.request['run_id'])
        self.assertTrue(request['resume'])
        self.assertEqual(request['continuation'], 1)

    def test_resume_rejects_old_button_insufficient_space_and_restores_failed_launch(self):
        self.paused_study()
        jobs = self.jobs()
        with patch.object(jobs, '_systemd_user_available', return_value=False), \
             patch.object(jobs, '_systemd_unit_active', return_value=False), \
             patch('sim_gamma.application.services.deterministic_jobs.DeterministicJobs._matching_processes', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'cambió'):
                jobs.resume('test', 'other-study')
            with patch('sim_gamma.application.services.monte_carlo_jobs.shutil.disk_usage', return_value=SimpleNamespace(free=100)):
                with self.assertRaisesRegex(ValueError, '512'):
                    jobs.resume('test', 'same-study')
            old_request = self.request_path.read_bytes()
            old_status = json.loads((self.root / 'monte_carlo/status.json').read_text())
            with patch.object(jobs, '_launch_request', side_effect=RuntimeError('spawn failed')), \
                 patch('sim_gamma.application.services.monte_carlo_jobs.shutil.disk_usage', return_value=SimpleNamespace(free=1024**3)):
                with self.assertRaisesRegex(RuntimeError, 'spawn failed'):
                    jobs.resume('test', 'same-study')
            self.assertEqual(json.loads(self.request_path.read_text()), json.loads(old_request))
            self.assertEqual(jobs.status('test')['state'], old_status['state'])

    def test_pause_request_is_idempotent_and_does_not_kill_the_worker(self):
        write_json(self.root / 'monte_carlo/status.json', {'state': 'running', 'case_id': 'test', 'run_id': 'same-study'})
        jobs = self.jobs()
        with patch('sim_gamma.application.services.deterministic_jobs.DeterministicJobs._matching_processes', return_value=[(123, 123)]), \
             patch('sim_gamma.application.services.monte_carlo_jobs.os.killpg') as kill:
            self.assertEqual(jobs.pause('test')['state'], 'pausing')
            self.assertEqual(jobs.pause('test')['state'], 'pausing')
            kill.assert_not_called()

    def test_queued_pause_does_not_acquire_the_busy_slot(self):
        write_json(self.root / 'monte_carlo/pause.json', {'run_id': 'same-study'})
        with study_slot(self.root, self.root / 'monte_carlo', self.request) as acquired:
            self.assertIs(acquired, False)

    def test_pausing_a_queued_continuation_updates_its_archive_and_coverage(self):
        self.paused_study()
        archive = self.root / 'results/studies/same-study.json'
        result = json.loads(archive.read_text())
        result['stop_reason'] = 'disk-limited'
        write_json(archive, result)
        with patch.dict(os.environ, {'IRRADIUM_ROOT': str(self.root)}), \
             patch('sim_gamma.workers.study_guard.__file__', str(self.root / 'app/src/sim_gamma/workers/study_guard.py')):
            self.assertEqual(run_guarded(self.request_path), 0)
        self.assertEqual(json.loads(archive.read_text())['stop_reason'], 'paused')
        final = json.loads((self.root / 'monte_carlo/status.json').read_text())
        self.assertEqual(final['state'], 'paused')
        self.assertEqual(final['progress'], 33)

    def test_guard_keeps_paused_terminal_state_and_cumulative_active_time(self):
        self.request['previous_elapsed_seconds'] = 12
        write_json(self.request_path, self.request)
        status = self.root / 'monte_carlo/status.json'
        code = f'import pathlib; pathlib.Path({str(status)!r}).write_text(\'{{"state":"running","final_state":"paused","progress":33,"run_id":"same-study"}}\')'
        spawn = subprocess.Popen
        with patch.dict(os.environ, {'IRRADIUM_ROOT': ''}), \
             patch('sim_gamma.workers.study_guard.subprocess.Popen', side_effect=lambda *args, **kwargs: spawn([sys.executable, '-c', code], **kwargs)):
            self.assertEqual(run_guarded(self.request_path), 0)
        final = json.loads(status.read_text())
        resources = json.loads((self.root / 'monte_carlo/resources.json').read_text())
        self.assertEqual(final['state'], 'paused')
        self.assertEqual(final['progress'], 33)
        self.assertGreaterEqual(resources['elapsed_seconds'], 12)
        self.assertLess(resources['elapsed_seconds'], 15)
        self.assertEqual(resources['time_limit_seconds'], 900)


if __name__ == '__main__':
    unittest.main()
