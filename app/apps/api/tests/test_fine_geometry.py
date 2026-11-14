from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import trimesh

from sim_gamma.simulators.openmc.structured_postprocess import (
    RegularMeshSpec, _bounded_contains, _orthogonal_volume_boxes,
    build_material_volume_fractions,
)


class FineGeometryTests(unittest.TestCase):
    def test_thin_rectilinear_material_has_exact_mass_at_both_resolutions(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shape = trimesh.creation.box(extents=(2, 2, .16))
            shape.apply_translation((0, 0, .08))
            path = root / 'carton.stl'
            shape.export(path)
            for size in (1, .5):
                mesh = RegularMeshSpec((int(2 / size), int(2 / size), int(1 / size)), (-1, -1, 0), (1, 1, 1))
                fractions, _ = build_material_volume_fractions({'carton': path}, mesh, root / 'cache',
                                                               samples_per_axis=3, split_disjoint=True)
                # Sampling at only three heights would turn 0.16 into 1/3 or 0.
                # Exact clipping must preserve the slab's CAD volume instead.
                self.assertAlmostEqual(float(fractions.sum()) * size**3, .64, places=6)

    def test_curved_and_rotated_solids_do_not_use_rectilinear_clipping(self):
        sphere = trimesh.creation.icosphere(subdivisions=1)
        self.assertIsNone(_orthogonal_volume_boxes(sphere))
        cube = trimesh.creation.box()
        cube.apply_transform(trimesh.transformations.rotation_matrix(.2, [0, 0, 1]))
        self.assertIsNone(_orthogonal_volume_boxes(cube))

    def test_contains_batches_fallback_queries_without_changing_points(self):
        class Fallback:
            def __init__(self):
                self.ray = object()
                self.calls = []
            def contains(self, points):
                self.calls.append(len(points))
                return points[:, 0] > 0
        surface = Fallback()
        points = np.linspace(-1, 1, 2100).reshape(-1, 3)
        np.testing.assert_array_equal(_bounded_contains(surface, points), points[:, 0] > 0)
        self.assertLessEqual(max(surface.calls), 128)
