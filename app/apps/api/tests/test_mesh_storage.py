from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_gamma.workers.mesh_storage import detach_surface_before_write, share_surface_files


class MeshStorageTests(unittest.TestCase):
    def test_preserves_geometry_paths_bytes_and_future_case_writes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'first.stl', root / 'second.stl'
            content = b'archived CAD surface' * 1024
            for path in (first, second):
                path.write_bytes(content)
            released = share_surface_files([first, second], root / 'shared')
            self.assertGreater(released, 0)
            self.assertEqual(first.stat().st_ino, second.stat().st_ino)
            self.assertEqual(first.read_bytes(), content)
            self.assertEqual(second.read_bytes(), content)
            self.assertEqual(share_surface_files([first, second], root / 'shared'), 0)
            detach_surface_before_write(first)
            first.write_bytes(b'new geometry for this work directory')
            self.assertEqual(second.read_bytes(), content)
            self.assertNotEqual(first.stat().st_ino, second.stat().st_ino)
            self.assertEqual(next((root / 'shared').iterdir()).read_bytes(), content)

    def test_different_meshes_remain_separate_and_corrupt_cache_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'first.stl', root / 'second.stl'
            first.write_bytes(b'first geometry')
            second.write_bytes(b'second geometry')
            share_surface_files([first, second], root / 'shared')
            self.assertNotEqual(first.stat().st_ino, second.stat().st_ino)
            canonical = next(path for path in (root / 'shared').iterdir() if path.read_bytes() == b'first geometry')
            detach_surface_before_write(first)
            first.write_bytes(b'first geometry')
            canonical.write_bytes(b'corrupted')
            with self.assertRaises(ValueError):
                share_surface_files([first], root / 'shared')
            self.assertEqual(first.read_bytes(), b'first geometry')
