#!/usr/bin/env bash
# Run from a downloaded release or Git checkout. No privileged package installation.
set -euo pipefail
installer_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Se necesita Python 3.11+ para ejecutar el instalador.' >&2
  exit 1
fi
exec python3 "$installer_root/scripts/install.py" "$@"
