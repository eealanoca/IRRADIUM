import asyncio
import importlib
import json
from pathlib import Path
import signal
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from apps.api.sim_gamma_api.desktop_session import DesktopWindows
from apps.api.sim_gamma_api.services import stop_all_jobs
from sim_gamma.application.services.monte_carlo_jobs import MonteCarloJobs


class DesktopSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_window_closes_services_and_multiple_windows_keep_them_open(self):
        stop = Mock()
        windows = DesktopWindows(True, stop, grace_seconds=.02)
        windows.connected()
        windows.connected()
        windows.disconnected()
        await asyncio.sleep(.04)
        stop.assert_not_called()
        windows.disconnected()
        await asyncio.sleep(.04)
        stop.assert_called_once_with()

    async def test_reload_cancels_pending_shutdown(self):
        stop = Mock()
        windows = DesktopWindows(True, stop, grace_seconds=.02)
        windows.connected()
        windows.disconnected()
        windows.connected()
        await asyncio.sleep(.04)
        stop.assert_not_called()
        windows.disconnected()
        await asyncio.sleep(.04)
        stop.assert_called_once_with()

    async def test_server_shutdown_and_headless_use_do_not_schedule_another_stop(self):
        stop = Mock()
        windows = DesktopWindows(True, stop, grace_seconds=.02)
        windows.connected()
        windows.disconnected()
        windows.cancel_pending()
        await asyncio.sleep(.04)
        stop.assert_not_called()
        windows = DesktopWindows(False, stop, grace_seconds=.02)
        windows.connected()
        windows.disconnected()
        await asyncio.sleep(.04)
        stop.assert_not_called()

    async def test_route_workers_survive_ui_shutdown_and_can_be_cancelled_explicitly(self):
        services = SimpleNamespace(
            case_store=SimpleNamespace(irradiator_type='IV'),
            deterministic_jobs=Mock(), monte_carlo_jobs=Mock(),
        )
        with patch('apps.api.sim_gamma_api.services._instances', {'IV': services}):
            stop_all_jobs(preserve_route_studies=True)
            services.monte_carlo_jobs.cancel_all.assert_not_called()
            services.deterministic_jobs.cancel_all.assert_not_called()
            stop_all_jobs()
            services.monte_carlo_jobs.cancel_all.assert_called_once()


class DetachedStudyTests(unittest.TestCase):
    def test_browser_socket_requires_the_local_origin_and_tracks_connections(self):
        api = importlib.import_module('apps.api.sim_gamma_api.main')
        windows = DesktopWindows(True, Mock(), grace_seconds=15)
        with patch.object(api, 'desktop_windows', windows), TestClient(api.app) as client:
            self.assertTrue(client.get('/api/health').json()['desktop_session'])
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('/api/desktop/session', headers={'origin': 'https://example.com'}):
                    pass
            with client.websocket_connect('/api/desktop/session', headers={'origin': 'http://127.0.0.1:3001'}):
                self.assertEqual(windows.count, 1)
            windows.on_close.assert_not_called()
        windows.on_close.assert_not_called()

    def test_detached_worker_keeps_progress_after_api_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / 'cases/study'
            for folder in (case / 'monte_carlo/work', case / 'logs', root / 'src'):
                folder.mkdir(parents=True)
            module = root / 'src/desktop_test_worker.py'
            module.write_text(
                'import json, pathlib, sys, time\n'
                'root = pathlib.Path(sys.argv[1]).parent\n'
                'while True:\n'
                '    (root / "heartbeat").write_text(str(time.monotonic()))\n'
                '    time.sleep(.03)\n'
            )
            request = {'run_id': 'fixture', 'case_id': 'study', 'case_root': str(case)}
            (case / 'monte_carlo/request.json').write_text(json.dumps(request))
            store = SimpleNamespace(root=lambda _: case, irradiator_type='IV', runs_root=case.parent)
            jobs = MonteCarloJobs(store, root, True)
            jobs.WORKER_MODULE = 'desktop_test_worker'
            jobs._launch_request('study', request)
            process = next(iter(jobs._children.values()))
            try:
                heartbeat = case / 'monte_carlo/heartbeat'
                deadline = time.monotonic() + 3
                while not heartbeat.is_file() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(heartbeat.is_file())
                before = float(heartbeat.read_text())
                services = SimpleNamespace(case_store=store, deterministic_jobs=Mock(), monte_carlo_jobs=jobs)
                with patch('apps.api.sim_gamma_api.services._instances', {'IV': services}):
                    stop_all_jobs(preserve_route_studies=True)
                time.sleep(.12)
                self.assertIsNone(process.poll())
                self.assertGreater(float(heartbeat.read_text()), before)
            finally:
                import os
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
