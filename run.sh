#!/usr/bin/env bash
# Run the app locally with auto-reload: ./run.sh [port]  (default 8000)
# Only this machine can reach it unless HOST is set: HOST=tailscale ./run.sh serves it on this
# machine's Tailscale address (tailnet only); any other HOST value is passed to uvicorn as is.
# Uses .env if present, and the local data/ folder (not the Fly database).
set -euo pipefail
cd "$(dirname "$0")"

host="${HOST:-127.0.0.1}"
if [ "$host" = tailscale ]; then
  host="$(tailscale ip -4)"
fi

if [ ! -x .venv/bin/uvicorn ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements-dev.txt
fi

if [ -f .env ]; then
  set -a; source .env; set +a
fi
export HTTPS_ONLY=false  # served over plain http; a secure-only cookie would break login

echo "Serving on http://$host:${1:-8000}"
exec .venv/bin/uvicorn app.main:app --reload --host "$host" --port "${1:-8000}"
