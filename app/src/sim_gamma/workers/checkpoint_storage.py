"""Lossless HDF5 checkpoint compression, verified before atomic replacement."""
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import h5py
import numpy as np


def _equal(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype != b.dtype or a.shape != b.shape:
        return False
    return np.array_equal(a, b, equal_nan=True) if a.dtype.kind in 'fc' else np.array_equal(a, b)


def _verify(original, repacked):
    with h5py.File(original, 'r') as before, h5py.File(repacked, 'r') as after:
        left, right = {}, {}
        before.visititems(lambda name, obj: left.update({name: obj}))
        after.visititems(lambda name, obj: right.update({name: obj}))
        if left.keys() != right.keys():
            raise ValueError('El checkpoint comprimido cambió su estructura')
        for name, obj in {'': before, **left}.items():
            other = after if name == '' else right[name]
            if obj.attrs.keys() != other.attrs.keys() or any(not _equal(obj.attrs[key], other.attrs[key]) for key in obj.attrs):
                raise ValueError('El checkpoint comprimido cambió sus atributos')
            if isinstance(obj, h5py.Dataset):
                if obj.shape != other.shape or obj.dtype != other.dtype:
                    raise ValueError('El checkpoint comprimido cambió sus datasets')
                if not obj.shape:
                    equal = _equal(obj[()], other[()])
                else:
                    rows = max(1, 1_000_000 // max(1, int(np.prod(obj.shape[1:]))))
                    equal = all(_equal(obj[start:start + rows], other[start:start + rows])
                                for start in range(0, obj.shape[0], rows))
                if not equal:
                    raise ValueError('El checkpoint comprimido cambió valores del cálculo')


def compress_checkpoint(path: Path) -> int:
    executable = Path(sys.executable).resolve().with_name('h5repack')
    if not executable.is_file() or path.stat().st_size < 65536:
        return 0
    staged = path.with_name(path.name + '.' + uuid4().hex + '.repacked')
    try:
        result = subprocess.run([str(executable), '-f', 'GZIP=1', str(path), str(staged)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('No se pudo comprimir el checkpoint: ' + result.stderr[-1000:])
        _verify(path, staged)
        saved = path.stat().st_size - staged.stat().st_size
        if saved > 0:
            staged.replace(path)
            return saved
        return 0
    finally:
        staged.unlink(missing_ok=True)
