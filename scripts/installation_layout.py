"""Local installation layout; no scientific or third-party dependencies."""
import json
import os
from pathlib import Path
from uuid import uuid4


def migrate_json(source: Path, destination: Path):
    """Keep legacy entry points as relative links to one authoritative file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if source.resolve() != destination.resolve():
            raise ValueError(f"Enlace de configuración inesperado: {source}")
        return
    if source.exists():
        original = json.loads(source.read_text(encoding="utf-8"))
        if destination.exists():
            if json.loads(destination.read_text(encoding="utf-8")) != original:
                raise ValueError(f"Hay dos configuraciones distintas: {source} y {destination}")
        else:
            temporary = destination.with_name('.migration-' + uuid4().hex)
            temporary.write_bytes(source.read_bytes())
            temporary.replace(destination)
        source.unlink()
    if destination.is_file():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.symlink_to(os.path.relpath(destination, source.parent))


def organize_user_data(root: Path):
    root = root.resolve()
    workspace = root / 'workspace'
    config = workspace / 'Configuracion'
    config.mkdir(parents=True, exist_ok=True)
    migrate_json(root / 'irradium-config.json', config / 'instalacion.json')
    if not (config / 'instalacion.json').exists():
        (config / 'instalacion.json').write_text(json.dumps({
            'OPENMC_CROSS_SECTIONS': '', 'PHITSPATH': '',
        }, indent=2) + '\n', encoding='utf-8')
        migrate_json(root / 'irradium-config.json', config / 'instalacion.json')
    migrate_json(root / '.local/config/preferences.json', config / 'preferencias.json')
    for source, target in (
        (root / 'visualizaciones', workspace / 'Animaciones'),
        (root / '.local/validation', workspace / 'Pruebas/Validacion_interna'),
    ):
        if source.is_symlink():
            if source.resolve() != target.resolve():
                raise ValueError(f'Enlace de datos inesperado: {source}')
        elif source.exists():
            if target.exists():
                raise ValueError(f'Hay dos carpetas de datos; no se sobrescribirá ninguna: {source}, {target}')
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            source.symlink_to(os.path.relpath(target, source.parent), target_is_directory=True)
        elif target.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.symlink_to(os.path.relpath(target, source.parent), target_is_directory=True)


def desktop_quote(value):
    # Desktop Entry Exec is not a shell. Percent signs are field-code escapes.
    value = str(value).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return '"' + value + '"'


def write_launchers(root: Path):
    header = '[Desktop Entry]\nType=Application\nVersion=1.0\n'
    launch = header + (
        'Name=IRRADIUM\nGenericName=Irradiation Modeling and Simulation\n'
        'Comment=Modelado, simulación y análisis de irradiación\n'
        f'Exec={desktop_quote(root / "Iniciar_IRRADIUM.sh")}\n'
        f'Icon={root / "app/icons/icon-512.png"}\n'
        'Terminal=false\nCategories=Science;Engineering;\n'
    )
    uninstall = header + (
        'Name=Desinstalar IRRADIUM\n'
        'Comment=Permite conservar los casos, estudios y configuraciones\n'
        f'Exec={desktop_quote(root / "Desinstalar_IRRADIUM.sh")}\n'
        'Icon=system-software-install\nTerminal=true\nCategories=System;\n'
    )
    for name, content in [('IRRADIUM.desktop', launch), ('Desinstalar_IRRADIUM.desktop', uninstall)]:
        path = root / name
        path.write_text(content, encoding='utf-8')
        path.chmod(0o755)
