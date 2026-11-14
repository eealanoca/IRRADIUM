#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/distribution"
while [[ $# -gt 0 ]]; do
  case "$1" in
    *) printf 'Uso: %s (los datos nucleares se descargan al instalar)\n' "$0" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT"
VERSION="0.3.0-alpha.1"
ARCHIVE="$OUT/IRRADIUM-$VERSION-linux.tar.gz"
TEMP="$ARCHIVE.tmp"
STAGING=""
cleanup() {
  rm -f "$TEMP"
  [[ -z "$STAGING" ]] || rm -rf -- "$STAGING"
}
trap cleanup EXIT
tar -C "$ROOT" --exclude='node_modules' --exclude='dist' --exclude='.next' --exclude='.wrangler' \
  --exclude='docs/PHITS_PERFORMANCE_2026-11-13.md' --exclude='docs/presupuesto_predeterminado_2_por_ciento.md' \
  --exclude='docs/quickstart/*.tex' --exclude='app/docs' --exclude='app/examples/irradiation_loads' --exclude='app/icons/icon.png' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*.egg-info' \
  --exclude='*.tsbuildinfo' --exclude='*.aux' --exclude='*.log' --exclude='*.fdb_latexmk' --exclude='*.fls' --exclude='*.out' --exclude='*.toc' --exclude='installation-manifest.json' --exclude='.github' \
  --transform='flags=rh;s,^,IRRADIUM/,' \
  -czf "$TEMP" app scripts docs irradium install.sh uninstall.sh LICENSE DATA_NOTICES.md Iniciar_IRRADIUM.sh Desinstalar_IRRADIUM.sh README.md README.es.md ARCHITECTURE.md irradium-config.example.json \
  runtime/bin/micromamba runtime/guard runtime/licenses
mv "$TEMP" "$ARCHIVE"
(cd "$OUT" && sha256sum "IRRADIUM-$VERSION-linux.tar.gz" > "IRRADIUM-$VERSION-linux.tar.gz.sha256")
printf 'Paquete de instalación: %s\n' "$ARCHIVE"
