from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

import h5py
import numpy as np

from sim_gamma.simulators.openmc import engine
from sim_gamma.simulators.openmc.geometry_contract import geometry_signature, cached_geometry_matches


class GeometryContractTests(unittest.TestCase):
    def test_air_is_forwarded_cache_is_invalidated_and_conversion_is_atomic(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            step = root / 'world.step'
            step.write_text('STEP-one')
            destination = root / 'world.h5m'
            destination.write_bytes(b'legacy-void')

            def convert(staging, name, **kwargs):
                self.assertEqual(kwargs, {'material_tags': ['water'], 'implicit_air': True})
                with h5py.File(Path(staging) / (name + '.h5m'), 'w') as file:
                    file['/tstt/tags/NAME/values'] = np.asarray([b'mat:water', b'mat:air_comp'])

            with patch.object(engine, '_run_isolated_cad_converter', side_effect=convert) as converter:
                engine.generate_openmc_files(root, 'world', ['water'], implicit_air=True)
                self.assertEqual(converter.call_count, 1)
                self.assertTrue(cached_geometry_matches(destination, geometry_signature(step, ['water'], True)))
                engine.generate_openmc_files(root, 'world', ['water'], implicit_air=True)
                self.assertEqual(converter.call_count, 1)
                step.write_text('STEP-two')
                engine.generate_openmc_files(root, 'world', ['water'], implicit_air=True)
                self.assertEqual(converter.call_count, 2)
                before = destination.read_bytes()
                step.write_text('STEP-three')
                converter.side_effect = RuntimeError('Conversion failed')
                with self.assertRaises(RuntimeError):
                    engine.generate_openmc_files(root, 'world', ['water'], implicit_air=True)
                self.assertEqual(before, destination.read_bytes())
                self.assertFalse(cached_geometry_matches(destination, geometry_signature(step, ['water'], True)))

    def test_requested_air_cannot_silently_become_void(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'world.step').write_text('STEP')
            def invalid(staging, name, **kwargs):
                with h5py.File(Path(staging) / (name + '.h5m'), 'w') as file:
                    file['/tstt/tags/NAME/values'] = np.asarray([b'mat:water'])
            with patch.object(engine, '_run_isolated_cad_converter', side_effect=invalid):
                with self.assertRaisesRegex(ValueError, 'aire exterior'):
                    engine.generate_openmc_files(root, 'world', ['water'], implicit_air=True)
            self.assertFalse((root / 'world.h5m').exists())

    def test_occupied_scene_identity_tracks_scene_not_particle_budget(self):
        from sim_gamma.simulators.openmc.geometry_contract import occupied_geometry_key
        with TemporaryDirectory() as directory:
            step=Path(directory)/'load.step';step.write_text('STEP')
            positions=[{'name':'I1','center_mm':[0,0,0],'yaw_degrees':0},
                       {'name':'I2','center_mm':[500,0,0],'yaw_degrees':180}]
            route={'positions':positions,'particles':4_000_000}
            first=occupied_geometry_key(step,route)
            changed={**route,'positions':[positions[0],{**positions[1],'center_mm':[700,0,0]}]}
            self.assertNotEqual(first,occupied_geometry_key(step,changed))
            self.assertEqual(first,occupied_geometry_key(step,{**route,'particles':100_000_000}))
            step.write_text('STEP changed')
            self.assertNotEqual(first,occupied_geometry_key(step,route))
