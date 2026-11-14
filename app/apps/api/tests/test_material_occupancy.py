import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from sim_gamma.simulators.openmc import material_occupancy as occupancy
from sim_gamma.simulators.openmc.structured_postprocess import (
    RegularMeshSpec, _bounded_contains, build_material_volume_fractions,
)


def sampled_reference(surface, mesh, n=5):
    """Independent full-voxel oracle: never uses a boundary approximation."""
    indices = np.stack(np.meshgrid(*(np.arange(s) for s in mesh.dimension), indexing='ij'), axis=-1).reshape(-1, 3, order='F')
    offsets = (np.arange(n) + .5) / n
    shifts = np.stack(np.meshgrid(offsets, offsets, offsets, indexing='ij'), axis=-1).reshape(-1, 3)
    points = np.asarray(mesh.lower_left) + (indices[:, None, :] + shifts) * mesh.spacing
    return _bounded_contains(surface, points.reshape(-1, 3)).reshape(-1, n ** 3).mean(axis=1).astype(np.float32)


class MaterialOccupancyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        outer = trimesh.creation.icosphere(subdivisions=2, radius=1.35)
        inner = trimesh.creation.icosphere(subdivisions=2, radius=.65)
        inner.invert()
        self.shell = trimesh.util.concatenate([outer, inner])
        self.shell.apply_translation((.071, .113, .137))
        self.path = self.root / 'hdpe.stl'
        self.shell.export(self.path)
        # Compare to the same float32 STL surface used by production.
        self.shell = trimesh.load_mesh(self.path, process=True)
        self.mesh = RegularMeshSpec((8, 8, 8), (-2., -2., -2.), (2., 2., 2.))

    def tearDown(self):
        self.temporary.cleanup()

    def test_curved_hollow_solid_matches_full_125_point_oracle(self):
        actual, _ = build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', samples_per_axis=5)
        expected = sampled_reference(self.shell, self.mesh)
        np.testing.assert_array_equal(actual[0], expected)
        self.assertFalse(self.shell.contains([[.071, .113, .137]])[0], 'Inner cavity was filled')
        self.assertTrue(np.any((actual[0] > 0) & (actual[0] < 1)))

    def test_separate_bodies_parallel_and_serial_match_aggregate_oracle(self):
        left = self.shell.copy(); left.apply_translation((-2., 0., 0.))
        right = self.shell.copy(); right.apply_translation((2., 0., 0.))
        a, b, combined = self.root / 'left.stl', self.root / 'right.stl', self.root / 'combined.stl'
        left.export(a); right.export(b)
        surface = trimesh.util.concatenate([left, right]); surface.export(combined)
        surface = trimesh.load_mesh(combined, process=True)
        mesh = RegularMeshSpec((16, 8, 8), (-4., -2., -2.), (4., 2., 2.))
        options = dict(samples_per_axis=5, component_surface_files={'hdpe': [a, b]})
        serial, _ = build_material_volume_fractions({'hdpe': combined}, mesh, self.root / 'serial', cpu_threads=1, **options)
        parallel, _ = build_material_volume_fractions({'hdpe': combined}, mesh, self.root / 'parallel', cpu_threads=2, **options)
        np.testing.assert_array_equal(serial, parallel)
        np.testing.assert_array_equal(serial[0], sampled_reference(surface, mesh))

    def test_conservative_boundary_includes_both_sides_of_grid_plane(self):
        triangle = trimesh.Trimesh(vertices=[[0., -1., -1.], [0., 1., -1.], [0., 0., 1.]], faces=[[0, 1, 2]], process=False)
        mask = occupancy.boundary_voxels(triangle, self.mesh).reshape(self.mesh.dimension, order='F')
        self.assertTrue(mask[3].any())
        self.assertTrue(mask[4].any())
        self.assertFalse(mask[:3].any())
        self.assertFalse(mask[5:].any())

    def test_warm_cache_needs_no_mesh_loading_and_transform_invalidates_it(self):
        options = dict(samples_per_axis=5, component_surface_files={'hdpe': [self.path]})
        cold, _ = build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', **options)
        with patch('trimesh.load_mesh', side_effect=AssertionError('Warm cache rebuilt geometry')):
            warm, _ = build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', **options)
        np.testing.assert_array_equal(cold, warm)
        moved, _ = build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', component_translation_cm=(.5, 0., 0.), **options)
        translated = self.shell.copy(); translated.apply_translation((.5, 0., 0.))
        np.testing.assert_array_equal(moved[0], sampled_reference(translated, self.mesh))
        self.assertFalse(np.array_equal(moved, cold))

    def test_interrupted_body_resumes_from_saved_blocks(self):
        calls = 0
        def interrupted(surface, points):
            nonlocal calls
            calls += 1
            if calls == 5:
                raise RuntimeError('interrupted preparation')
            return _bounded_contains(surface, points)
        with patch.object(occupancy, 'CHECKPOINT_SECONDS', 0), patch('sim_gamma.simulators.openmc.structured_postprocess._bounded_contains', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'interrupted preparation'):
                build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', samples_per_axis=5, chunk_cells=16)
        partials = list((self.root / 'cache').rglob('*.partial.npz'))
        self.assertEqual(len(partials), 1)
        with np.load(partials[0]) as saved:
            self.assertGreater(int(saved['next_index']), 0)
        resumed, _ = build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', samples_per_axis=5, chunk_cells=16)
        np.testing.assert_array_equal(resumed[0], sampled_reference(self.shell, self.mesh))
        self.assertEqual(list((self.root / 'cache').rglob('*.partial.npz')), [])

    def test_nearest_surface_queries_are_not_used(self):
        with patch('trimesh.proximity.ProximityQuery.on_surface', side_effect=AssertionError('Expensive nearest query')):
            build_material_volume_fractions({'hdpe': self.path}, self.mesh, self.root / 'cache', samples_per_axis=5)

    def test_overlapping_materials_remain_rejected(self):
        with self.assertRaisesRegex(ValueError, 'superponen'):
            build_material_volume_fractions({'a': self.path, 'b': self.path}, self.mesh, self.root / 'cache', samples_per_axis=5)

    def test_shared_cavity_interface_recheck_conserves_volume(self):
        # Closed inner material and its hollow outer shell share the same
        # interface. A preexisting ambiguous ray sample must not make this
        # valid assembly fail, nor may a real overlapping volume be hidden.
        outer = trimesh.creation.box(extents=(2., 2., 2.))
        inner = trimesh.creation.box(extents=(1.4, 1.4, 1.4))
        cavity = inner.copy(); cavity.invert()
        shell_path, inner_path = self.root / 'shell.stl', self.root / 'inner.stl'
        trimesh.util.concatenate([outer, cavity]).export(shell_path)
        inner.export(inner_path)
        mesh = RegularMeshSpec((2, 2, 2), (-1., -1., -1.), (1., 1., 1.))
        ambiguous = np.full((2, 8), .55, dtype=np.float32)
        repaired = occupancy.reconcile_boundary_cells(ambiguous,
            [('shell', shell_path, [0., 0., 0.], 1.), ('water', inner_path, [0., 0., 0.], 1.)],
            mesh, 5, {'shell': 0, 'water': 1})
        self.assertEqual(repaired, 8)
        np.testing.assert_allclose(ambiguous.sum(axis=0), 1., atol=1e-6)
        # Use fully overlapping outer solids to cover entire sample volumes.
        outer.export(inner_path)
        overlapping = np.ones((2, 8), dtype=np.float32)
        occupancy.reconcile_boundary_cells(overlapping,
            [('a', inner_path, [0., 0., 0.], 1.), ('b', inner_path, [0., 0., 0.], 1.)],
            mesh, 5, {'a': 0, 'b': 1})
        np.testing.assert_array_equal(overlapping.sum(axis=0), 2.)

    def test_aggregate_stl_retry_uses_original_nested_cad_bodies(self):
        import cadquery as cq
        outer = cq.Workplane('XY').box(4, 4, 4).val()
        cavity = cq.Workplane('XY').box(3, 3, 3).val()
        shell = outer.cut(cavity)
        insert = cq.Workplane('XY').box(.5, .5, .5).translate((-1, 0, 0)).val()
        water = cq.Workplane('XY').box(1, 1, 1).translate((.5, 0, 0)).val()
        paths = []
        for name, shape in [('shell', shell), ('insert', insert), ('water', water)]:
            path = self.root / (name + '.stl')
            cq.exporters.export(shape, str(path))
            cq.exporters.export(shape, str(path.with_suffix('.step')))
            paths.append(path)
        aggregate = self.root / 'pp.stl'
        trimesh.util.concatenate([trimesh.load_mesh(path) for path in paths[:2]]).export(aggregate)
        mesh = RegularMeshSpec((4, 4, 4), (-2., -2., -2.), (2., 2., 2.))
        original = occupancy._component
        def ambiguous(task, *args):
            if task[1] == aggregate:
                return 'pp', np.arange(64), np.ones(64, dtype=np.float32), False
            return original(task, *args)
        with patch.object(occupancy, '_component', side_effect=ambiguous):
            values, labels = build_material_volume_fractions(
                {'pp': aggregate, 'water': paths[2]}, mesh, self.root / 'native-cache',
                samples_per_axis=5, component_surface_files={'pp': paths[:2], 'water': paths[2:]})
        self.assertTrue(np.all(values.sum(axis=0) <= 1 + 1e-6))
        self.assertTrue(np.any((values[0] < 1) & (values[1] > 0)))
        self.assertEqual([label['name'] for label in labels], ['pp', 'water'])
