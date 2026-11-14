import json
import os
import signal
import subprocess
import sys
import time
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .case_store import CaseStore


class DeterministicJobs:
    """Lanza el método actual fuera del proceso HTTP y expone su estado por archivo."""

    ACTIVE_STATES = ("queued", "running", "cancelling")
    WORKER_MODULE = "sim_gamma.workers.deterministic_cli"

    def __init__(self, case_store: CaseStore, project_root: Path):
        self.case_store = case_store
        self.project_root = project_root
        self._children: Dict[int, subprocess.Popen] = {}

    def submit(self, case_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        case_root = self.case_store.root(case_id)
        deterministic_root = case_root / "deterministic"
        status_path = deterministic_root / "status.json"
        request_path = deterministic_root / "request.json"
        lock_path = deterministic_root / "submit.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            self._reap_children()
            if self._matching_processes(request_path, self.WORKER_MODULE):
                raise RuntimeError("La optimización determinista ya está en ejecución")
            if status_path.is_file():
                status = self.status(case_id)
                if status.get("state") in self.ACTIVE_STATES:
                    raise RuntimeError("La optimización determinista ya está en ejecución")

            run_id = uuid4().hex
            submitted_at = datetime.now(timezone.utc).isoformat()
            request = {
                "case_id": case_id,
                "case_root": str(case_root),
                "project_root": str(self.project_root),
                "irradiator_type": self.case_store.irradiator_type,
                "run_id": run_id,
                "parameters": parameters,
            }
            self._write_json(request_path, request)
            self._write_json(
                status_path,
                {
                    "case_id": case_id,
                    "run_id": run_id,
                    "state": "queued",
                    "stage": "waiting-for-worker",
                    "progress": 0,
                    "submitted_at": submitted_at,
                    "monte_carlo_started": False,
                },
            )

            environment = os.environ.copy()
            existing_python_path = environment.get("PYTHONPATH", "")
            source_root = self.project_root / "src"
            environment["PYTHONPATH"] = str(source_root) + (
                os.pathsep + existing_python_path if existing_python_path else ""
            )

            log_path = case_root / "logs" / "deterministic.log"
            with log_path.open("ab") as log_stream:
                process = subprocess.Popen(
                    [sys.executable, "-m", self.WORKER_MODULE, str(request_path)],
                    cwd=str(case_root / "input"),
                    env=environment,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            self._children[process.pid] = process
            self._write_json(
                deterministic_root / "worker.json",
                {
                    "case_id": case_id,
                    "run_id": run_id,
                    "pid": process.pid,
                    "process_group_id": process.pid,
                    "request_path": str(request_path.resolve()),
                    "started_at": submitted_at,
                },
            )
        return self.status(case_id)

    def status(self, case_id: str) -> Dict[str, Any]:
        # Recolecta hijos finalizados aunque no se envíe otro trabajo después.
        self._reap_children()
        deterministic_root = self.case_store.root(case_id) / "deterministic"
        path = deterministic_root / "status.json"
        if not path.is_file():
            return {
                "case_id": case_id,
                "state": "not-started",
                "progress": 0,
                "monte_carlo_started": False,
            }
        status = self._read_json(path)
        if status.get("state") not in self.ACTIVE_STATES:
            return status

        request_path = deterministic_root / "request.json"
        if self._matching_processes(request_path, self.WORKER_MODULE):
            return status

        # A result only completes the exact run that produced it. Results from
        # earlier runs must never unlock a second concurrent submission.
        result_path = deterministic_root / "result.json"
        if result_path.is_file():
            result = self._read_json(result_path)
            if status.get("run_id") and result.get("run_id") == status.get("run_id"):
                completed = {
                    **status,
                    "state": "completed",
                    "stage": "review-trajectory",
                    "progress": 100,
                    "result_file": result_path.name,
                    "reconciled_at": datetime.now(timezone.utc).isoformat(),
                }
                self._write_json(path, completed)
                self._remove_worker_metadata(deterministic_root)
                return completed

        worker = self._worker_metadata(deterministic_root)
        if not worker or not self._worker_is_active(worker):
            was_cancelling = status.get("state") == "cancelling"
            interrupted = {
                **status,
                "state": "cancelled" if was_cancelling else "interrupted",
                "stage": "cancelled-by-user" if was_cancelling else "worker-not-active",
                "error": None if was_cancelling else "El proceso determinista ya no está activo. Puede volver a ejecutarlo.",
                "interrupted_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_json(path, interrupted)
            self._remove_worker_metadata(deterministic_root)
            return interrupted
        return status

    def cancel(self, case_id: str) -> Dict[str, Any]:
        deterministic_root = self.case_store.root(case_id) / "deterministic"
        status_path = deterministic_root / "status.json"
        status = self._read_json(status_path) if status_path.is_file() else {
            "case_id": case_id,
            "state": "not-started",
            "progress": 0,
        }
        request_path = deterministic_root / "request.json"
        processes = self._matching_processes(request_path, self.WORKER_MODULE)
        if status.get("state") not in self.ACTIVE_STATES and not processes:
            return status

        worker = self._worker_metadata(deterministic_root)
        process_groups = {group for _, group in processes}
        process_ids = {pid for pid, _ in processes}
        if worker and self._worker_is_active(worker):
            process_groups.add(int(worker["process_group_id"]))
            process_ids.add(int(worker["pid"]))
        if process_groups:
            status = {**status, "state": "cancelling", "stage": "stopping-worker-group"}
            self._write_json(status_path, status)
        for process_group_id in process_groups:
            try:
                os.killpg(process_group_id, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
            self._process_group_exists(group) for group in process_groups
        ):
            time.sleep(0.05)
        for process_group_id in process_groups:
            if self._process_group_exists(process_group_id):
                try:
                    os.killpg(process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self._reap_processes(process_ids)

        cancelled = {
            **status,
            "state": "cancelled",
            "stage": "cancelled-by-user",
            "error": None,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
            "monte_carlo_started": False,
        }
        self._write_json(status_path, cancelled)
        self._remove_worker_metadata(deterministic_root)
        return cancelled

    def cancel_all(self) -> list:
        cancelled = []
        runs_root = self.case_store.runs_root
        if not runs_root.is_dir():
            return cancelled
        for case_root in runs_root.iterdir():
            if not case_root.is_dir():
                continue
            try:
                status_path = case_root / "deterministic" / "status.json"
                request_path = case_root / "deterministic" / "request.json"
                has_processes = bool(self._matching_processes(request_path, self.WORKER_MODULE))
                if has_processes or (
                    status_path.is_file()
                    and self._read_json(status_path).get("state") in self.ACTIVE_STATES
                ):
                    cancelled.append(self.cancel(case_root.name))
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                continue
        return cancelled

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def _worker_metadata(self, deterministic_root: Path) -> Optional[Dict[str, Any]]:
        path = deterministic_root / "worker.json"
        if not path.is_file():
            return None
        try:
            worker = self._read_json(path)
            pid = int(worker["pid"])
            process_group_id = int(worker["process_group_id"])
            request_path = Path(worker["request_path"]).resolve()
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return None
        expected_request = (deterministic_root / "request.json").resolve()
        if pid <= 1 or process_group_id <= 1 or request_path != expected_request:
            return None
        return worker

    @staticmethod
    def _worker_is_active(worker: Dict[str, Any]) -> bool:
        pid = int(worker["pid"])
        proc_root = Path("/proc") / str(pid)
        try:
            state = (proc_root / "stat").read_text(encoding="utf-8").split()[2]
            cmdline = (proc_root / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, IndexError, OSError):
            return False
        if state == "Z":
            return False
        command = b" ".join(cmdline).decode("utf-8", errors="replace")
        return (
            "sim_gamma.workers.deterministic_cli" in command
            and str(Path(worker["request_path"]).resolve()) in command
        )

    @staticmethod
    def _matching_processes(request_path: Path, worker_module: str) -> list:
        if not request_path.is_file():
            return []
        expected_request = str(request_path.resolve()).encode()
        expected_module = worker_module.encode()
        matches = []
        for process_root in Path("/proc").iterdir():
            if not process_root.name.isdigit():
                continue
            try:
                args = (process_root / "cmdline").read_bytes().split(b"\0")
                stat = (process_root / "stat").read_text(encoding="utf-8")
                fields = stat[stat.rfind(")") + 2 :].split()
                state, group = fields[0], int(fields[2])
            except (FileNotFoundError, PermissionError, IndexError, OSError, ValueError):
                continue
            if state != "Z" and expected_module in args and expected_request in args:
                matches.append((int(process_root.name), group))
        return matches

    def _reap_children(self) -> None:
        for pid, process in list(self._children.items()):
            if process.poll() is not None:
                self._children.pop(pid, None)

    def _reap_processes(self, process_ids) -> None:
        for pid in process_ids:
            process = self._children.pop(pid, None)
            if process is not None:
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
                continue
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass

    @staticmethod
    def _process_group_exists(process_group_id: int) -> bool:
        # killpg(..., 0) also reports zombie leaders. Inspect the group so a
        # completed SIGTERM does not force every cancellation to wait 3 seconds.
        for process_root in Path("/proc").iterdir():
            if not process_root.name.isdigit():
                continue
            try:
                stat = (process_root / "stat").read_text(encoding="utf-8")
                fields = stat[stat.rfind(")") + 2 :].split()
                state, group = fields[0], int(fields[2])
            except (FileNotFoundError, PermissionError, IndexError, OSError, ValueError):
                continue
            if group == process_group_id and state != "Z":
                return True
        return False

    @staticmethod
    def _remove_worker_metadata(deterministic_root: Path) -> None:
        try:
            (deterministic_root / "worker.json").unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
        temporary.replace(path)
