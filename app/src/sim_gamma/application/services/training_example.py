"""Create a reproducible teaching case without replacing the user's inventory."""
import json
from pathlib import Path
import shutil
from .case_store import CaseStore


def create_training_case(output_root: Path, bundle: Path, variant='water'):
    if variant not in {'water','medical_kit'}:
        raise ValueError('Unknown teaching load')
    provenance = json.loads((bundle / 'provenance.json').read_text())
    store = CaseStore(output_root, bundle / 'config', bundle / 'water.step', cases_directory_name='Casos', irradiator_type='IV')
    route = json.loads((bundle / 'config/route_parameters.json').read_text())
    name = provenance['name'] if variant=='water' else 'SVST teaching reconstruction · Medical-fluid kit · 54 bodies'
    case = store.create(name,
        'Ejemplo educativo con datos publicados y reconstrucciones identificadas. No es una réplica validada ni un caso operativo.',
        route_configuration=route)
    root = store.root(case['case_id'])
    try:
        source=bundle / (variant+'.step')
        shutil.copy2(source, root / 'input/load.step')
        materials = json.loads((bundle / 'config/materials.json').read_text())['materials']
        tags=['water'] if variant=='water' else ['water','pp','pc','silicone','steel']
        selected=[m for m in materials if m['name'] in tags]
        case = store.set_step_input(case['case_id'], source.name, source.stat().st_size, selected, tags)
        shutil.copy2(bundle / 'provenance.json', root / 'input/example_provenance.json')
        case['teaching_example'] = provenance['id']
        store._write_json(root / 'manifest.json', case)
        return case
    except Exception:
        store.delete(case['case_id'])
        raise
