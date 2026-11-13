#!/usr/bin/env bash
# Opt-in scheduled refresh wrapper. Default: do NOT install in launchd/cron.
# Runs: refresh + score new + enrich ready-to-apply candidates.
# Never opens browsers, fills forms, or submits applications.
# Logs: ~/.ganesha/logs/ (outside the repo; gitignored by location).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="${GANESHA_VENV_PYTHON:-/Users/amanjakunnel/Tech/Ganesha/.venv/bin/python}"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"
LOG_DIR="${HOME}/.ganesha/logs"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true

cd "$ROOT"
exec "$VENV_PYTHON" -m apps.cli.main jobs scheduled-refresh "$@"
