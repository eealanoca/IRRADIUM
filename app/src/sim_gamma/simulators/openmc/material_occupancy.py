"""Bounded, resumable material fractions on the unchanged CAD triangulation.

Triangle bounding boxes conservatively mark every voxel a surface can cross.
Only those voxels require the original stratified samples. No nearest-surface
search, decimation, or change to transport geometry is needed.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
from queue import Empty, SimpleQueue
from tempfile import TemporaryDirectory
import time

import numpy as np


VERSION = 'triangle-aabb-stratified-v1'
COMPOSITION_VERSION = 'shared-interface-cad-components-v3'
CHECKPOINT_SECONDS = 5.


def _atomic_npz(path, **arrays):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        np.savez_compressed(temporary, **arrays)
        temporary.replace(path)


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _bounds(path):
    """Read binary STL bounds without constructing another large Trimesh."""
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(84)
    count = int.from_bytes(header[80:84], 'little') if len(header) == 84 else 0
    if count and path.stat().st_size == 84 + count * 50:
        dtype = np.dtype([('normal', '<f4', 3), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
        triangles = np.memmap(path, dtype=dtype, mode='r', offset=84, shape=(count,))
        result = np.array([triangles['vertices'].min(axis=(0, 1)), triangles['vertices'].max(axis=(0, 1))], dtype=np.float64)
        del triangles
        return result
    import trimesh
    return np.asarray(trimesh.load_mesh(path, force='mesh', process=False).bounds)


def boundary_voxels(surface, mesh):
    """Conservative surface/voxel broad phase; false positives are harmless.

    Include both sides of a plane exactly on a cell boundary. All triangles
    remain unchanged. Unique integer boxes avoid revisiting thousands of tiny
    facets which fall in the same voxel.
    """
    lower = np.asarray(mesh.lower_left)
    spacing = np.asarray(mesh.spacing)
    dimensions = np.asarray(mesh.dimension, dtype=np.int64)
    mask = np.zeros(mesh.dimension, dtype=bool, order='F')
    for start in range(0, len(surface.faces), 100_000):
        triangles = surface.vertices[surface.faces[start:start + 100_000]]
        low = np.floor((triangles.min(axis=1) - lower) / spacing - 1e-9).astype(np.int64)
        high = np.floor((triangles.max(axis=1) - lower) / spacing + 1e-9).astype(np.int64)
        keep = np.all(high >= 0, axis=1) & np.all(low < dimensions, axis=1)
        low = np.maximum(low[keep], 0)
        high = np.minimum(high[keep], dimensions - 1)
        boxes = np.unique(np.column_stack((low, high)), axis=0)
        for x, y, z, xx, yy, zz in boxes:
            mask[x:xx + 1, y:yy + 1, z:zz + 1] = True
    return mask.ravel(order='F')


def _read_final(path, cell_count):
    try:
        with np.load(path, allow_pickle=False) as stored:
            ids = np.asarray(stored['indices'], dtype=np.int64)
            values = np.asarray(stored['fractions'], dtype=np.float32)
        if (ids.ndim == 1 and values.shape == ids.shape and np.all(ids >= 0)
                and np.all(ids < cell_count) and np.all(np.diff(ids) > 0)
                and np.isfinite(values).all() and np.all((values > 0) & (values <= 1 + 1e-6))):
            return ids, values
    except (OSError, ValueError, KeyError, EOFError):
        pass
    return None


def _component(task, mesh, cache_root, scale, samples, chunk_cells, updates):
    import trimesh
    from .structured_postprocess import _bounded_contains, _orthogonal_volume_boxes

    material, source, translation = task
    key = json.dumps([VERSION, _file_digest(source), asdict(mesh), scale,
                      translation, samples, bool(trimesh.ray.has_embree)], sort_keys=True)
    cache = Path(cache_root) / material / 'bodies' / (hashlib.sha256(key.encode()).hexdigest()[:32] + '.npz')
    cached = _read_final(cache, mesh.cell_count) if cache.is_file() else None
    if cached is not None:
        return material, *cached, True

    surface = trimesh.load_mesh(source, force='mesh', process=False)
    surface.apply_scale(scale)
    surface.apply_translation(translation)
    # Welding identical coordinates preserves every triangle and its winding,
    # including inner cavity shells, and avoids a duplicate-vertex ray index.
    surface.merge_vertices(digits_vertex=12)
    surface.update_faces(surface.nondegenerate_faces())
    lower = np.asarray(mesh.lower_left)
    spacing = np.asarray(mesh.spacing)
    dimensions = np.asarray(mesh.dimension, dtype=np.int64)
    first = np.maximum(np.floor((surface.bounds[0] - lower) / spacing).astype(np.int64), 0)
    last = np.minimum(np.floor((surface.bounds[1] - lower) / spacing).astype(np.int64), dimensions - 1)
    shape = np.maximum(last - first + 1, 0)
    count = int(np.prod(shape))
    values = np.zeros(count, dtype=np.float32)
    start_at = 0
    partial = cache.with_name(cache.stem + '.partial.npz')
    if partial.is_file():
        try:
            with np.load(partial, allow_pickle=False) as stored:
                previous = np.asarray(stored['fractions'], dtype=np.float32)
                stop = int(stored['next_index'])
            if (previous.shape == values.shape and 0 <= stop <= count
                    and np.isfinite(previous).all() and np.all((previous >= 0) & (previous <= 1 + 1e-6))):
                values = previous
                start_at = stop
        except (OSError, ValueError, KeyError, EOFError):
            pass
    orthogonal = _orthogonal_volume_boxes(surface) if count else None
    boundary = boundary_voxels(surface, mesh) if count and orthogonal is None else None
    offsets = (np.arange(samples) + .5) / samples - .5
    sample_offsets = np.stack(np.meshgrid(offsets, offsets, offsets, indexing='ij'), axis=-1).reshape(-1, 3)
    nx, ny, _ = mesh.dimension
    cx, cy, _ = shape
    checkpoint_at = time.monotonic()
    for start in range(start_at, count, chunk_cells):
        stop = min(start + chunk_cells, count)
        local = np.arange(start, stop, dtype=np.int64)
        i, j, k = first[0] + local % cx, first[1] + (local // cx) % cy, first[2] + local // (cx * cy)
        ids = i + nx * (j + ny * k)
        indices = np.column_stack((i, j, k))
        if orthogonal is not None:
            lo = lower + indices * spacing
            hi = lo + spacing
            overlap = np.maximum(0, np.minimum(hi[:, None, :], orthogonal[1]) - np.maximum(lo[:, None, :], orthogonal[0]))
            values[start:stop] = (np.prod(overlap, axis=2).sum(axis=1) / np.prod(spacing)).astype(np.float32)
        else:
            centers = lower + (indices + .5) * spacing
            block = _bounded_contains(surface, centers).astype(np.float32)
            crossed = boundary[ids]
            if np.any(crossed):
                points = lower + (indices[crossed, None, :] + .5 + sample_offsets[None, :, :]) * spacing
                occupied = _bounded_contains(surface, points.reshape(-1, 3)).reshape(-1, samples ** 3)
                block[crossed] = occupied.sum(axis=1).astype(np.float32) / float(samples ** 3)
            values[start:stop] = block
        if time.monotonic() - checkpoint_at >= CHECKPOINT_SECONDS:
            _atomic_npz(partial, fractions=values, next_index=stop)
            updates.put({'material': material, 'body': source.name, 'cells': stop, 'total_cells': count})
            checkpoint_at = time.monotonic()

    local = np.flatnonzero(values > 0)
    ids = (first[0] + local % cx + nx * (first[1] + (local // cx) % cy
           + ny * (first[2] + local // (cx * cy)))) if count else np.empty(0, dtype=np.int64)
    # Candidate traversal is already in global Fortran order.
    positive = values[local]
    _atomic_npz(cache, indices=ids, fractions=positive)
    partial.unlink(missing_ok=True)
    return material, ids, positive, False


def preparation_workers(tasks, requested, memory_limit_mb):
    """Bound simultaneous ray indices and scratch buffers by current headroom."""
    from sim_gamma.compute_resources import resolve_cpu_threads
    requested = resolve_cpu_threads(requested)
    try:
        resident = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 1024 ** 2
        available = next(int(line.split()[1]) / 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    except (OSError, ValueError, StopIteration):
        resident, available = 512, memory_limit_mb
    largest_mb = max((path.stat().st_size for _, path, _, _ in tasks), default=0) / 1024 ** 2
    per_worker = max(256, largest_mb * 24)
    headroom = max(0, min(memory_limit_mb - resident - 512, available * .6))
    return max(1, min(requested, len(tasks), int(headroom // per_worker)))


def reconcile_boundary_cells(fractions, tasks, mesh, samples, rows, *, _ids=None):
    """Recheck ambiguous interface samples without treating a face as volume.

    Ray parity is undefined exactly on a shared material face. All bodies use
    the same small, deterministic displacement on this retry, so a point has
    one side of the interface. This also avoids mixing exact box fractions
    with sampled fractions in these cells. Never renormalize an actual overlap.
    """
    import trimesh
    from .structured_postprocess import _bounded_contains, _orthogonal_volume_boxes

    ids = (np.flatnonzero(fractions.sum(axis=0, dtype=np.float32) > 1 + 1e-6)
           if _ids is None else _ids)
    if not len(ids):
        return 0
    # A truly overlapping CAD can affect the entire grid. Bound the union
    # sample buffer instead of allocating cells × samples³ for that grid.
    if len(ids) > 2048:
        for start in range(0, len(ids), 2048):
            reconcile_boundary_cells(fractions, tasks, mesh, samples, rows,
                                     _ids=ids[start:start + 2048])
        return len(ids)
    spacing = np.asarray(mesh.spacing)
    lower = np.asarray(mesh.lower_left)
    nx, ny, _ = mesh.dimension
    indices = np.column_stack((ids % nx, (ids // nx) % ny, ids // (nx * ny)))
    offsets = (np.arange(samples) + .5) / samples
    shifts = np.stack(np.meshgrid(offsets, offsets, offsets, indexing='ij'), axis=-1).reshape(-1, 3)
    # One micron at a 1 cm mesh, far below the CAD tessellation tolerance.
    shifts += 1e-4 * np.array([1., np.sqrt(2), np.sqrt(3)])
    lo = lower + indices * spacing
    hi = lo + spacing
    occupied = np.zeros((len(rows), len(ids), samples ** 3), dtype=bool)
    for name, path, translation, scale in tasks:
        bounds = _bounds(path) * scale + translation
        keep = np.all(hi > bounds[0], axis=1) & np.all(lo < bounds[1], axis=1)
        selected = np.flatnonzero(keep)
        if not len(selected):
            continue
        native = None
        cad = Path(path).with_suffix('.step')
        if cad.is_file():
            from cadquery import importers
            native = importers.importStep(str(cad)).solids().vals()
        else:
            surface = trimesh.load_mesh(path, force='mesh', process=False)
            surface.apply_scale(scale)
            surface.apply_translation(translation)
            surface.merge_vertices(digits_vertex=12)
            surface.update_faces(surface.nondegenerate_faces())
            boxes = _orthogonal_volume_boxes(surface)
        for start in range(0, len(selected), 128):
            block = selected[start:start + 128]
            points = lo[block, None, :] + shifts[None, :, :] * spacing
            points = points.reshape(-1, 3)
            if native is not None:
                local = (points - translation) / scale
                inside = np.fromiter((any(solid.isInside(tuple(point), tolerance=1e-9) for solid in native)
                                      for point in local), dtype=bool, count=len(local))
            elif boxes is not None:
                inside = np.any(np.all(points[:, None, :] > boxes[0], axis=2)
                                & np.all(points[:, None, :] < boxes[1], axis=2), axis=1)
            else:
                inside = _bounded_contains(surface, points)
            inside = inside.reshape(-1, samples ** 3)
            # Same-material bodies form a union, including nested assemblies.
            # Different material rows remain independent to detect real overlap.
            occupied[rows[name], block] |= inside
    fractions[:, ids] = occupied.mean(axis=2).astype(np.float32)
    return len(ids)


def material_volume_fractions(material_files, mesh, cache_root, *, length_scale_to_cm=1.,
                              samples_per_axis=3, chunk_cells=1000, component_surface_files=None,
                              component_length_scale_to_cm=None, component_translation_cm=(0., 0., 0.), cpu_threads=1,
                              memory_limit_mb=4096, progress_callback=None):
    samples = int(samples_per_axis)
    if not 1 <= samples <= 7 or chunk_cells < 1:
        raise ValueError('Muestreo o tamaño de bloque inválido')
    scale = float(length_scale_to_cm)
    component_scale = scale if component_length_scale_to_cm is None else float(component_length_scale_to_cm)
    translation = [float(v) for v in component_translation_cm]
    if not all(math.isfinite(value) and value > 0 for value in (scale, component_scale)) or len(translation) != 3 or not np.isfinite(translation).all():
        raise ValueError('Transformación CAD inválida')
    names = sorted(str(name).strip().lower() for name in material_files)
    rows = {name: row for row, name in enumerate(names)}
    fractions = np.zeros((len(names), mesh.cell_count), dtype=np.float32)
    tasks = []
    interface_tasks = []
    for name in names:
        paths = [Path(p) for p in (component_surface_files or {}).get(name, [material_files[name]])]
        body_translation = translation if component_surface_files and name in component_surface_files else [0., 0., 0.]
        body_scale = component_scale if component_surface_files and name in component_surface_files else scale
        if not paths:
            raise ValueError('El material no contiene cuerpos: ' + name)
        # Keep the original CAD bodies for ambiguous interface classification.
        # An aggregate STL may have intersecting bounding boxes even when its
        # constituent solids are disjoint; it has no matching native STEP.
        interface_tasks.extend((name, path, body_translation, body_scale) for path in paths)
        # Explicit CAD bodies preserve nested shells belonging to one hollow
        # solid. For overlapping bounds, retain the original aggregate parity.
        if len(paths) > 1:
            bounds = [_bounds(p) for p in paths]
            separated = all(np.any(a[1] < b[0]) or np.any(b[1] < a[0])
                            for index, a in enumerate(bounds) for b in bounds[index + 1:])
            if not separated:
                paths = [Path(material_files[name])]
                body_translation = [0., 0., 0.]
                body_scale = scale
        tasks.extend((name, path, body_translation, body_scale) for path in paths)
    signature = [COMPOSITION_VERSION, asdict(mesh), samples,
                 [(name, _file_digest(path), translation, body_scale,
                   _file_digest(path.with_suffix('.step')) if path.with_suffix('.step').is_file() else None)
                  for name, path, translation, body_scale in tasks],
                 [(name, _file_digest(path), translation, body_scale,
                   _file_digest(path.with_suffix('.step')) if path.with_suffix('.step').is_file() else None)
                  for name, path, translation, body_scale in interface_tasks]]
    composition = Path(cache_root) / 'compositions' / (hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest() + '.npz')
    labels = [{'id': index + 1, 'name': name} for index, name in enumerate(names)]
    if composition.is_file():
        try:
            with np.load(composition, allow_pickle=False) as stored:
                previous = stored['fractions']
            if (previous.shape == fractions.shape and np.isfinite(previous).all()
                    and np.all(previous >= 0) and np.all(previous.sum(axis=0) <= 1 + 1e-6)):
                if progress_callback:
                    progress_callback({'completed': len(tasks), 'total': len(tasks), 'workers': 0, 'cache_hits': len(tasks)})
                return previous, labels
        except (OSError, ValueError, KeyError, EOFError):
            pass
    workers = preparation_workers(tasks, cpu_threads, float(memory_limit_mb))
    updates = SimpleQueue()
    completed = hits = 0
    def report(**extra):
        if progress_callback:
            progress_callback({'completed': completed, 'total': len(tasks), 'workers': workers, 'cache_hits': hits, **extra})
    report()
    # Each thread owns its Trimesh/Embree index; only immutable CAD files and
    # small returned arrays are shared. Native ray queries run concurrently.
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='cad-material') as pool:
        pending = {pool.submit(_component, (name, path, offset), mesh, cache_root, body_scale,
                               samples, min(chunk_cells, 1000), updates)
                   for name, path, offset, body_scale in tasks}
        while pending:
            done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                name, ids, values, cached = future.result()
                fractions[rows[name], ids] += values
                completed += 1
                hits += int(cached)
                report(material=name)
            latest = None
            while True:
                try:
                    latest = updates.get_nowait()
                except Empty:
                    break
            if latest:
                report(**latest)
    repaired = reconcile_boundary_cells(fractions, interface_tasks, mesh, samples, rows)
    if repaired:
        report(rechecked_interface_cells=repaired)
    tolerance = 1e-6
    overlapping = np.flatnonzero(fractions.sum(axis=0, dtype=np.float32) > 1 + tolerance)
    if len(overlapping):
        cell = int(overlapping[0])
        nx, ny, _ = mesh.dimension
        index = np.array([cell % nx, (cell // nx) % ny, cell // (nx * ny)])
        center = np.asarray(mesh.lower_left) + (index + .5) * mesh.spacing
        involved = ', '.join(name for name in names if fractions[rows[name], cell] > 0)
        coordinate = ', '.join(f'{value:.3f}' for value in center)
        raise ValueError(f'Los volúmenes de los materiales STEP se superponen en {len(overlapping)} celdas. '
                         f'Materiales: {involved}. Primer centro afectado: [{coordinate}] cm en la carga. '
                         'Revisa las intersecciones entre estos cuerpos en el CAD.')
    _atomic_npz(composition, fractions=fractions)
    return fractions, labels
