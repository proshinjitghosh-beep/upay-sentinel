#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [ ! -f data/transactions.csv ]; then .venv/bin/python -m data.generate_data; fi
if command -v gcc >/dev/null 2>&1; then gcc -O2 -shared -fPIC -o engine/libsentinel.so engine/graph_engine.c; fi
printf 'Open http://127.0.0.1:8000 after startup.\n'
exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
