import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import h5py

from sim_gamma.workers.interval_restart import configure_restart, compatible_restart

MODEL = '<model><geometry><cell id="1" material="1"/></geometry><settings><particles>40000</particles><batches>{batches}</batches><seed>{seed}</seed><state_point><batches>{batches}</batches></state_point></settings></model>'


class IntervalRestartTests(unittest.TestCase):
    def test_reuse_requires_identical_model_and_preserves_original_batch_size(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'monte_carlo/positions/run/001'
            work = old / 'work'
            work.mkdir(parents=True)
            checkpoint = work / 'statepoint.100.h5'
            with h5py.File(checkpoint, 'w') as file:
                file['n_particles'] = 40000
                file['current_batch'] = 100
            summary = old / 'result.json'
            summary.write_text(json.dumps({'statepoint': str(checkpoint.relative_to(root)), 'transport_wall_seconds': 7}))
            (work / 'model.xml').write_text(MODEL.format(batches=100, seed=1))
            settings = SimpleNamespace(particles=80000, batches=100)
            candidate = configure_restart(settings, summary, 8_100_000)
            self.assertEqual(settings.particles, 40000)
            self.assertEqual(settings.batches, 202)
            self.assertLessEqual(settings.particles * settings.batches, 8_100_000)
            current = root / 'model.xml'
            current.write_text(MODEL.format(batches=202, seed=1))
            accepted = compatible_restart(candidate, current)
            self.assertEqual(accepted['histories'], 4_000_000)
            self.assertEqual(accepted['transport_seconds'], 7)
            current.write_text(MODEL.format(batches=202, seed=2))
            self.assertIsNone(compatible_restart(candidate, current))
            self.assertIsNone(configure_restart(settings, summary, 4_000_000))
