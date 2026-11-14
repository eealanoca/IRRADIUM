"""Interactive, installation-local uninstaller. Never follows external symlinks."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROGRAM_ENTRIES = (
    'app', 'runtime', 'scripts', '.local', 'distribution', 'docs',
    'nuclear-data', 'irradium', 'install.sh', 'uninstall.sh', 'LICENSE', 'DATA_NOTICES.md', 'Iniciar_IRRADIUM.sh', 'Desinstalar_IRRADIUM.sh',
    'IRRADIUM.desktop', 'Desinstalar_IRRADIUM.desktop', 'installation-manifest.json',
    'irradium-config.json', 'irradium-config.example.json', 'README.md', 'README.es.md', 'ARCHITECTURE.md',
)
ACTIVE_STATES = {'running', 'queued', 'pausing', 'cancelling'}


def validate_root(root):
    if root == Path('/') or root == Path.home() or len(root.parts) < 3:
        raise ValueError('La ruta no es una carpeta de instalación segura')
    manifest = root / 'installation-manifest.json'
    if manifest.is_symlink() or json.loads(manifest.read_text()).get('application') != 'IRRADIUM':
        raise ValueError('No se reconoce el manifiesto de IRRADIUM')
    for name in ('app', 'scripts', 'workspace', 'runtime', '.local'):
        if (root / name).is_symlink():
            raise ValueError(f'{name} debe estar dentro de esta instalación, sin enlaces externos')
    if not (root / 'app/src/sim_gamma/branding.py').is_file() or not (root / 'irradium').is_file():
        raise ValueError('No se reconoce la estructura de IRRADIUM')


def check_jobs(root):
    for engine in ('monte_carlo', 'deterministic'):
        for path in (root / 'workspace/irradiators').glob(f'*/Casos/*/{engine}/status.json'):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError(f'Estado fuera de la instalación: {path}')
            if json.loads(path.read_text()).get('state') in ACTIVE_STATES:
                raise ValueError('Hay un cálculo activo o pendiente. Páusalo o detenlo desde la aplicación antes de desinstalar: ' + str(path.parent.parent.name))


def identity(pid):
    try:
        proc = Path('/proc') / str(pid)
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z' or proc.stat().st_uid != os.getuid():
            return None
        return {'start_ticks': int(fields[19]), 'pgid': int(fields[2])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def stop_services(root):
    for service in ('web', 'api'):
        path = root / '.local/state' / (service + '.json')
        if not path.is_file():
            continue
        info = json.loads(path.read_text())
        actual = identity(int(info['pid']))
        if actual is None:
            continue
        expected = {key: info[key] for key in ('start_ticks', 'pgid')}
        # A reused PID is never signalled. A live unrecognised record blocks deletion.
        if info.get('root') != str(root) or actual != expected or actual['pgid'] == os.getpgrp():
            raise ValueError('No se pudo verificar el proceso propio de ' + service)
        command = (Path('/proc') / str(info['pid']) / 'cmdline').read_bytes()
        if os.fsencode(str(root)) not in command:
            raise ValueError('El proceso registrado no pertenece a esta instalación')
        try:
            os.killpg(actual['pgid'], signal.SIGTERM)
        except ProcessLookupError:
            continue
        deadline = time.monotonic() + 10
        while identity(int(info['pid'])) == expected and time.monotonic() < deadline:
            time.sleep(.1)
        if identity(int(info['pid'])) == expected:
            raise ValueError('El servicio no se detuvo. No se borró la instalación: ' + service)


def plan(root, keep_data):
    validate_root(root)
    remove = [name for name in PROGRAM_ENTRIES if (root / name).exists() or (root / name).is_symlink()]
    if keep_data and '.local' in remove:
        # Keep old absolute references to preserved pilot geometries and preferences.
        # Their real contents are migrated into workspace/, not copied.
        remove.remove('.local')
        remove += [f'.local/{name}' for name in ('cache', 'tmp', 'empty', 'share', 'state')
                   if (root / '.local' / name).exists() or (root / '.local' / name).is_symlink()]
    if not keep_data:
        remove += [name for name in ('workspace', 'visualizaciones') if (root / name).exists() or (root / name).is_symlink()]
    retained = sorted(p.name for p in root.iterdir() if p.name not in remove)
    return {'installation': str(root), 'keep_data': keep_data, 'remove': remove,
            'preserve': retained, 'data_directory': str(root / 'workspace'),
            'external_openmc_and_phits': 'untouched'}


def uninstall(root, keep_data):
    operations = plan(root, keep_data)
    check_jobs(root)
    stop_services(root)
    check_jobs(root)  # Catch a submission made while services were stopping.
    menu_script = root / 'scripts/menu_entry.py'
    if menu_script.is_file():
        spec = importlib.util.spec_from_file_location('irradium_menu_entry', menu_script)
        menu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(menu)
        menu.unregister(root)
    if keep_data:
        # Load before deleting scripts/runtime. Migrations refuse conflicting copies.
        spec = importlib.util.spec_from_file_location('installation_layout', root / 'scripts/installation_layout.py')
        layout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout)
        layout.organize_user_data(root)
        (root / 'DATOS_CONSERVADOS.txt').write_text(
            'IRRADIUM fue desinstalado. Los casos, estudios, fuentes, configuraciones,\n'
            'animaciones e informes se conservan dentro de workspace/.\n'
            'Para restaurarlos, instala IRRADIUM en esta misma carpeta; el instalador\n'
            'reutiliza workspace/ sin sobrescribir los casos ni sus configuraciones.\n'
            'Conserva la ruta de esta carpeta: los estudios antiguos pueden guardar\n'
            'referencias absolutas. Los enlaces de compatibilidad en .local/ y\n'
            'visualizaciones/ también se conservan. No se eliminaron OpenMC/PHITS externos.\n', encoding='utf-8')
    # Only named application entries; never recurse through an external link.
    # Scripts are removed last, and all required modules are already loaded.
    ordered = [name for name in operations['remove'] if name != 'scripts'] + ['scripts']
    for name in ordered:
        path = root / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    try:
        root.rmdir()  # Only succeeds if no preserved/unknown user files remain.
    except OSError:
        pass
    return operations


def main(argv=None):
    parser = argparse.ArgumentParser(description='Desinstala únicamente esta copia de IRRADIUM.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--keep-data', action='store_true', help='Conservar workspace/ completo (recomendado).')
    mode.add_argument('--remove-data', action='store_true', help='Eliminar también todos los casos y estudios.')
    parser.add_argument('--dry-run', action='store_true', help='Mostrar el plan sin modificar ningún archivo.')
    parser.add_argument('--yes', action='store_true', help='Confirmación explícita para uso automatizado; exige elegir modo.')
    args = parser.parse_args(argv)
    validate_root(ROOT)
    if args.dry_run:
        print(json.dumps(plan(ROOT, not args.remove_data), indent=2, ensure_ascii=False))
        return 0
    if args.yes and not (args.keep_data or args.remove_data):
        parser.error('--yes exige --keep-data o --remove-data')
    check_jobs(ROOT)
    keep = not args.remove_data
    print('Se desinstalará únicamente IRRADIUM de:\n  ' + str(ROOT))
    print('OpenMC y PHITS instalados fuera de esta carpeta permanecerán intactos.')
    if not args.yes:
        if not sys.stdin.isatty():
            parser.error('Abre el desinstalador en una terminal para responder; no se modificó nada.')
        if not (args.keep_data or args.remove_data):
            choice = input('\n¿Conservar los casos de irradiación, estudios, resultados y configuraciones? [S/n/cancelar]: ').strip().lower()
            if choice in ('cancelar', 'c', 'cancel'):
                print('Desinstalación cancelada.'); return 0
            if choice not in ('', 's', 'si', 'sí', 'n', 'no'):
                print('Respuesta no reconocida. Desinstalación cancelada.'); return 0
            keep = choice not in ('n', 'no')
        phrase = 'DESINSTALAR' if keep else 'ELIMINAR DATOS'
        print('\n' + ('Se conservará workspace/ íntegro.' if keep else 'Se eliminarán permanentemente TODOS los casos, estudios y resultados de esta instalación.'))
        if input('Escribe ' + phrase + ' para confirmar: ').strip() != phrase:
            print('Desinstalación cancelada.'); return 0
    uninstall(ROOT, keep)
    print('IRRADIUM desinstalado. ' + ('Datos conservados en ' + str(ROOT / 'workspace') if keep else 'Datos de esta instalación eliminados.'))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('No se completó la desinstalación: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
