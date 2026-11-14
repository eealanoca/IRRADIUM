"""Save bounded-size checkpoints and reserve time to publish a usable map."""
import math
from pathlib import Path
import subprocess
import time


def configure_checkpoints(settings) -> None:
    maximum = int(settings.trigger_max_batches if settings.trigger_active else settings.batches)
    total = int(settings.particles) * maximum
    batch_cap = 100_000 if settings.trigger_active else 200_000
    if settings.particles > batch_cap:
        maximum = math.ceil(total / batch_cap)
        settings.particles = total // maximum
        if settings.trigger_active:
            settings.batches = min(maximum, max(25, int(settings.batches)))
            settings.trigger_max_batches = maximum
        else:
            settings.batches = maximum
    batches = {maximum}
    batches.update(range(2, min(maximum, 100) + 1, 1 if settings.trigger_active else 5))
    batches.update(range(100, min(maximum, 10_000) + 1, 100))
    batches.update(range(10_000, maximum, max(100, maximum // 10_000)))
    settings.statepoint = {"batches": sorted(batches)}


def valid_checkpoints(root: Path) -> list[Path]:
    import h5py
    valid = []
    for path in sorted(root.glob("statepoint.*.h5"), key=lambda p: int(p.stem.rsplit(".", 1)[-1])):
        try:
            with h5py.File(path, "r") as statepoint:
                if int(statepoint["n_realizations"][()]) >= 2:
                    valid.append(path)
        except (OSError, KeyError, ValueError):
            # OpenMC may still be writing the newest checkpoint.
            continue
    return valid


def prune_completed_checkpoints(root: Path, selected: Path) -> int:
    """After publishing a position, retain its referenced cumulative checkpoint."""
    valid = valid_checkpoints(root)
    if selected.resolve().parent != root.resolve() or selected not in valid:
        return 0
    released = 0
    for path in valid:
        if path != selected:
            released += path.stat().st_size
            path.unlink()
    return released


def run_checkpointed_transport(executable: str, root: Path, threads: int, event_based: bool, deadline: float,
                               reserve_seconds: float = 30, restart_file: Path | None = None) -> tuple[bool, Path]:
    command = [executable, "-s", str(threads)] + (["-e"] if event_based else [])
    if restart_file is not None:
        command.extend(["-r", str(restart_file)])
    limited = False
    with (root / "transport.log").open("w") as output:
        process = subprocess.Popen(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                valid = valid_checkpoints(root)
                for obsolete in valid[:-2]:
                    obsolete.unlink(missing_ok=True)
                if time.monotonic() >= deadline - reserve_seconds:
                    limited = True
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                time.sleep(.5)
            code = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    valid = valid_checkpoints(root)
    if code and not limited:
        raise RuntimeError("OpenMC terminó con error:\n" + (root / "transport.log").read_text(errors="replace")[-4000:])
    if not valid:
        if limited:
            raise TimeoutError("Se agotó el tiempo antes del primer checkpoint con una estimación del error.")
        raise RuntimeError("OpenMC no produjo un checkpoint válido")
    return limited, valid[-1]
