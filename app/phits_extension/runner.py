from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PhitsRuntime:
    executable: Path
    command_prefix: tuple[str, ...]
    parallel_mode: str
    workers: int


def _runtime_library_environment(environment=None) -> dict[str, str]:
    env = dict(os.environ if environment is None else environment)
    conda_prefix = env.get("CONDA_PREFIX")
    if conda_prefix:
        conda_lib = str(Path(conda_prefix) / "lib")
        conda_bin = str(Path(conda_prefix) / "bin")
        env["LD_LIBRARY_PATH"] = conda_lib + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        env["PATH"] = conda_bin + os.pathsep + env.get("PATH", "")
    return env


def _glibc_incompatibility(executable: Path) -> str | None:
    completed = subprocess.run(
        ["ldd", str(executable)],
        env=_runtime_library_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    missing = sorted(set(re.findall(r"GLIBC_[0-9.]+[^\n]*not found", completed.stdout)))
    missing.extend(sorted(set(re.findall(r"^\s*[^\s]+\s*=>\s*not found\s*$", completed.stdout, re.MULTILINE))))
    if missing:
        return "; ".join(missing)
    return None


def _local_mpi_socket_available() -> bool:
    """MPI/Hydra necesita un socket local; algunos sandboxes lo bloquean."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def _available_cpu_slots() -> int:
    """Count CPU slots visible to this process, including scheduler affinity."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return int(os.cpu_count() or 1)


def _available_physical_cores() -> int:
    """Count distinct physical cores in the current Linux CPU affinity."""
    cpus = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else range(_available_cpu_slots())
    cores = set()
    for cpu in cpus:
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        try:
            package = (topology / "physical_package_id").read_text().strip()
            core = (topology / "core_id").read_text().strip()
        except OSError:
            return _available_cpu_slots()
        cores.add((package, core))
    return len(cores)


def resolve_runtime(phits_root: Path, cpu_workers: int, prefer_openmp: bool = False) -> PhitsRuntime:
    """Elige MPI y usa todos los CPU; OMP es el respaldo con memoria compartida."""

    mpi = next(
        (path for path in (phits_root / "bin" / "phits334_lin_mpi.exe", phits_root / "bin" / "phits_mpi_lin") if path.is_file()),
        phits_root / "bin" / "phits334_lin_mpi.exe",
    )
    wrapper = phits_root / "bin" / "phits.sh"
    if not wrapper.is_file():
        raise FileNotFoundError(f"No se encontró el lanzador oficial de PHITS: {wrapper}")
    launch_path = _runtime_library_environment().get("PATH")
    mpiexec = shutil.which("mpiexec", path=launch_path) or shutil.which("mpirun", path=launch_path)
    if mpi.is_file() and mpiexec and not prefer_openmp and _local_mpi_socket_available():
        incompatibility = _glibc_incompatibility(mpi)
        if incompatibility is None:
            # PHITS MPI reserva un proceso maestro: N procesos de cálculo requieren N+1 rangos.
            # No pedir más ranks que los slots que OpenMPI recibe del host o cpuset.
            mpi_workers = min(cpu_workers, _available_cpu_slots() - 1)
            if mpi_workers >= 1:
                return PhitsRuntime(
                    mpi,
                    ("bash", str(wrapper)),
                    "mpi",
                    mpi_workers,
                )
    omp = next(
        (path for path in (phits_root / "bin" / "phits334_lin_openmp.exe", phits_root / "bin" / "phits_omp_lin") if path.is_file()),
        phits_root / "bin" / "phits334_lin_openmp.exe",
    )
    if omp.is_file():
        incompatibility = _glibc_incompatibility(omp)
        if incompatibility is None:
            return PhitsRuntime(omp, ("bash", str(wrapper)), "openmp", cpu_workers)

    candidates = [path for path in (mpi, omp, phits_root / "bin" / "phits334_lin.exe", phits_root / "bin" / "phits_lin") if path.is_file()]
    details = []
    for candidate in candidates:
        issue = _glibc_incompatibility(candidate)
        if issue:
            details.append(f"{candidate.name}: {issue}")
    if details:
        raise RuntimeError(
            "PHITS no puede iniciarse en este Linux: faltan bibliotecas compartidas "
            "o la GLIBC es incompatible (" + " | ".join(details) + "). "
            "Se necesita un binario PHITS compatible con este sistema."
        )
    raise FileNotFoundError(
        f"No se encontró un ejecutable PHITS utilizable bajo {phits_root / 'bin'}"
    )


def has_fatal_output(output: str) -> bool:
    return any(token in output.lower() for token in (
        "forrtl: severe", "forrtl: no such file or directory", "segmentation fault",
        "error stop", "fatal error", "calculation is terminated",
        "unable to create pmi port", "cannot open socket",
        "tetra error:", "tetra intersection error:", "tetra geometry error:",
    ))


def completed_history_layout(report: str) -> tuple[int, int] | None:
    lost = re.search(r"number of lost particles\s*=\s*(\d+)", report, re.IGNORECASE)
    if "job termination date" not in report.lower() or lost is None or int(lost.group(1)):
        return None
    history = re.search(
        r"source:\s*maxcas\s+maxbch\s+irskip[^\n]*\n[^\n]*\n\s*(\d+)\s+(\d+)\s+\d+",
        report, re.IGNORECASE,
    )
    if history is None:
        raise RuntimeError("PHITS no reportó el conteo efectivo de historias")
    return int(history.group(1)), int(history.group(2))


def run_input(runtime: PhitsRuntime, input_path: Path, environment=None) -> tuple[int, int] | None:
    work_root = input_path.parent
    launch_source = input_path.read_text(encoding="utf-8")
    source = launch_source.lower()
    output_match = re.search(r"^\s*file\(6\)\s*=\s*(\S+)", source, re.MULTILINE)
    output_name = output_match.group(1) if output_match else "phits.out"
    report_path = work_root / output_name
    prior_report_mtime = report_path.stat().st_mtime_ns if report_path.is_file() else None
    # El wrapper 3.341 interpreta $OMP/$MPI antes de la primera sección y
    # selecciona el ejecutable oficial; el binario interno no se invoca directo.
    launcher_path = work_root / f"{input_path.stem}_launcher.inp"
    directive = "$MPI" if runtime.parallel_mode == "mpi" else "$OMP"
    if runtime.parallel_mode == "mpi":
        from .model import _batch_layout

        histories_match = re.search(r"(?im)^[ \t]*maxcas[ \t]*=[ \t]*(\d+)[ \t]*$", launch_source)
        batches_match = re.search(r"(?im)^[ \t]*maxbch[ \t]*=[ \t]*(\d+)[ \t]*$", launch_source)
        if histories_match and batches_match:
            histories, batches = int(histories_match[1]), int(batches_match[1])
            if batches % runtime.workers:
                # Repartition the same budget across the actual MPI workers.
                new_histories, new_batches = _batch_layout(histories * batches, runtime.workers, batches)
                replacements = [(histories_match.span(1), new_histories), (batches_match.span(1), new_batches)]
                for (start, end), value in sorted(replacements, reverse=True):
                    launch_source = f"{launch_source[:start]}{value}{launch_source[end:]}"
    launcher_path.write_text(
        f"{directive}={runtime.workers}\n" + launch_source,
        encoding="utf-8",
    )
    command = [*runtime.command_prefix, launcher_path.name]
    env = _runtime_library_environment(environment)
    env["OMP_NUM_THREADS"] = str(runtime.workers)
    env["OMP_PROC_BIND"] = "spread"
    env["OMP_PLACES"] = "cores"
    phits_root = Path(runtime.command_prefix[-1]).resolve().parent.parent
    env["PHITSPATH"] = str(phits_root)
    env["PATH"] = str(phits_root / "bin") + os.pathsep + env.get("PATH", "")
    # OpenMPI 4 counts physical cores as slots by default. PHITS also needs a
    # master rank; allow hardware-thread slots only when the whole MPI job
    # fits in the CPU affinity, so this does not overrun the allocated cpuset.
    if runtime.parallel_mode == "mpi" and _available_physical_cores() < runtime.workers + 1 <= _available_cpu_slots():
        env.setdefault("OMPI_MCA_rmaps_base_oversubscribe", "1")
    completed = subprocess.run(
        command, cwd=work_root, env=env, check=False,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    log_path = work_root / f"{input_path.stem}.log"
    log_path.write_text(completed.stdout, encoding="utf-8", errors="replace")
    # phits.sh termina con la limpieza de temporales; puede devolver 0 incluso
    # si el ejecutable interno abortó antes de producir resultados.
    if completed.returncode != 0 or has_fatal_output(completed.stdout):
        raise RuntimeError(
            f"PHITS falló al ejecutar {input_path.name}; revisar {log_path}:\n"
            + completed.stdout[-3000:]
        )
    tetra_check = re.search(r"^\s*itgchk\s*=\s*1\b", source, re.MULTILINE) is not None
    geometry_view = re.search(r"^\s*icntl\s*=\s*8\b", source, re.MULTILINE) is not None
    finished = "program is finished" in completed.stdout.lower()
    history_layout = None
    if tetra_check:
        finished = "no error found in tetrahedral-mesh geom." in completed.stdout.lower()
    elif geometry_view:
        finished = "icntl = 8: draw geometry by gshow option" in completed.stdout.lower()
    else:
        fresh_report = report_path.is_file() and (
            prior_report_mtime is None or report_path.stat().st_mtime_ns > prior_report_mtime
        )
        report = report_path.read_text(encoding="utf-8", errors="replace") if fresh_report else ""
        history_layout = completed_history_layout(report)
        finished = history_layout is not None
        if not finished and report:
            log_path.write_text(completed.stdout + "\n--- PHITS REPORT ---\n" + report[-3000:], encoding="utf-8", errors="replace")
    if not finished:
        raise RuntimeError(
            f"PHITS no confirmó el fin de {input_path.name}; revisar {log_path}:\n"
            + completed.stdout[-3000:]
        )
    return history_layout
