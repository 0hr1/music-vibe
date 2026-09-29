#!/usr/bin/env bash
# Run the app locally with auto-reload: ./run.sh [port]  (default 8000)
# Uses .env if present, and the local data/ folder (not the Fly database).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements-dev.txt
fi

if [ -f .env ]; then
  set -a; source .env; set +a
fi
export HTTPS_ONLY=false  # plain http on localhost; a secure-only cookie would break login

exec .venv/bin/uvicorn app.main:app --reload --port "${1:-8000}"
