#!/usr/bin/env bash
# Start the editor. First run:  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")"
[ -d data/baseline ] || { echo "no data/ yet — run:  python3 bootstrap.py --disks <dir with your .g64 images>"; exit 1; }
exec python3 -m uvicorn app:app --app-dir backend --host "${WL_HOST:-127.0.0.1}" --port "${WL_PORT:-8080}" "$@"
