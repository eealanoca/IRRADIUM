import json
import fcntl
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from sim_gamma.domain.study_recovery import write_checkpoint_json

from .case_store import CaseStore
from .deterministic_jobs import DeterministicJobs


class MonteCarloJobs:
    """Lanza y supervisa el backend Monte Carlo como grupo cancelable."""

    ACTIVE_STATES = (*DeterministicJobs.ACTIVE_STATES, "pausing")
    WORKER_MODULE = "sim_gamma.workers.monte_carlo_cli"
    SYSTEMD_UNIT_PREFIX = "irradium-mc-"

    def __init__(
        self,
        case_store: CaseStore,
        project_root: Path = None,
        execution_enabled: bool = False,
    ):
        self.case_store = case_store
        self.project_root = project_root or Path.cwd()
        self.execution_enabled = execution_enabled
        self._children: Dict[int, subprocess.Popen] = {}

    def submit(self, case_id: str, *, route_study: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.execution_enabled:
            raise RuntimeError("La ejecución Monte Carlo está deshabilitada")
        case_root = self.case_store.root(case_id)
        deterministic_result = case_root / "deterministic" / "result.json"
        route_path = case_root / "input" / "route.json"
        if route_path.is_file():
            from sim_gamma.domain.route_dose import source_endpoints

            if not (case_root / "input" / "load.step").is_file():
                raise ValueError("Selecciona el STEP de la carga antes de calcular las tasas")
            inventory = DeterministicJobs._read_json(case_root / "input" / "sources.json")
            if not inventory.get("sources"):
                raise ValueError("El caso no contiene fuentes configuradas")
            for source in inventory["sources"]:
                source_endpoints(source)
        elif not deterministic_result.is_file():
            raise ValueError("Primero debe completarse la optimización determinista")

        root = case_root / "monte_carlo"
        root.mkdir(parents=True, exist_ok=True)
        (root / "work").mkdir(exist_ok=True)
        status_path = root / "status.json"
        request_path = root / "request.json"
        lock_path = root / "submit.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            self._reap_children()
            if route_path.is_file() and status_path.is_file() and DeterministicJobs._read_json(status_path).get("guard_finalized"):
                # The results are saved; allow the small wrapper-exit interval
                # to finish before reusing this case's request/status files.
                import time
                deadline = time.monotonic() + 2
                while DeterministicJobs._matching_processes(request_path, self.WORKER_MODULE) and time.monotonic() < deadline:
                    time.sleep(.05)
                self._reap_children()
            if DeterministicJobs._matching_processes(request_path, self.WORKER_MODULE):
                raise RuntimeError("La simulación Monte Carlo ya está en ejecución")
            if status_path.is_file() and self.status(case_id).get("state") in self.ACTIVE_STATES:
                raise RuntimeError("La simulación Monte Carlo ya está en ejecución")

            # Una nueva ejecución no debe exponer artefactos ni statepoints de
            # la malla anterior mientras OpenMC calcula la configuración vigente.
            for filename in (
                "result.json",
                "visualization.json",
                "dose_distribution.vti",
                "dose_distribution.vtk",
                "isodose_xy.png",
                "isodose_yz.png",
                "weight_windows.h5",
            ):
                (root / filename).unlink(missing_ok=True)
            for stale_statepoint in (root / "work").glob("statepoint.*.h5"):
                stale_statepoint.unlink(missing_ok=True)
            (root / "work" / "weight_windows.h5").unlink(missing_ok=True)

            run_id = uuid4().hex
            (root / "resources.json").unlink(missing_ok=True)
            submitted_at = datetime.now(timezone.utc).isoformat()
            request = {
                "case_id": case_id,
                "case_root": str(case_root.resolve()),
                "project_root": str(self.project_root.resolve()),
                "irradiator_type": self.case_store.irradiator_type,
                "run_id": run_id,
            }
            if route_study is not None:
                from sim_gamma.domain.study_recovery import input_fingerprints
                request["route_study"] = route_study
                request["input_fingerprints"] = input_fingerprints(case_root)
            (root / "pause.json").unlink(missing_ok=True)
            systemd_unit = None
            if self._systemd_user_available():
                systemd_unit = self._systemd_unit_name(run_id)
                request["systemd_unit"] = systemd_unit
            write_checkpoint_json(request_path, request, through=case_root.parent)
            write_checkpoint_json(
                status_path,
                {
                    "case_id": case_id,
                    "run_id": run_id,
                    "state": "queued",
                    "stage": "waiting-for-monte-carlo-worker",
                    "progress": 0,
                    "submitted_at": submitted_at,
                    "execution_enabled": True,
                    **({"route_study": route_study} if route_study is not None else {}),
                },
            )

            self._launch_request(case_id, request)
        return self.status(case_id)

    def _launch_request(self, case_id: str, request: dict) -> None:
        root = self.case_store.root(case_id) / "monte_carlo"
        request_path = root / "request.json"
        submitted_at = datetime.now(timezone.utc).isoformat()
        run_id = request["run_id"]
        case_root = self.case_store.root(case_id)
        systemd_unit = request.get("systemd_unit")
        environment = os.environ.copy()
        existing_python_path = environment.get("PYTHONPATH", "")
        source_root = self.project_root / "src"
        environment["PYTHONPATH"] = os.pathsep.join((str(source_root), str(self.project_root))) + (
            os.pathsep + existing_python_path if existing_python_path else ""
        )
        log_path = case_root / "logs" / "monte_carlo.log"
        process = None
        process_id = 0
        if systemd_unit:
            try:
                process_id = self._launch_systemd_worker(
                    systemd_unit,
                    request_path,
                    root / "work",
                    log_path,
                    environment,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                # La aplicación también debe seguir siendo portable en equipos
                # sin una sesión systemd de usuario utilizable.
                systemd_unit = None
                request.pop("systemd_unit", None)
                write_checkpoint_json(request_path, request)
        if not systemd_unit:
            with log_path.open("ab") as log_stream:
                process = subprocess.Popen(
                    [sys.executable, "-m", self.WORKER_MODULE, str(request_path)],
                    cwd=str(root / "work"),
                    env=environment,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            process_id = process.pid
            self._children[process.pid] = process
        DeterministicJobs._write_json(
            root / "worker.json",
            {
                "case_id": case_id,
                "run_id": run_id,
                "pid": process_id,
                "process_group_id": process_id,
                "request_path": str(request_path.resolve()),
                "started_at": submitted_at,
                **({"systemd_unit": systemd_unit} if systemd_unit else {}),
            },
        )

    @classmethod
    def _systemd_unit_name(cls, run_id: str) -> str:
        safe_run_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(run_id)).strip("-")
        if not safe_run_id:
            raise ValueError("El identificador de ejecución no permite crear un servicio")
        return (cls.SYSTEMD_UNIT_PREFIX + safe_run_id[:40]).lower()

    @staticmethod
    def _systemd_user_available() -> bool:
        if os.environ.get("SIM_GAMMA_DISABLE_SYSTEMD") == "1":
            return False
        if not shutil.which("systemd-run") or not shutil.which("systemctl"):
            return False
        try:
            check = subprocess.run(
                ["systemctl", "--user", "is-system-running"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return check.returncode in (0, 1)

    @classmethod
    def _validated_systemd_unit(cls, value: Any) -> str | None:
        unit = str(value or "").removesuffix(".service")
        if not unit.startswith(cls.SYSTEMD_UNIT_PREFIX):
            return None
        if not re.fullmatch(r"[a-z0-9_-]+", unit):
            return None
        return unit

    @classmethod
    def _systemd_unit_active(cls, unit: Any) -> bool:
        validated = cls._validated_systemd_unit(unit)
        if not validated:
            return False
        try:
            check = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", validated + ".service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return check.returncode == 0

    @classmethod
    def _systemd_unit_from_files(cls, root: Path) -> str | None:
        for path in (root / "worker.json", root / "request.json"):
            if not path.is_file():
                continue
            try:
                unit = cls._validated_systemd_unit(
                    DeterministicJobs._read_json(path).get("systemd_unit")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if unit:
                return unit
        return None

    def _launch_systemd_worker(
        self,
        unit: str,
        request_path: Path,
        work_root: Path,
        log_path: Path,
        environment: Dict[str, str],
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "systemd-run",
            "--user",
            "--collect",
            "--quiet",
            "--unit",
            unit,
            "--description",
            "IRRADIUM Monte Carlo",
            "--working-directory",
            str(work_root.resolve()),
            "--property",
            "KillMode=mixed",
            "--property",
            "TimeoutStopSec=10s",
            "--property",
            "StandardOutput=append:" + str(log_path.resolve()),
            "--property",
            "StandardError=append:" + str(log_path.resolve()),
        ]
        for name in ("PYTHONPATH", "PATH", "CONDA_PREFIX", "LD_LIBRARY_PATH", "PHITSPATH"):
            value = environment.get(name)
            if value:
                command.extend(("--setenv", f"{name}={value}"))
        command.extend(
            (sys.executable, "-m", self.WORKER_MODULE, str(request_path.resolve()))
        )
        launched = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if launched.returncode != 0:
            reason = launched.stderr.strip() or launched.stdout.strip() or "error desconocido"
            raise RuntimeError("No se pudo crear el servicio persistente: " + reason)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            show = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    "--property=MainPID",
                    "--value",
                    unit + ".service",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if show.returncode == 0 and show.stdout.strip().isdigit():
                process_id = int(show.stdout.strip())
                if process_id > 1:
                    return process_id
            time.sleep(0.05)
        raise RuntimeError("El servicio persistente no publicó un PID principal")

    def _reap_children(self) -> None:
        for pid, process in list(self._children.items()):
            if process.poll() is not None:
                self._children.pop(pid, None)

    def pause(self, case_id: str) -> Dict[str, Any]:
        from sim_gamma.domain.study_recovery import pause_requested
        root = self.case_store.root(case_id) / "monte_carlo"
        with (root / "submit.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            status = self.status(case_id)
            request = DeterministicJobs._read_json(root / "request.json")
            if not request.get("route_study") or status.get("state") not in {"running", "queued", "pausing"}:
                raise ValueError("Solo se puede pausar un estudio de posiciones activo.")
            if not pause_requested(root, request["run_id"]):
                DeterministicJobs._write_json(root / "pause.json", {"run_id": request["run_id"]})
        return self.status(case_id)

    def resume(self, case_id: str, run_id: str) -> Dict[str, Any]:
        from sim_gamma.domain.study_recovery import RECOVERABLE_STATES, input_fingerprints, recover_study
        root = self.case_store.root(case_id) / "monte_carlo"
        with (root / "submit.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            status = self.status(case_id)
            if status.get("state") not in RECOVERABLE_STATES or not status.get("can_resume"):
                raise ValueError("Este estudio no tiene una continuación pendiente o ya está ejecutándose.")
            request = DeterministicJobs._read_json(root / "request.json")
            if request.get("run_id") != run_id or status.get("run_id") != run_id:
                raise ValueError("El estudio actual cambió. Actualiza la ventana antes de continuar.")
            if Path(request["case_root"]).resolve() != root.parent.resolve() or Path(request["project_root"]).resolve() != self.project_root.resolve():
                raise ValueError("La ejecución no pertenece a esta instalación de IRRADIUM.")
            if DeterministicJobs._matching_processes(root / "request.json", self.WORKER_MODULE) or self._systemd_unit_active(self._systemd_unit_from_files(root)):
                raise RuntimeError("La ejecución anterior todavía no terminó de guardar. Espera unos segundos.")
            result = recover_study(root.parent, request)
            if shutil.disk_usage(root).free < 512 * 1024**2:
                raise ValueError("Libera al menos 512 MiB antes de continuar el estudio.")
            resource_path = root / "resources.json"
            resource = DeterministicJobs._read_json(resource_path) if resource_path.is_file() else {}
            elapsed = resource.get("elapsed_seconds", result.get("wall_seconds", 0))
            if elapsed >= request["route_study"]["time_limit_seconds"] - 1:
                raise ValueError("Se agotó el presupuesto de tiempo original. La continuación no lo amplía automáticamente.")
            original_request = dict(request)
            request.update(resume=True, continuation=request.get("continuation", 0) + 1,
                           previous_elapsed_seconds=elapsed, previous_peak_rss_mb=resource.get("peak_rss_mb", 0),
                           input_fingerprints=input_fingerprints(root.parent))
            if self._systemd_user_available():
                request["systemd_unit"] = self._systemd_unit_name(f"{run_id}-c{request['continuation']}")
            else:
                request.pop("systemd_unit", None)
            (root / "pause.json").unlink(missing_ok=True)
            write_checkpoint_json(root / "request.json", request)
            count = len(result.get("positions", []))
            queued = {**status, "state": "queued", "stage": "resuming-study", "error": None,
                      "progress": min(99, count * 100 // max(1, result.get("requested_position_count", 1))),
                      "can_resume": False, "resumed_at": datetime.now(timezone.utc).isoformat()}
            queued.pop("guard_finalized", None)
            write_checkpoint_json(root / "status.json", queued)
            try:
                self._launch_request(case_id, request)
            except Exception:
                write_checkpoint_json(root / "request.json", original_request)
                write_checkpoint_json(root / "status.json", status)
                raise
        return self.status(case_id)

    def status(self, case_id: str) -> Dict[str, Any]:
        # ``Popen.poll()`` también ejecuta ``waitpid(..., WNOHANG)`` y evita
        # dejar procesos terminados como zombis entre consultas de estado.
        self._reap_children()
        root = self.case_store.root(case_id) / "monte_carlo"
        status_path = root / "status.json"
        request_path = root / "request.json"
        request = DeterministicJobs._read_json(request_path) if request_path.is_file() else {}
        try:
            status = DeterministicJobs._read_json(status_path)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            status = {}
        if request.get("route_study") and request.get("run_id") != status.get("run_id"):
            status = {
                "case_id": case_id, "run_id": request["run_id"], "state": "queued",
                "stage": "recovering-study-status", "progress": 0,
                "execution_enabled": self.execution_enabled,
            }
        if not status:
            return {
                "case_id": case_id,
                "state": "not-started",
                "progress": 0,
                "execution_enabled": self.execution_enabled,
            }
        if request.get('run_id') == status.get('run_id') and request.get('route_study'):
            status['route_study'] = request['route_study']
        if status.get("state") not in self.ACTIVE_STATES:
            from sim_gamma.domain.study_recovery import RECOVERABLE_STATES
            archive = root.parent / "results/studies" / f"{request.get('run_id', '')}.json"
            result = DeterministicJobs._read_json(archive) if archive.is_file() else {}
            study = request.get("route_study") or {}
            incomplete = not result.get("complete") or bool(result.get("accumulated_error_target") and not (result.get("interval_precision") or {}).get("meets_target"))
            if result.get("positions") and not study.get("adaptive_statistics"):
                incomplete |= any(point.get("simulated_particles", 0) < result.get("requested_particles_per_position", 0) for point in result["positions"])
            status["can_resume"] = bool(self.execution_enabled and study and incomplete and request.get("run_id") == status.get("run_id") and status.get("state") in RECOVERABLE_STATES)
            saved_count = len(result.get("positions", []))
            status["saved_position_count"] = saved_count
            if status.get("state") == "interrupted" and result:
                status["progress"] = min(99, saved_count * 100 // max(1, result.get("requested_position_count", 1)))
            return status
        if DeterministicJobs._matching_processes(
            root / "request.json", self.WORKER_MODULE
        ):
            from sim_gamma.domain.study_recovery import pause_requested
            if pause_requested(root, status.get("run_id")) and status.get("state") != "cancelling":
                return {**status, "state": "pausing", "stage": "pausing-after-position"}
            return status
        if self._systemd_unit_active(self._systemd_unit_from_files(root)):
            return status
        final_state = "cancelled" if status.get("state") == "cancelling" else "interrupted"
        updated = {
            **status,
            "state": final_state,
            "stage": "cancelled-by-user" if final_state == "cancelled" else "worker-not-active",
            "error": None if final_state == "cancelled" else "El proceso Monte Carlo ya no está activo.",
            "interrupted_at": datetime.now(timezone.utc).isoformat(),
        }
        write_checkpoint_json(status_path, updated)
        DeterministicJobs._remove_worker_metadata(root)
        return self.status(case_id)

    def cancel(self, case_id: str) -> Dict[str, Any]:
        root = self.case_store.root(case_id) / "monte_carlo"
        status_path = root / "status.json"
        status = DeterministicJobs._read_json(status_path) if status_path.is_file() else {
            "case_id": case_id,
            "state": "not-started",
            "progress": 0,
            "execution_enabled": self.execution_enabled,
        }
        processes = DeterministicJobs._matching_processes(
            root / "request.json", self.WORKER_MODULE
        )
        systemd_unit = self._systemd_unit_from_files(root)
        systemd_active = self._systemd_unit_active(systemd_unit)
        if (
            status.get("state") not in self.ACTIVE_STATES
            and not processes
            and not systemd_active
        ):
            return status

        process_ids = {pid for pid, _ in processes}
        process_groups = {group for _, group in processes}
        worker_path = root / "worker.json"
        if worker_path.is_file():
            try:
                worker = DeterministicJobs._read_json(worker_path)
                process_ids.add(int(worker["pid"]))
                process_groups.add(int(worker["process_group_id"]))
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                pass

        if process_groups or systemd_unit:
            DeterministicJobs._write_json(
                status_path,
                {**status, "state": "cancelling", "stage": "stopping-monte-carlo-group"},
            )
        if systemd_unit:
            subprocess.run(
                ["systemctl", "--user", "stop", systemd_unit + ".service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        for group in process_groups:
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(
            DeterministicJobs._process_group_exists(group) for group in process_groups
        ):
            time.sleep(0.05)
        for group in process_groups:
            if DeterministicJobs._process_group_exists(group):
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for pid in process_ids:
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass

        cancelled = {
            **status,
            "state": "cancelled",
            "stage": "cancelled-by-user",
            "error": None,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        }
        DeterministicJobs._write_json(status_path, cancelled)
        (root / "pause.json").unlink(missing_ok=True)
        DeterministicJobs._remove_worker_metadata(root)
        return cancelled

    def cancel_all(self) -> list:
        cancelled = []
        runs_root = self.case_store.runs_root
        if not runs_root.is_dir():
            return cancelled
        for case_root in runs_root.iterdir():
            if not case_root.is_dir():
                continue
            root = case_root / "monte_carlo"
            status_path = root / "status.json"
            has_processes = bool(
                DeterministicJobs._matching_processes(
                    root / "request.json", self.WORKER_MODULE
                )
            )
            try:
                is_active = (
                    status_path.is_file()
                    and DeterministicJobs._read_json(status_path).get("state")
                    in self.ACTIVE_STATES
                )
                if has_processes or is_active:
                    cancelled.append(self.cancel(case_root.name))
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                continue
        return cancelled
