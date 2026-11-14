from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import h5py

from sim_gamma.workers.transport_budget import prune_completed_checkpoints
from sim_gamma.workers.checkpoint_storage import compress_checkpoint, _verify
import numpy as np


class CheckpointStorageTests(unittest.TestCase):
    def test_lossless_compression_preserves_rng_attributes_and_tally_moments(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'statepoint.100.h5'
            with h5py.File(path, 'w') as output:
                output.attrs['filetype'] = np.bytes_('statepoint')
                output['seed'] = 424
                output['n_particles'] = 40000
                output['current_batch'] = 100
                output['n_realizations'] = 100
                output['tallies/results'] = np.zeros((20000, 1, 2))
                output['tallies/results'][0, 0] = [1.234, 5.678]
            original = root / 'original.h5'
            original.write_bytes(path.read_bytes())
            self.assertGreater(compress_checkpoint(path), 0)
            _verify(original, path)
            with h5py.File(path, 'r') as output:
                self.assertEqual(output['seed'][()], 424)
                self.assertEqual(output['tallies/results'].compression, 'gzip')
            with h5py.File(path, 'r+') as output:
                output['seed'][...] = 425
            with self.assertRaises(ValueError):
                _verify(original, path)

    def test_keeps_published_checkpoint_and_never_removes_incomplete_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old, selected, partial = [root / f'statepoint.{n}.h5' for n in (10, 20, 30)]
            for path in (old, selected):
                with h5py.File(path, 'w') as output:
                    output['n_realizations'] = 10
            partial.write_bytes(b'not a complete checkpoint')
            self.assertGreater(prune_completed_checkpoints(root, selected), 0)
            self.assertFalse(old.exists())
            self.assertTrue(selected.exists())
            self.assertTrue(partial.exists())
            self.assertEqual(prune_completed_checkpoints(root, partial), 0)
