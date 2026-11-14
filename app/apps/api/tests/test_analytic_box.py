from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import openmc
import trimesh

from sim_gamma.simulators.openmc.analytic_box import rectangular_geometry, rectangular_surface_bounds


class AnalyticBoxTests(unittest.TestCase):
    def test_exact_axis_aligned_box_is_recognized_but_modified_cad_is_not(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'load.stl'
            box = trimesh.creation.box(extents=[500, 400, 1000])
            box.apply_translation([0, 0, 500])
            box.export(path)
            np.testing.assert_allclose(rectangular_surface_bounds(path), [[-25, -20, 0], [25, 20, 100]])
            for other in (trimesh.creation.cylinder(radius=200, height=1000),
                          trimesh.util.concatenate([box, box.copy().apply_translation([700, 0, 0])])):
                other.export(path)
                self.assertIsNone(rectangular_surface_bounds(path))
            box.apply_transform(trimesh.transformations.rotation_matrix(.3, [0, 0, 1]))
            box.export(path)
            self.assertIsNone(rectangular_surface_bounds(path))

    def test_csg_regions_preserve_load_air_and_vacuum_boundaries(self):
        water, air = openmc.Material(), openmc.Material()
        geometry = rectangular_geometry([[-25, -20, 0], [25, 20, 100]], [[-100, -100, -50], [100, 100, 150]], water, air)
        self.assertIs(geometry.find([24, 19, 99])[-1].fill, water)
        self.assertIs(geometry.find([26, 19, 99])[-1].fill, air)
        self.assertEqual(geometry.find([101, 0, 0]), [])
        self.assertEqual(sum(s.boundary_type == 'vacuum' for s in geometry.get_all_surfaces().values()), 6)

    def test_exterior_material_is_read_from_dagmc_instead_of_assuming_air(self):
        import h5py
        from sim_gamma.simulators.openmc.analytic_box import homogeneous_box_exterior
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'world.h5m'
            for labels, expected in ((['water'], (True, None)),
                                     (['water', 'air_comp'], (True, 'air')),
                                     (['water', 'steel'], (False, None))):
                with h5py.File(path, 'w') as file:
                    file['/tstt/tags/NAME/values'] = np.asarray(['mat:' + label for label in labels], dtype='S32')
                self.assertEqual(homogeneous_box_exterior(path, 'water'), expected)
            geometry = rectangular_geometry([[-25, -20, 0], [25, 20, 100]], [[-100, -100, -50], [100, 100, 150]], openmc.Material(), None)
            self.assertIsNone(geometry.find([26, 0, 50])[-1].fill)

    def test_invalid_or_uncontained_geometry_is_rejected(self):
        for box, world in (([[0,0,0],[0,20,30]],[[-1,-1,-1],[50,50,50]]),
                           ([[0,0,0],[10,20,30]],[[0,-1,-1],[50,50,50]]),
                           ([[0,0,0],[10,20,float('nan')]],[[-1,-1,-1],[50,50,50]])):
            with self.assertRaises(ValueError):
                rectangular_geometry(box, world, openmc.Material(), openmc.Material())

    def test_sources_intersecting_the_load_are_rejected(self):
        from sim_gamma.simulators.openmc.analytic_box import validate_source_clearance
        def source(x):
            return openmc.IndependentSource(space=openmc.stats.CylindricalIndependent(
                r=openmc.stats.PowerLaw(0,.55,1),phi=openmc.stats.Uniform(0,6.283185),
                z=openmc.stats.Uniform(0,45),origin=[x,0,20]))
        validate_source_clearance([source(25.6)],[[-25,-20,0],[25,20,100]])
        with self.assertRaisesRegex(ValueError, 'intersecta'):
            validate_source_clearance([source(25.4)],[[-25,-20,0],[25,20,100]])
