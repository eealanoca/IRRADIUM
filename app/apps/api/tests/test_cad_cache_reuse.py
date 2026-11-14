import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sim_gamma.application.services.cad_preview import CadPreviewService

class CadCacheReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'IV'
        self.step=Path(self.temp.name)/'load.step';self.step.write_bytes(b'exact STEP input')
        self.digest=hashlib.sha256(self.step.read_bytes()).hexdigest()[:20]
        self.peer=self.root/'Temporales/step_previews'/(self.digest+'-components');self.peer.mkdir(parents=True)
        self.manifest={'version':3,'digest':self.digest,'parts':[{'name':'water_1','material':'water','file':'water_1.stl','cad_file':'water_1.step','transparent':False}]}
        (self.peer/'manifest.json').write_text(json.dumps(self.manifest))
        for name in ('water_1.stl','water_1.step'):(self.peer/name).write_bytes(name.encode())
        self.service=CadPreviewService(self.root/'cache/monte-carlo-step-components')
    def tearDown(self):self.temp.cleanup()

    def test_reuses_native_and_mesh_assets_without_loading_cad(self):
        actual=self.service.derive_component_meshes(self.step)
        self.assertEqual(actual,self.manifest)
        target=self.service.cache_root/self.peer.name
        for name in ('water_1.stl','water_1.step'):
            self.assertEqual((target/name).stat().st_ino,(self.peer/name).stat().st_ino)
        self.assertEqual(self.service.derive_component_meshes(self.step),self.manifest)

    def test_atomic_replacement_does_not_modify_the_other_cache(self):
        self.service._reuse_component_cache(self.digest)
        target=self.service.cache_root/self.peer.name/'water_1.stl'
        new=target.with_suffix('.new');new.write_bytes(b'regenerated');new.replace(target)
        self.assertEqual((self.peer/'water_1.stl').read_bytes(),b'water_1.stl')
        self.assertEqual(target.read_bytes(),b'regenerated')

    def test_incomplete_peer_is_not_reused(self):
        (self.peer/'water_1.step').unlink()
        self.assertIsNone(self.service._reuse_component_cache(self.digest))

    def test_other_category_and_digest_are_not_reused(self):
        other=CadPreviewService(self.root.parent/'II/cache/phits-step-components')
        self.assertIsNone(other._reuse_component_cache(self.digest))
        self.assertIsNone(self.service._reuse_component_cache('0'*20))

    def test_unsafe_manifest_filename_is_rejected(self):
        self.manifest['parts'][0]['file']='../water_1.stl'
        (self.peer/'manifest.json').write_text(json.dumps(self.manifest))
        self.assertIsNone(self.service._reuse_component_cache(self.digest))

    def test_corrupt_manifest_is_rejected(self):
        (self.peer/'manifest.json').write_text('{broken')
        self.assertIsNone(self.service._reuse_component_cache(self.digest))
