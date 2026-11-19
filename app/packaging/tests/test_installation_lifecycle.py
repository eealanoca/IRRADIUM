import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


layout = load('irradium_layout_test', ROOT / 'scripts/installation_layout.py')
uninstaller = load('irradium_uninstaller_test', ROOT / 'scripts/uninstall.py')
menu = load('irradium_menu_test', ROOT / 'scripts/menu_entry.py')


class InstallationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='irradium-install-test-')
        self.base = Path(self.temp.name)
        self.root = self.base / 'IRRADIUM con espacios %'
        for name in ('app/src/sim_gamma', 'scripts', 'runtime/bin', '.local/config', 'workspace/irradiators/IV/Casos/case/input'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'app/src/sim_gamma/branding.py').write_text('NAME="IRRADIUM"')
        (self.root / 'irradium').write_text('fixture')
        (self.root / 'installation-manifest.json').write_text(json.dumps({'application': 'IRRADIUM'}))
        (self.root / 'irradium-config.json').write_text('{"PHITSPATH":"/outside/phits"}\n')
        (self.root / '.local/config/preferences.json').write_text('{"theme":"dark"}\n')
        self.case = self.root / 'workspace/irradiators/IV/Casos/case/input/load.step'
        self.case.write_bytes(b'original STEP content')
        shutil.copy2(ROOT / 'scripts/installation_layout.py', self.root / 'scripts')
        shutil.copy2(ROOT / 'scripts/uninstall.py', self.root / 'scripts')
        shutil.copy2(ROOT / 'scripts/menu_entry.py', self.root / 'scripts')
        self.outside = self.base / 'original-gamma-sim-and-nuclear-data'
        self.outside.mkdir()
        (self.outside / 'important.h5').write_bytes(b'untouched')

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_is_lossless_idempotent_and_uses_one_configuration(self):
        original = (self.root / 'irradium-config.json').read_bytes()
        animation = self.root / 'visualizaciones/movie'
        animation.mkdir(parents=True); (animation / 'sequence.md').write_text('original sequence')
        layout.organize_user_data(self.root)
        layout.organize_user_data(self.root)
        self.assertEqual((self.root / 'workspace/Configuracion/instalacion.json').read_bytes(), original)
        self.assertTrue((self.root / 'irradium-config.json').is_symlink())
        self.assertEqual((self.root / 'workspace/Configuracion/preferencias.json').read_text(), '{"theme":"dark"}\n')
        self.assertEqual((animation / 'sequence.md').read_text(), 'original sequence')
        self.assertTrue((self.root / 'visualizaciones').is_symlink())
        self.assertEqual(self.case.read_bytes(), b'original STEP content')

    def test_conflicting_configuration_is_never_overwritten(self):
        config = self.root / 'workspace/Configuracion'; config.mkdir()
        (config / 'instalacion.json').write_text('{"PHITSPATH":"different"}')
        with self.assertRaisesRegex(ValueError, 'distintas'):
            layout.organize_user_data(self.root)
        self.assertEqual((self.root / 'irradium-config.json').read_text(), '{"PHITSPATH":"/outside/phits"}\n')

    def test_launchers_quote_paths_and_uninstaller_opens_a_terminal(self):
        layout.write_launchers(self.root)
        desktop = (self.root / 'IRRADIUM.desktop').read_text()
        self.assertIn('Exec="', desktop)
        self.assertIn('espacios %%/Iniciar_IRRADIUM.sh"', desktop)
        self.assertIn('Terminal=false', desktop)
        self.assertIn('Terminal=true', (self.root / 'Desinstalar_IRRADIUM.desktop').read_text())
        self.assertTrue(os.access(self.root / 'IRRADIUM.desktop', os.X_OK))

    def test_browser_uses_desktop_environment_after_isolated_startup(self):
        shutil.copy2(ROOT / 'irradium', self.root / 'irradium')
        guard = self.root / 'runtime/guard/bwrap'
        guard.parent.mkdir(parents=True)
        guard.write_text('#!/bin/sh\n/usr/bin/env > "$IRRADIUM_ROOT/guard.env"\nexit "${IRRADIUM_TEST_EXIT_CODE:-0}"\n')
        guard.chmod(0o755)
        python = self.root / 'runtime/main/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o755)
        browser = self.base / 'desktop-bin/xdg-open'
        browser.parent.mkdir()
        browser.write_text('#!/bin/sh\n/usr/bin/env > "$IRRADIUM_TEST_BROWSER_LOG"\nprintf "%s\\n" "$@" >> "$IRRADIUM_TEST_BROWSER_LOG"\n')
        browser.chmod(0o755)
        browser_log = self.base / 'browser.env'
        desktop = {
            'XDG_CONFIG_HOME': str(self.base / 'desktop-config'),
            'XDG_DATA_HOME': str(self.base / 'desktop-data'),
            'XDG_CACHE_HOME': str(self.base / 'desktop-cache'),
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/desktop/session-bus',
            'TMPDIR': str(self.base),
        }
        environment = {
            **os.environ, **desktop, 'PATH': str(browser.parent) + ':/usr/bin:/bin',
            'DISPLAY': ':0', 'IRRADIUM_OPEN_BROWSER': '1',
            'IRRADIUM_TEST_BROWSER_LOG': str(browser_log),
        }
        for host_settings in (desktop, {}):
            with self.subTest(desktop_settings=bool(host_settings)):
                env = {key: value for key, value in environment.items() if key not in desktop}
                env.update(host_settings)
                subprocess.run(['/bin/bash', str(self.root / 'irradium'), 'start'], env=env, check=True)
                browser_values = browser_log.read_text().splitlines()
                guard_values = (self.root / 'guard.env').read_text().splitlines()
                self.assertIn(f'XDG_CONFIG_HOME={self.root}/.local/config', guard_values)
                self.assertNotIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/desktop/session-bus', guard_values)
                self.assertIn('http://127.0.0.1:3001/', browser_values)
                for key in desktop:
                    values = [line for line in browser_values if line.startswith(key + '=')]
                    self.assertEqual(values, [f'{key}={host_settings[key]}'] if key in host_settings else [])
        browser_log.unlink()
        environment['IRRADIUM_TEST_EXIT_CODE'] = '7'
        failed = subprocess.run(['/bin/bash', str(self.root / 'irradium'), 'start'], env=environment)
        self.assertEqual(failed.returncode, 7)
        self.assertFalse(browser_log.exists())


    def test_keep_data_preserves_cases_settings_animation_and_unknown_user_files(self):
        (self.root / 'visualizaciones').mkdir()
        (self.root / 'visualizaciones/animation.blend').write_bytes(b'blend')
        (self.root / '.local/validation/pilot').mkdir(parents=True)
        (self.root / '.local/validation/pilot/load.h5m').write_bytes(b'pilot geometry referenced by a study')
        (self.root / '.local/cache').mkdir()
        (self.root / '.local/cache/stale').write_bytes(b'cache')
        (self.root / 'notes.txt').write_text('user notes')
        (self.root / '.git').mkdir()
        (self.root / 'app/external-link').symlink_to(self.outside, target_is_directory=True)
        uninstaller.uninstall(self.root, True)
        self.assertEqual(self.case.read_bytes(), b'original STEP content')
        self.assertEqual((self.root / 'workspace/Configuracion/instalacion.json').read_text(), '{"PHITSPATH":"/outside/phits"}\n')
        self.assertEqual((self.root / 'workspace/Configuracion/preferencias.json').read_text(), '{"theme":"dark"}\n')
        self.assertEqual((self.root / 'workspace/Animaciones/animation.blend').read_bytes(), b'blend')
        self.assertEqual((self.root / '.local/validation/pilot/load.h5m').read_bytes(), b'pilot geometry referenced by a study')
        self.assertTrue((self.root / '.local/validation').is_symlink())
        self.assertTrue((self.root / 'workspace/Pruebas/Validacion_interna/pilot/load.h5m').is_file())
        self.assertFalse((self.root / '.local/cache').exists())
        self.assertTrue((self.root / 'DATOS_CONSERVADOS.txt').exists())
        self.assertEqual((self.root / 'notes.txt').read_text(), 'user notes')
        self.assertTrue((self.root / '.git').exists())
        self.assertFalse((self.root / 'runtime').exists())
        self.assertFalse((self.root / 'app').exists())
        self.assertEqual((self.outside / 'important.h5').read_bytes(), b'untouched')

    def test_remove_data_deletes_only_this_installation_without_following_links(self):
        (self.root / 'workspace/external-link').symlink_to(self.outside, target_is_directory=True)
        uninstaller.uninstall(self.root, False)
        self.assertFalse(self.root.exists())
        self.assertEqual((self.outside / 'important.h5').read_bytes(), b'untouched')

    def test_active_study_blocks_uninstall_before_deleting_anything(self):
        status = self.case.parents[1] / 'monte_carlo/status.json'
        status.parent.mkdir(); status.write_text('{"state":"running"}')
        with self.assertRaisesRegex(ValueError, 'cálculo activo'):
            uninstaller.uninstall(self.root, True)
        self.assertTrue((self.root / 'runtime').exists())
        self.assertTrue(self.case.exists())

    def test_symlinked_workspace_and_wrong_application_are_rejected(self):
        shutil.rmtree(self.root / 'workspace')
        (self.root / 'workspace').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'sin enlaces externos'):
            uninstaller.validate_root(self.root)
        (self.root / 'workspace').unlink()
        (self.root / 'installation-manifest.json').write_text('{"application":"Gamma Sim"}')
        with self.assertRaises(ValueError):
            uninstaller.uninstall(self.root, False)

    def test_confirmation_cancel_and_noninteractive_refusal_do_not_modify_data(self):
        with patch.object(uninstaller, 'ROOT', self.root), patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=['s', 'CANCELAR']):
            self.assertEqual(uninstaller.main([]), 0)
        self.assertTrue((self.root / 'runtime').exists())
        with patch.object(uninstaller, 'ROOT', self.root), patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaises(SystemExit):
                uninstaller.main([])
        self.assertEqual(self.case.read_bytes(), b'original STEP content')

    def test_delete_data_requires_stronger_confirmation(self):
        with patch.object(uninstaller, 'ROOT', self.root), patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=['n', 'DESINSTALAR']):
            self.assertEqual(uninstaller.main([]), 0)
        self.assertTrue(self.case.exists())

    def test_actual_cli_dry_run_and_automated_fixture_uninstall(self):
        script = self.root / 'scripts/uninstall.py'
        before = self.case.read_bytes()
        dry = subprocess.run([sys.executable, '-I', '-B', str(script), '--remove-data', '--dry-run'], check=True, capture_output=True, text=True)
        self.assertIn('workspace', json.loads(dry.stdout)['remove'])
        self.assertEqual(self.case.read_bytes(), before)
        subprocess.run([sys.executable, '-I', '-B', str(script), '--keep-data', '--yes'], check=True, capture_output=True)
        self.assertEqual(self.case.read_bytes(), before)
        self.assertFalse((self.root / 'runtime').exists())

    def test_reinstall_reuses_retained_settings_without_replacing_cases(self):
        uninstaller.uninstall(self.root, True)
        layout.organize_user_data(self.root)
        self.assertEqual(self.case.read_bytes(), b'original STEP content')
        self.assertEqual(json.loads((self.root / 'irradium-config.json').read_text())['PHITSPATH'], '/outside/phits')

    def test_unrecognised_live_process_is_never_signalled(self):
        state = self.root / '.local/state'; state.mkdir()
        record = {'pid': os.getpid(), 'root': str(self.root), **uninstaller.identity(os.getpid())}
        (state / 'api.json').write_text(json.dumps(record))
        with patch.object(uninstaller.os, 'killpg') as kill:
            with self.assertRaises(ValueError):
                uninstaller.uninstall(self.root, False)
            kill.assert_not_called()
        self.assertTrue(self.case.exists())

    def test_own_service_can_be_stopped_without_touching_other_processes(self):
        sleeper = self.root / 'service-fixture.py'
        sleeper.write_text('import time\ntime.sleep(60)\n')
        process = subprocess.Popen([sys.executable, str(sleeper)], start_new_session=True)
        try:
            state = self.root / '.local/state'; state.mkdir()
            record = {'pid': process.pid, 'root': str(self.root), **uninstaller.identity(process.pid)}
            (state / 'api.json').write_text(json.dumps(record))
            uninstaller.stop_services(self.root)
            self.assertEqual(process.wait(timeout=2), -15)
            self.assertEqual(self.case.read_bytes(), b'original STEP content')
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=2)

    def test_menu_registration_is_idempotent_and_uses_only_a_link(self):
        layout.write_launchers(self.root)
        with patch.object(menu, 'refresh'):
            entry = menu.register(self.root, self.base / 'desktop-data')
            self.assertEqual(entry, menu.register(self.root, self.base / 'desktop-data'))
        self.assertTrue(entry.is_symlink())
        self.assertEqual(entry.resolve(), self.root / 'IRRADIUM.desktop')

    def test_menu_registration_never_overwrites_another_application(self):
        layout.write_launchers(self.root)
        directory = self.base / 'desktop-data/applications'; directory.mkdir(parents=True)
        entry = directory / menu.ENTRY; entry.write_text('another application')
        with self.assertRaises(ValueError):
            menu.register(self.root, directory.parent)
        self.assertEqual(entry.read_text(), 'another application')

    def test_uninstall_removes_only_its_own_menu_link(self):
        layout.write_launchers(self.root)
        with patch.object(menu, 'refresh'):
            entry = menu.register(self.root, self.base / 'desktop-data')
        uninstaller.uninstall(self.root, True)
        self.assertFalse(entry.is_symlink())
        self.assertTrue(self.case.is_file())

    def test_unregister_preserves_a_replaced_menu_link(self):
        layout.write_launchers(self.root)
        with patch.object(menu, 'refresh'):
            entry = menu.register(self.root, self.base / 'desktop-data')
        entry.unlink(); entry.symlink_to(self.outside / 'important.h5')
        menu.unregister(self.root)
        self.assertEqual(entry.resolve(), self.outside / 'important.h5')


if __name__ == '__main__':
    unittest.main()
