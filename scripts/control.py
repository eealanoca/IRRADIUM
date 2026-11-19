"""IRRADIUM lifecycle. All mutable application state stays below ROOT."""
from pathlib import Path
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from sim_gamma.branding import NAME, FULL_TITLE, SUBTITLE, VERSION

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
STATE = ROOT / ".local/state"
WORKSPACE = ROOT / "workspace"
API_PORT, WEB_PORT = 8001, 3001
sys.path.insert(0, str(ROOT / "scripts"))
from installation_layout import organize_user_data, write_launchers


def runtime_environment(name="main"):
    prefix = ROOT / "runtime" / name
    env = os.environ.copy()
    env.update({
        "PATH": str(prefix / "bin") + ":/usr/local/bin:/usr/bin:/bin",
        "CONDA_PREFIX": str(prefix),
        "PYTHONPATH": str(APP / "src") + os.pathsep + str(APP),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUSERBASE": str(ROOT / "runtime/user"),
        "LD_LIBRARY_PATH": str(prefix / "lib") + os.pathsep + str(ROOT / "runtime/guard/lib"),
        "IRRADIUM_ROOT": str(ROOT), "IRRADIUM_WORKSPACE_ROOT": str(WORKSPACE),
        "SIM_GAMMA_CASES_DIRECTORY": "Casos", "SIM_GAMMA_DISABLE_SYSTEMD": "1",
        "SIM_GAMMA_WEB_PORT": str(WEB_PORT), "SIM_GAMMA_API_PORT": str(API_PORT),
        "IRRADIUM_CAD_PYTHON": str(ROOT / "runtime/cad/bin/python"),
        "NEXT_PUBLIC_IRRADIUM_API_URL": f"http://127.0.0.1:{API_PORT}",
        "NEXT_PUBLIC_SIM_GAMMA_API_URL": f"http://127.0.0.1:{API_PORT}",
        "PIP_CACHE_DIR": str(ROOT / ".local/cache/pip"),
        "PIP_CONFIG_FILE": "/dev/null", "PIP_USER": "false",
        "npm_config_cache": str(ROOT / ".local/cache/npm"),
        "npm_config_userconfig": str(ROOT / ".local/config/npmrc"),
        "npm_config_globalconfig": str(ROOT / ".local/config/npm-globalrc"),
        "npm_config_prefix": str(ROOT / "runtime/main"),
        "MPLCONFIGDIR": str(ROOT / ".local/cache/matplotlib"),
        "TMPDIR": str(ROOT / ".local/tmp"),
    })
    config_path = ROOT / "workspace/Configuracion/instalacion.json"
    if not config_path.is_file():
        config_path = ROOT / "irradium-config.json"  # Previous installations.
    if config_path.is_file():
        config = json.loads(config_path.read_text())
        for name in ("OPENMC_CROSS_SECTIONS", "PHITSPATH"):
            if config.get(name):
                env[name] = config[name]
    bundled_cross_sections = ROOT / "nuclear-data/cross_sections.xml"
    if bundled_cross_sections.is_file():
        env["OPENMC_CROSS_SECTIONS"] = str(bundled_cross_sections)
    return env


def run(command, runtime="main", **kwargs):
    return subprocess.run(command, cwd=APP, env=runtime_environment(runtime), check=True, **kwargs)


