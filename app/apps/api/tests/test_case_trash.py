import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from apps.api.sim_gamma_api.main import app
from sim_gamma.application.services.case_store import CaseStore


class CaseTrashTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        config = self.base / 'config'
        config.mkdir()
        for name in ('sources.json', 'materials.json', 'work_sections.json'):
            (config / name).write_text('{}')
        step = self.base / 'load.step'
        step.write_bytes(b'original-step-bytes')
        self.store = CaseStore(self.base / 'output', config, step)
        self.case = self.store.create('Caso recuperable')
        self.case_id = self.case['case_id']
        self.client = TestClient(app)

    def tearDown(self):
        self.temporary.cleanup()

    def test_round_trip_preserves_every_file_and_other_cases(self):
        other = self.store.create('Conservar')
        root = self.store.root(self.case_id)
        (root / 'results' / 'map.npz').write_bytes(b'untouched-result')
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.store.trash(self.case_id)
        self.assertEqual([c['case_id'] for c in self.store.list()], [other['case_id']])
        self.assertEqual([c['case_id'] for c in self.store.list_trash()], [self.case_id])
        with self.assertRaises(FileNotFoundError):
            self.store.get(self.case_id)
        self.store.restore(self.case_id)
        self.assertEqual(list(self.store.list_trash()), [])
        self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})
        self.assertTrue(self.store.root(other['case_id']).is_dir())

    def test_restore_rejects_traversal_collision_and_other_category(self):
        self.store.trash(self.case_id)
        with self.assertRaises(ValueError):
            self.store.restore('../outside')
        destination = self.store.runs_root / self.case_id
        destination.mkdir()
        with self.assertRaises(ValueError):
            self.store.restore(self.case_id)
        destination.rmdir()
        manifest = self.store.trash_root / self.case_id / 'manifest.json'
        data = json.loads(manifest.read_text())
        data['irradiator_type'] = 'II'
        manifest.write_text(json.dumps(data))
        self.assertEqual(list(self.store.list_trash()), [])
        with self.assertRaises(FileNotFoundError):
            self.store.restore(self.case_id)

    def test_api_blocks_active_calculation_and_allows_restore_after_stop(self):
        deterministic = Mock(ACTIVE_STATES={'running'}, status=Mock(return_value={'state': 'completed'}))
        transport = Mock(ACTIVE_STATES={'running', 'pausing'}, status=Mock(return_value={'state': 'running'}))
        with patch('apps.api.sim_gamma_api.main.case_store', self.store), patch('apps.api.sim_gamma_api.main.deterministic_jobs', deterministic), patch('apps.api.sim_gamma_api.main.monte_carlo_jobs', transport):
            path = '/irradiators/IV/api/cases/' + self.case_id
            self.assertEqual(self.client.post(path + '/trash').status_code, 409)
            self.assertTrue(self.store.root(self.case_id).is_dir())
            transport.status.return_value = {'state': 'cancelled'}
            self.assertEqual(self.client.post(path + '/trash').status_code, 200)
            listed = self.client.get('/irradiators/IV/api/cases-trash').json()
            self.assertEqual(listed['cases'][0]['case_id'], self.case_id)
            response = self.client.post('/irradiators/IV/api/cases-trash/' + self.case_id + '/restore')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['case_id'], self.case_id)
