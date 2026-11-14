from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from sim_gamma.domain.route_map import write_route_dose_map
from sim_gamma.simulators.openmc.structured_postprocess import (
    RegularMeshSpec, tally_absolute_standard_deviation, write_compressed_vti,
)


class AbsoluteUncertaintyTests(unittest.TestCase):
    def test_absolute_moments_preserve_observed_zero_without_hiding_missing_errors(self):
        values = np.array([0, 3, 4.0])
        relative = np.array([np.nan, .2, np.nan])
        result = tally_absolute_standard_deviation(values, relative, 100)
        np.testing.assert_allclose(result, [0, .6, np.nan])
        self.assertTrue(np.isnan(tally_absolute_standard_deviation(values, relative, 1)[0]))

    def test_interval_sums_absolute_variances_including_zero_observations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (2, 1, 1))
            positions = []
            for index, (values, error) in enumerate((([0, 6], [np.nan, .1]), ([4, 0], [.1, np.nan]))):
                folder = root / str(index)
                folder.mkdir()
                write_compressed_vti(values, mesh, folder / 'dose_distribution.vti', relative_error=error,
                                     absolute_std_dev=tally_absolute_standard_deviation(values, error, 100))
                positions.append({'name': str(index), 'summary': f'{index}/result.json', 'yaw_degrees': 180 * index})
            result = write_route_dose_map(root, positions, [30, 60], root / 'sum.vti')
            self.assertEqual(result['precision']['cells_with_error_estimate'], 2)
            self.assertEqual(result['absolute_uncertainty_maps'], 2)
            self.assertEqual(result['observed_zero_contributions'], 2)
            self.assertAlmostEqual(result['relative_error_maximum'], .1, places=6)
            # Old files without absolute moments retain their conservative
            # treatment; a missing relative error is not reinterpreted as zero.
            write_compressed_vti([0, 6], mesh, root / '0/dose_distribution.vti', relative_error=[np.nan, .1])
            result = write_route_dose_map(root, positions, [30, 60], root / 'legacy.vti')
            self.assertEqual(result['precision']['cells_with_error_estimate'], 1)
