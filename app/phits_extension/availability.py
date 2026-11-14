"""Detección local y no invasiva del runtime PHITS instalado por el usuario."""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .model import PHITS_DEFAULT_ROOT
from .runner import resolve_runtime


@dataclass(frozen=True)
class PhitsAvailability:
    available: bool
    parallel_mode: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _profile_roots(home: Path) -> list[Path]:
    roots: list[Path] = []
    for profile in (home / ".bashrc", home / ".zshrc", home / ".profile"):
        try:
            lines = profile.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                tokens = shlex.split(stripped, comments=True, posix=True)
            except ValueError:
                continue
            for token in tokens:
                if not token.startswith("PHITSPATH="):
                    continue
                value = token.split("=", 1)[1]
                if not value or any(marker in value for marker in ("$(", "`", ";", "|")):
                    continue
                value = value.replace("${HOME}", str(home)).replace("$HOME", str(home))
                roots.append(Path(value).expanduser())
    return roots


def candidate_phits_roots(
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    default_root: Path | None = PHITS_DEFAULT_ROOT,
) -> list[Path]:
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    candidates: list[Path] = []
    configured = environment.get("PHITSPATH", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(_profile_roots(home))

    launcher = shutil.which("phits.sh", path=environment.get("PATH"))
    if launcher:
        candidates.append(Path(launcher).resolve().parent.parent)
    if default_root is not None:
        candidates.append(Path(default_root))

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        normalized = candidate.resolve()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def detect_phits_availability(
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    default_root: Path | None = PHITS_DEFAULT_ROOT,
) -> PhitsAvailability:
    for root in candidate_phits_roots(environment, home, default_root):
        try:
            runtime = resolve_runtime(root, cpu_workers=1)
        except (FileNotFoundError, OSError, RuntimeError):
            continue
        return PhitsAvailability(available=True, parallel_mode=runtime.parallel_mode)
    return PhitsAvailability(
        available=False,
        message="PHITS no está disponible en este sistema. Verifica la instalación y reinicia la aplicación.",
    )
