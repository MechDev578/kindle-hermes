#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  uv venv .venv
  uv pip install --python .venv/bin/python -r requirements.txt
fi

exec .venv/bin/uvicorn backend.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --reload
