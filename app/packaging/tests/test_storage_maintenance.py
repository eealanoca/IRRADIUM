import gzip
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('storage_maintenance',ROOT/'scripts/maintain_storage.py')
storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)

class StorageMaintenanceTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def file(self,name,data=b'fixture'):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p

    def test_only_known_generated_files_are_candidates(self):
        old=self.file('.local/tmp/irradium-results-ui-old/test.txt').parent
        keep=self.file('.local/tmp/my-load/fixture.step')
        generated=self.file('.local/validation/phits-angular-pilots/I1/load.bdf')
        referenced=self.file('.local/validation/phits-angular-pilots/I2/load.bdf')
        source=self.file('.local/validation/phits-angular-pilots/I1/load.step')
        report=self.file('.local/validation/phits-angular-pilots/report.json')
        candidates=storage.cleanup_candidates(self.root,[str(referenced)])
        self.assertIn(old,candidates);self.assertIn(generated,candidates)
        for p in (keep,referenced,source,report):self.assertNotIn(p,candidates)

    def test_identical_cache_files_share_storage_within_category_only(self):
        content=b'STL'*30000;digest='a'*20+'-components'
        a=self.file('workspace/irradiators/IV/Temporales/step_previews/'+digest+'/water.stl',content)
        b=self.file('workspace/irradiators/IV/cache/monte-carlo-step-components/'+digest+'/water.stl',content)
        other=self.file('workspace/irradiators/II/Temporales/step_previews/'+digest+'/water.stl',content)
        groups=storage.preview_groups(self.root);self.assertEqual(len(groups),1)
        self.assertEqual(len(storage.deduplicate(groups,self.root)),1)
        self.assertEqual(a.stat().st_ino,b.stat().st_ino);self.assertNotEqual(a.stat().st_ino,other.stat().st_ino)
        self.assertEqual(a.read_bytes(),content);self.assertEqual(storage.preview_groups(self.root),[])

    def test_log_archive_is_complete_before_original_is_cleared(self):
        data=b'complete historical diagnostics\n'*1000;p=self.file('workspace/Registros/api.log',data)
        result=storage.compress_log(p)
        self.assertEqual(gzip.decompress(Path(result['archive']).read_bytes()),data)
        self.assertEqual(p.read_bytes(),b'')

    def test_symlinked_cache_file_is_not_modified(self):
        outside=self.file('original.stl',b'STL'*30000)
        target=self.root/('workspace/irradiators/IV/Temporales/step_previews/'+'a'*20+'-components/water.stl')
        target.parent.mkdir(parents=True);target.symlink_to(outside)
        self.assertEqual(storage.preview_groups(self.root),[])
        self.assertTrue(target.is_symlink());self.assertEqual(outside.read_bytes(),b'STL'*30000)