def ensure_workspace():
    organize_user_data(ROOT)
    for directory in [STATE, ROOT / ".local/tmp", WORKSPACE / "Registros"]:
        directory.mkdir(parents=True, exist_ok=True)
    for source, target in [(APP / "examples/irradiation_loads", WORKSPACE / "Ejemplos/Cargas_de_irradiacion"),
                           (APP / "docs", WORKSPACE / "Documentacion")]:
        if source.is_dir() and not target.exists():
            shutil.copytree(source, target)
    from sim_gamma.domain.irradiators import catalog
    for item in catalog():
        category_root = WORKSPACE / "irradiators" / item["id"]
        for directory in ["Casos", "Salidas/Trayectorias", "Salidas/Reportes", "Temporales"]:
            (category_root / directory).mkdir(parents=True, exist_ok=True)
        (category_root / "category.json").write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n")
        if item["enabled"]:
            config = category_root / "Configuracion"
            config.mkdir(exist_ok=True)
            for source in (APP / "data/config").glob("*.json"):
                if not (config / source.name).exists():
                    shutil.copy2(source, config / source.name)
    write_launchers(ROOT)
    manifest = {"application": NAME, "subtitle": SUBTITLE, "full_title": FULL_TITLE, "version": VERSION, "root": str(ROOT),
                "main_runtime": str(ROOT / "runtime/main"), "cad_runtime": str(ROOT / "runtime/cad"),
                "package_manager": str(ROOT / "runtime/bin/micromamba"),
                "external_conda_required": False, "workspace": str(WORKSPACE),
                "layout_version": 2, "user_data": str(WORKSPACE),
                "installation_config": str(WORKSPACE / "Configuracion/instalacion.json"),
                "preferences": str(WORKSPACE / "Configuracion/preferencias.json"),
                "launcher": str(ROOT / "Iniciar_IRRADIUM.sh"),
                "uninstaller": str(ROOT / "Desinstalar_IRRADIUM.sh"),
                "enabled_irradiators": [item["id"] for item in catalog() if item["enabled"]],
                "ports": {"web": WEB_PORT, "api": API_PORT}}
    (ROOT / "installation-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def process_identity(pid):
    proc = Path("/proc") / str(pid)
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] == "Z" or proc.stat().st_uid != os.getuid():
        return None
    return {"start_ticks": int(fields[19]), "pgid": int(fields[2])}


def process_info(service):
    path = STATE / f"{service}.json"
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text())
        if info["root"] != str(ROOT):
            return None
        actual = process_identity(int(info["pid"]))
        if actual is None or actual != {"start_ticks": info["start_ticks"], "pgid": info["pgid"]}:
            return None
        return info
    except (OSError, ValueError, KeyError):
        return None


