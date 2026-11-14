import json
from pathlib import Path
from unittest.mock import patch
from .test_irradiators import IrradiatorApiTests
from apps.api.sim_gamma_api.route_models import RouteConfiguration


class LearningTests(IrradiatorApiTests):
    def test_language_is_saved_separately_and_validated(self):
        self.assertEqual(self.client.get('/api/language').json(),{'language':'en'})
        self.assertEqual(self.client.put('/api/language',json={'language':'es'}).status_code,200)
        self.client.put('/api/preferences',json={'theme':'light'})
        self.assertEqual(self.client.get('/api/language').json(),{'language':'es'})
        self.assertEqual(self.client.put('/api/language',json={'language':'fr'}).status_code,422)
    def test_learning_state_persists_and_is_independent_of_theme(self):
        self.assertFalse(self.client.get('/api/onboarding').json()['dismissed'])
        self.assertEqual(self.client.put('/api/onboarding',json={'dismissed':True,'version':1}).status_code,200)
        self.client.put('/api/preferences',json={'theme':'dark'})
        self.assertTrue(self.client.get('/api/onboarding').json()['dismissed'])
        self.assertTrue((self.workspace/'Configuracion/aprendizaje.json').is_file())

    def test_example_has_all_positions_sources_and_geometry_without_changing_configuration(self):
        before={p.name:p.read_bytes() for p in self.config.iterdir() if p.is_file()}
        response=self.client.post('/api/learning/example',json={})
        self.assertEqual(response.status_code,201,response.text)
        case=response.json();root=self.workspace/'irradiators/IV/Casos'/case['case_id']
        route=RouteConfiguration.model_validate_json((root/'input/route.json').read_text())
        self.assertEqual(len(route.positions),68)
        self.assertEqual(len({p.name for p in route.positions}),68)
        self.assertTrue((root/'input/load.step').is_file())
        self.assertEqual(len(json.loads((root/'input/sources.json').read_text())['sources']),12)
        self.assertTrue((root/'input/example_provenance.json').is_file())
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.config.iterdir() if p.is_file()})
        self.assertEqual(case['step_material_tags'],['water'])
        self.assertEqual(self.client.post('/irradiators/I/api/learning/example',json={}).status_code,409)

    def test_complex_example_contains_all_five_materials_and_independent_snapshots(self):
        before={p.name:p.read_bytes() for p in self.config.iterdir() if p.is_file()}
        response=self.client.post('/api/learning/example',json={'variant':'medical_kit'})
        self.assertEqual(response.status_code,201,response.text)
        case=response.json();root=self.workspace/'irradiators/IV/Casos'/case['case_id']
        self.assertEqual(set(case['step_material_tags']),{'water','pp','pc','silicone','steel'})
        self.assertGreater((root/'input/load.step').stat().st_size,500000)
        materials=json.loads((root/'input/materials.json').read_text())['materials']
        self.assertTrue({'water','pp','pc','silicone','steel'}.issubset({m['name'] for m in materials}))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.config.iterdir() if p.is_file()})
        self.assertEqual(len(json.loads((root/'input/route.json').read_text())['positions']),68)
        self.assertEqual(self.client.post('/api/learning/example',json={'variant':'private'}).status_code,422)
