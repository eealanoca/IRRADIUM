"""Indicative precision cost from a completed, physically compatible pilot."""
from functools import lru_cache
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from sim_gamma.simulators.openmc.geometry_contract import TRANSPORT_GEOMETRY_REVISION


def estimate_interval(root: Path, configuration: dict, request: dict):
    target = request.get('accumulated_error_target')
    if not target:
        return None
    paths = sorted((root / 'results/studies').glob('*.json'), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    from sim_gamma.domain.route_study import selected_position_indices
    positions = configuration['positions']
    selected = [positions[index] for index in selected_position_indices(positions, request)]
    for path in paths:
        study = json.loads(path.read_text())
        if study.get('transport_geometry_revision') != TRANSPORT_GEOMETRY_REVISION or not study.get('complete') or study.get('quick'):
            continue
        if any(study.get(key) != configuration.get(key) for key in ('cell_size_cm', 'occupancy', 'variance_reduction', 'source_mixture_fraction', 'dose_tally')):
            continue
        if request.get('cpu_cores') and request['cpu_cores'] != study.get('cpu_cores'):
            continue
        by_name = {position['name']: position for position in study['positions']}
        if not all(position['name'] in by_name and by_name[position['name']]['center_mm'] == position['center_mm']
                   and by_name[position['name']]['yaw_degrees'] == position['yaw_degrees'] + configuration.get('cad_offset_degrees', 0)
                   for position in selected):
            continue
        return _from_maps(str(root.resolve()), str(path.resolve()), path.stat().st_mtime_ns,
                          tuple(position['name'] for position in selected), float(target))
    return {'available': False, 'reason': 'Se necesita un piloto completo con aire exterior, la misma malla, método y ocupación.'}


@lru_cache(maxsize=32)
def _from_maps(root_string, archive_string, revision, names, target):
    from sim_gamma.domain.route_map import write_route_dose_map
    root = Path(root_string)
    study = json.loads(Path(archive_string).read_text())
    by_name = {position['name']: position for position in study['positions']}
    positions = [by_name[name] for name in names]
    with TemporaryDirectory(prefix='irradium-precision-') as directory:
        combined = write_route_dose_map(root, positions, [60] * len(positions), Path(directory) / 'sum.vti', sum_rates=True)
    precision = combined['precision']
    maximum = combined.get('relative_error_maximum')
    if maximum is None or not precision['active_cells'] or precision['cells_with_error_estimate'] != precision['active_cells']:
        return {'available': False, 'reason': 'El piloto aún no ofrece cobertura estadística completa del intervalo.'}
    factor = max((maximum / (target * .97)) ** 2, 1e-12)
    histories = [max(100_000, int(math.ceil(position['simulated_particles'] * factor / 100_000)) * 100_000)
                 for position in positions]
    from sim_gamma.domain.interval_budget import plan_interval_histories
    optimized = plan_interval_histories(root, positions, target, 10**12, initial_histories=4_000_000, return_projection=True)
    projected = optimized['projected_relative_error_maximum']
    method = 'uniform-variance-scaling-with-3-percent-planning-margin'
    if projected is not None and projected <= target and sum(optimized['histories']) < sum(histories):
        histories = optimized['histories']
        method = 'adaptive-variance-allocation-with-3-percent-planning-margin'
    seconds = 0
    for position, count in zip(positions, histories, strict=True):
        summary = json.loads((root / position['summary']).read_text())
        seconds += summary['transport_wall_seconds'] * count / position['simulated_particles']
        seconds += summary.get('preparation_wall_seconds', 0) + summary.get('postprocess_wall_seconds', 0)
    maximum_budget = int(math.ceil(max(histories) * 1.2 / 1_000_000)) * 1_000_000
    suggested_time = int(math.ceil((seconds * 1.25 + 30) / 60)) * 60
    return {
        'available': True, 'provisional': True, 'reference_run_id': study['run_id'],
        'transport_geometry_revision': TRANSPORT_GEOMETRY_REVISION,
        'target': target, 'pilot_relative_error_maximum': maximum,
        'estimated_total_histories': sum(histories),
        'estimated_histories_per_position_minimum': min(histories),
        'estimated_histories_per_position_maximum': max(histories),
        'estimated_seconds': int(math.ceil(seconds)),
        'suggested_max_particles_per_position': maximum_budget,
        'suggested_time_limit_seconds': suggested_time,
        'position_count': len(positions), 'active_voxels': precision['active_cells'],
        'method': method,
        'note': 'Estimación inicial por R ∝ 1/√N y varianzas del piloto; no garantiza convergencia. Las contribuciones raras del aire requieren validación durante la ejecución.',
    }
