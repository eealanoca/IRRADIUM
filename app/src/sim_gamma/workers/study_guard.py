"""Wall-clock and resident-memory limits for an entire route study."""
import json
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from sim_gamma.domain.route_study import EXTENDED_STUDY_SECONDS, STANDARD_STUDY_SECONDS
from sim_gamma.domain.study_recovery import pause_requested


def available_memory_mb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return 4096


def process_tree_rss_mb(pid: int) -> float:
    total = 0
    pending = [pid]
    while pending:
        current = pending.pop()
        try:
            status = Path(f"/proc/{current}/status").read_text()
            total += next((int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")), 0)
            children = Path(f"/proc/{current}/task/{current}/children").read_text()
            pending.extend(int(value) for value in children.split())
        except (OSError, ValueError):
            continue
    return total / 1024


def stop_group(process: subprocess.Popen, grace: float = 3):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


@contextmanager
def study_slot(application: Path, run_root: Path, request: dict):
    from sim_gamma.workers.monte_carlo_cli import write_json
    lock_path = application / ".local" / "state" / "route-study.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        waiting = False
        while True:
            if pause_requested(run_root, request["run_id"]):
                yield False
                return
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not waiting:
                    write_json(run_root / "status.json", {
                        "case_id": request["case_id"], "run_id": request["run_id"],
                        "state": "queued", "stage": "waiting-for-resources", "progress": 0,
                        "execution_enabled": True,
                    })
                    waiting = True
                time.sleep(.25)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_guarded(request_path: Path) -> int:
    if os.environ.get("IRRADIUM_ROOT"):
        application = Path(__file__).resolve().parents[4]
        request = json.loads(request_path.read_text())
        run_root = Path(request["case_root"]) / "monte_carlo"
        run_root.mkdir(parents=True, exist_ok=True)
        if run_root.resolve().is_relative_to(application):
            with study_slot(application, run_root, request) as acquired:
                if acquired is False:
                    from sim_gamma.workers.monte_carlo_cli import write_json, write_status
                    archive = Path(request["case_root"]) / "results/studies" / f"{request['run_id']}.json"
                    coverage = 0
                    if archive.is_file():
                        result = json.loads(archive.read_text())
                        result["stop_reason"] = "paused"
                        coverage = min(99, len(result.get("positions", [])) * 100 // max(1, result.get("requested_position_count", 1)))
                        write_json(archive, result)
                        write_json(archive.parent.parent / "route_rates.json", result)
                    write_status(run_root / "status.json", request, "paused", coverage, state="paused")
                    (run_root / "worker.json").unlink(missing_ok=True)
                    return 0
                return _run_guarded(request_path)
    return _run_guarded(request_path)


def _run_guarded(request_path: Path) -> int:
    from sim_gamma.workers.monte_carlo_cli import write_json

    request = json.loads(request_path.read_text())
    study = request.get("route_study") or {}
    cap = EXTENDED_STUDY_SECONDS if study.get("extended_time") and not study.get("quick") else STANDARD_STUDY_SECONDS
    total_seconds = min(cap, float(study.get("time_limit_seconds", STANDARD_STUDY_SECONDS)))
    previous_elapsed = float(request.get("previous_elapsed_seconds", 0))
    seconds = max(.01, total_seconds - previous_elapsed)
    memory = min(float(study.get("memory_limit_mb", 4096)), max(512, available_memory_mb() * .75))
    root = Path(request["case_root"])
    run_root = root / "monte_carlo"
    run_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    peak = float(request.get("previous_peak_rss_mb", 0))
    process = subprocess.Popen([sys.executable, "-m", "sim_gamma.workers.monte_carlo_cli", str(request_path)],
                               env={**os.environ, "IRRADIUM_STUDY_CHILD": "1",
                                    "IRRADIUM_STUDY_MEMORY_LIMIT_MB": str(memory),
                                    "IRRADIUM_STUDY_DEADLINE": str(started + seconds)}, start_new_session=True)
    previous = {}

    def cancelled(signum, frame):
        stop_group(process)
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, cancelled)
    reason = None
    try:
        while process.poll() is None:
            current_rss = process_tree_rss_mb(process.pid)
            peak = max(peak, current_rss)
            elapsed = time.monotonic() - started
            free_disk = shutil.disk_usage(root).free / 1024**2
            resource = {"elapsed_seconds": previous_elapsed + elapsed, "time_limit_seconds": total_seconds,
                        "peak_rss_mb": peak, "memory_limit_mb": memory, "free_disk_mb": free_disk}
            write_json(run_root / "resources.json", resource)
            if elapsed >= max(.01, seconds - .25):
                reason = "time-limited"
                break
            if current_rss > memory or available_memory_mb() < 256:
                reason = "memory-limited"
                break
            if free_disk < 256:
                reason = "disk-limited"
                break
            time.sleep(.25)
        if reason:
            stop_group(process, grace=max(.01, min(3, seconds - (time.monotonic() - started) - .1)))
        code = process.wait()
        status_path = run_root / "status.json"
        status = json.loads(status_path.read_text()) if status_path.is_file() else {}
        if status.get("run_id") != request["run_id"]:
            status = {}
        child_state = status.get("final_state") or status.get("state")
        if reason is None and child_state in {"time-limited", "memory-limited", "disk-limited"}:
            reason = child_state
        resource = {"elapsed_seconds": previous_elapsed + time.monotonic() - started, "time_limit_seconds": total_seconds,
                    "peak_rss_mb": peak, "memory_limit_mb": memory,
                    "free_disk_mb": shutil.disk_usage(root).free / 1024**2}
        write_json(run_root / "resources.json", resource)
        archive = root / "results" / "studies" / f"{request['run_id']}.json"
        if archive.is_file():
            result = json.loads(archive.read_text())
            result["resources"] = resource
            result["stop_reason"] = reason or ("failed" if code else result.get("stop_reason"))
            write_json(archive, result)
            write_json(root / "results" / "route_rates.json", result)
        terminal = reason or ("failed" if code else child_state if child_state in {"completed", "failed", "paused"} else "completed")
        error = status.get("error")
        if reason and not error:
            error = {"time-limited": "Se alcanzó el tiempo máximo elegido.",
                     "memory-limited": "Se alcanzó el presupuesto de RAM. Revisa la fase indicada en el diagnóstico: la geometría CAD, la malla de dosis y el paralelismo tienen consumos diferentes. Ajusta el límite de memoria o la configuración correspondiente antes de continuar.",
                     "disk-limited": "Quedan menos de 256 MiB libres en el disco. Se conservaron las posiciones publicadas."}[reason]
        status.pop("final_state", None)
        (run_root / "worker.json").unlink(missing_ok=True)
        (run_root / "pause.json").unlink(missing_ok=True)
        write_json(status_path, {
            **status, "case_id": request["case_id"], "run_id": request["run_id"],
            "state": terminal, "stage": terminal,
            "progress": status.get("progress", 0) if terminal == "paused" else 100, "execution_enabled": True,
            "error": error, "guard_finalized": True,
        })
        return 0 if reason else code
    finally:
        stop_group(process)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
