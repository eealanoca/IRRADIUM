import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
release=load('gamma_release',ROOT/'scripts/prepare_nuclear_data_release.py')
control=load('irradium_release_control',ROOT/'scripts/control.py')

class NuclearReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'original';self.source.mkdir();self.xml=self.source/'cross_sections.xml'
        tree=ET.Element('cross_sections')
        for kind,names in [('photon',['H','C','Ne']),('neutron',['H1','H2','H3','C0']),('thermal',['c_H_in_H2O']),('wmp',['H1'])]:
            for name in names:
                relative=Path(kind)/(name+'.h5');p=self.source/relative;p.parent.mkdir(exist_ok=True)
                p.write_bytes((kind+':'+name+':complete-file-bremsstrahlung-relaxation').encode())
                ET.SubElement(tree,'library',{'type':kind,'materials':name,'path':str(relative)})
        ET.ElementTree(tree).write(self.xml)
        self.before={str(p.relative_to(self.source)):p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
    def tearDown(self):self.temp.cleanup()

    def test_profile_keeps_all_photons_and_only_required_nuclides(self):
        selected,report=release.select(self.xml)
        self.assertEqual({(x['type'],x['materials']) for x in selected},
            {('photon','H'),('photon','C'),('photon','Ne'),('neutron','H1'),('neutron','H2'),('neutron','C0')})
        self.assertFalse(report['photon_only_compatible'])
        self.assertIn('Ne',report['elements_already_unavailable_in_source'])

    def test_bundle_is_portable_and_does_not_change_source(self):
        output=self.root/'release';release.bundle(self.xml,output)
        self.assertEqual(self.before,{str(p.relative_to(self.source)):p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        self.assertFalse((output/'neutron/H3.h5').exists())
        self.assertFalse((output/'thermal').exists());self.assertFalse((output/'wmp').exists())
        for node in ET.parse(output/'cross_sections.xml').getroot().findall('library'):
            self.assertFalse(Path(node.get('path')).is_absolute())
            self.assertEqual((output/node.get('path')).read_bytes(),(self.source/node.get('path')).read_bytes())
        moved=self.root/'moved';output.rename(moved)
        self.assertEqual(len(release.entries(moved/'cross_sections.xml')[1]),6)

    def test_existing_or_source_destinations_are_rejected(self):
        for destination in [self.source,self.source/'subset']:
            with self.assertRaises(ValueError):release.bundle(self.xml,destination)
        self.assertEqual(self.before,{str(p.relative_to(self.source)):p.read_bytes() for p in self.source.rglob('*') if p.is_file()})

    def test_photon_only_source_cannot_be_packaged_as_compatible(self):
        tree=ET.parse(self.xml)
        for node in list(tree.getroot()):
            if node.get('type')!='photon':tree.getroot().remove(node)
        tree.write(self.xml)
        with self.assertRaisesRegex(ValueError,'Solo fotones no es compatible'):
            release.bundle(self.xml,self.root/'bad-release')
        self.assertFalse((self.root/'bad-release').exists())

    def test_bundled_library_is_used_even_with_a_legacy_external_configuration(self):
        data=self.root/'nuclear-data';data.mkdir();(data/'cross_sections.xml').write_text('<cross_sections/>')
        with patch.object(control,'ROOT',self.root),patch.dict(os.environ,{'OPENMC_CROSS_SECTIONS':''}):
            self.assertEqual(control.runtime_environment()['OPENMC_CROSS_SECTIONS'],str(data/'cross_sections.xml'))
            (self.root/'irradium-config.json').write_text(json.dumps({'OPENMC_CROSS_SECTIONS':'/existing/library.xml'}))
            self.assertEqual(control.runtime_environment()['OPENMC_CROSS_SECTIONS'],str(data/'cross_sections.xml'))

    def test_local_data_is_preferred_over_the_host_environment(self):
        data=self.root/'nuclear-data';data.mkdir();(data/'cross_sections.xml').write_text('<cross_sections/>')
        with patch.object(control,'ROOT',self.root),patch.dict(os.environ,{'OPENMC_CROSS_SECTIONS':'/external/library.xml'}):
            self.assertEqual(control.runtime_environment()['OPENMC_CROSS_SECTIONS'],str(data/'cross_sections.xml'))

if __name__=='__main__':unittest.main()