def stop():
    for service in ("web", "api"):
        info = process_info(service)
        if info:
            try:
                os.killpg(info["pgid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 4
            while process_info(service) and time.monotonic() < deadline:
                time.sleep(.1)
            if process_info(service):
                os.killpg(info["pgid"], signal.SIGKILL)
        (STATE / f"{service}.json").unlink(missing_ok=True)
    print("IRRADIUM está detenido.")


def health():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/api/health", timeout=2) as response:
            data = json.load(response)
            if data.get("environment") != "IRRADIUM":
                return False
        with urllib.request.urlopen(f"http://127.0.0.1:{WEB_PORT}/", timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def start():
    ensure_workspace()
    if process_info("api") and process_info("web") and health():
        print(f"IRRADIUM está disponible en http://127.0.0.1:{WEB_PORT}/")
        return
    if process_info("api") or process_info("web"):
        stop()
    for port in (API_PORT, WEB_PORT):
        with socket.socket() as connection_probe:
            connection_probe.settimeout(.3)
            if connection_probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"El puerto {port} está ocupado; no se detuvo el programa que lo utiliza")
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(f"El puerto {port} está ocupado; no se detuvo el programa que lo utiliza")
    commands = {
        "api": ([str(ROOT / "runtime/main/bin/python"), "-m", "uvicorn", "apps.api.sim_gamma_api.main:app", "--host", "127.0.0.1", "--port", str(API_PORT),
                 *([] if os.environ.get("IRRADIUM_HTTP_ACCESS_LOG") == "1" else ["--no-access-log"])], APP),
        "web": ([str(ROOT / "runtime/main/bin/node"), str(APP / "apps/web/node_modules/.bin/vinext"), "start", "--hostname", "127.0.0.1", "--port", str(WEB_PORT)], APP / "apps/web"),
    }
    launched = []
    try:
        for service, (command, cwd) in commands.items():
            with (WORKSPACE / "Registros" / f"{service}.log").open("ab") as log:
                process = subprocess.Popen(command, cwd=cwd, env=runtime_environment(), stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            launched.append(process)
            identity = process_identity(process.pid)
            if identity is None:
                raise RuntimeError("No se pudo identificar el proceso de " + service)
            (STATE / f"{service}.json").write_text(json.dumps({"pid": process.pid, "root": str(ROOT), **identity}))
        for _ in range(45):
            if any(process.poll() is not None for process in launched):
                raise RuntimeError("Un servicio de IRRADIUM terminó al iniciar; revisa workspace/Registros")
            if process_info("api") and process_info("web") and health():
                print(f"IRRADIUM está disponible en http://127.0.0.1:{WEB_PORT}/")
                return
            time.sleep(1)
        raise RuntimeError("Revisa workspace/Registros/api.log y web.log: IRRADIUM no respondió")
    except Exception:
        stop()
        for process in launched:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
        raise


def install():
    ensure_workspace()
    run([sys.executable, str(ROOT / 'scripts/install_nuclear_data.py')])
    manager = ROOT / "runtime/bin/micromamba"
    cad = ROOT / "runtime/cad"
    if not (cad / "bin/python").exists():
        run([str(manager), "--no-rc", "-r", str(ROOT / "runtime/mamba"), "create", "-y", "-p", str(cad), "-f", str(APP / "environment-cad-converter.yml")])
    run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(APP)])
    if not (APP / "apps/web/node_modules").exists():
        run([str(ROOT / "runtime/main/bin/npm"), "--prefix", "apps/web", "ci"])
    run([str(ROOT / "runtime/main/bin/npm"), "--prefix", "apps/web", "run", "build"])
    # Seed a teaching case only in a new installation. Never modify existing cases.
    if not list((WORKSPACE / 'irradiators/IV/Casos').glob('*/manifest.json')):
        from sim_gamma.application.services.training_example import create_training_case
        bundle = APP / 'examples/commercial_svst'
        config = WORKSPACE / 'irradiators/IV/Configuracion'
        if not (config / 'route_parameters.json').exists():
            for name in ('sources.json','materials.json','route_parameters.json'):
                shutil.copy2(bundle / 'config' / name, config / name)
        create_training_case(WORKSPACE / 'irradiators/IV', bundle, 'medical_kit')
    print("IRRADIUM instalado. Dependencias y archivos dentro de:", ROOT)


def doctor():
    ensure_workspace()
    run([sys.executable, "-c", "import sys,openmc,fastapi,vtk; print('[OK] Runtime principal:',sys.prefix)"])
    run([str(ROOT / "runtime/cad/bin/python"), "-c", "import sys,CAD_to_OpenMC,pymoab; print('[OK] Runtime CAD:',sys.prefix)"], runtime="cad")
    run([str(ROOT / "runtime/main/bin/node"), "--version"])
    env = runtime_environment()
    cross = env.get("OPENMC_CROSS_SECTIONS", "")
    print("[OK] Datos nucleares externos" if cross and Path(cross).is_file() else "[AVISO] Configura OPENMC_CROSS_SECTIONS en workspace/Configuracion/instalacion.json")
    run([sys.executable, "-c", "from phits_extension.availability import detect_phits_availability as d; a=d(); print('[OK] PHITS externo' if a.available else '[OPCIONAL] PHITS no disponible')"])
    print("[OK] Sin Conda, Python ni Node externos; archivos externos de solo lectura")


def main():
    command, *arguments = sys.argv[1:] or ["start"]
    if command == "start": start()
    elif command == "organize": ensure_workspace(); print("Estructura y lanzadores de IRRADIUM preparados.")
    elif command == "stop": stop()
    elif command == "close-ui":
        info = process_info("api")
        if info and arguments == [str(info["pid"])]:
            stop()
    elif command == "restart": stop(); start()
    elif command == "status":
        for service in ("api", "web"):
            print(f"{service}: {'activo' if process_info(service) else 'detenido'}")
    elif command in {"install", "repair", "update"}: install()
    elif command == "doctor": doctor()
    elif command in {"version", "--version"}: print("IRRADIUM " + VERSION)
    elif command in {"exec", "exec-cad"}:
        if not arguments: raise ValueError("Falta el comando")
        runtime = "cad" if command == "exec-cad" else "main"
        env = runtime_environment(runtime)
        executable = shutil.which(arguments[0], path=env["PATH"])
        if not executable: raise ValueError("No se encontró: " + arguments[0])
        os.execve(executable, [executable, *arguments[1:]], env)
    elif command == "mamba":
        if len(arguments) < 2 or arguments[0] not in {"main", "cad"}: raise ValueError("Uso: irradium mamba main|cad SUBCOMANDO ...")
        runtime, subcommand, *options = arguments
        run([str(ROOT / "runtime/bin/micromamba"), "--no-rc", "-r", str(ROOT / "runtime/mamba"), subcommand, "-p", str(ROOT / "runtime" / runtime), *options])
    else:
        print("IRRADIUM: start | stop | restart | status | doctor | install | organize | menu-install | uninstall | version | exec COMANDO | exec-cad COMANDO | mamba main|cad SUBCOMANDO")
        if command not in {"help", "-h", "--help"}: return 2
    return 0


if __name__ == "__main__":
    os.environ.update(runtime_environment())
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print("IRRADIUM:", error, file=sys.stderr)
        raise SystemExit(1)
