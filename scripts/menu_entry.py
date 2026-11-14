"""Register one desktop-menu link; application files stay in the installation."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ENTRY = 'org.irradium.IRRADIUM.desktop'


def record_path(root):
    return root / 'workspace/Configuracion/integracion_escritorio.json'


def refresh(directory):
    command = shutil.which('update-desktop-database')
    if command:
        subprocess.run([command, str(directory)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def register(root, data_home):
    target = root / 'IRRADIUM.desktop'
    if not target.is_file():
        raise ValueError('Primero prepara el lanzador de esta instalación')
    directory = data_home / 'applications'
    destination = directory / ENTRY
    if destination.exists() or destination.is_symlink():
        if not destination.is_symlink() or destination.resolve() != target.resolve():
            raise ValueError('Ya existe un acceso de otra instalación: ' + str(destination))
    else:
        directory.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target)
    record = record_path(root)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({'menu_entry': str(destination), 'target': str(target)}, indent=2) + '\n')
    refresh(directory)
    return destination


def unregister(root):
    record = record_path(root)
    if not record.is_file():
        return
    entry = Path(json.loads(record.read_text())['menu_entry'])
    # Never remove a regular desktop file or another copy's menu entry.
    if (entry.name == ENTRY and entry.parent.name == 'applications'
            and entry.is_symlink() and entry.resolve() == (root / 'IRRADIUM.desktop').resolve()):
        entry.unlink()
        refresh(entry.parent)
    record.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-home', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        print('Acceso al menú de aplicaciones: ' + str(register(root, args.data_home.expanduser().resolve())))
    except (OSError, ValueError) as error:
        parser.exit(1, 'No se registró el acceso al menú: ' + str(error) + '\n')
