#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IRRADIUM_DESKTOP_DATA_HOME="${IRRADIUM_DESKTOP_DATA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}}"
mkdir -p "$ROOT/runtime/bin" "$ROOT/runtime/mamba" "$ROOT/.local/tmp" "$ROOT/.local/empty"
export MAMBA_ROOT_PREFIX="$ROOT/runtime/mamba" CONDA_PKGS_DIRS="$ROOT/runtime/mamba/pkgs" CONDA_REGISTER_ENVS=false
export XDG_CACHE_HOME="$ROOT/.local/cache" XDG_CONFIG_HOME="$ROOT/.local/config" XDG_DATA_HOME="$ROOT/.local/share"
export TMPDIR="$ROOT/.local/tmp" PIP_CACHE_DIR="$ROOT/.local/cache/pip"
export PIP_CONFIG_FILE=/dev/null PIP_USER=false PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PATH="/usr/local/bin:/usr/bin:/bin"
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_EXE CONDA_PYTHON_EXE
export LD_LIBRARY_PATH="$ROOT/runtime/guard/lib"
GUARD="$ROOT/runtime/guard/bwrap"
[[ -x "$GUARD" ]] || { printf 'El paquete debe incluir runtime/guard.\n' >&2; exit 1; }
if [[ ! -x "$ROOT/runtime/bin/micromamba" ]]; then
  curl --fail --location --output "$TMPDIR/micromamba.tar.bz2" https://micro.mamba.pm/api/micromamba/linux-64/latest
  tar -xjf "$TMPDIR/micromamba.tar.bz2" -C "$ROOT/runtime" bin/micromamba
  rm "$TMPDIR/micromamba.tar.bz2"
fi
cd "$ROOT/app"
if [[ ! -x "$ROOT/runtime/main/bin/python" ]]; then
  "$GUARD" --ro-bind / / --bind "$ROOT" "$ROOT" --dev /dev --proc /proc --tmpfs /tmp --chdir "$ROOT/app" \
    -- "$ROOT/runtime/bin/micromamba" --no-rc -r "$ROOT/runtime/mamba" create -y -p "$ROOT/runtime/main" -f environment.yml
fi
exec "$ROOT/irradium" install
