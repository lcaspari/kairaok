#!/bin/bash
# Start Kairaok and open it in the browser.
cd "$(dirname "$0")"
. ./_env.sh

if ! use_env || ! "$PY" -c "import fastapi, torch, demucs" 2>/dev/null; then
  echo "Kairaok is not set up yet (or setup did not finish). Run ./setup.sh first."
  exit 1
fi

export PYTORCH_ENABLE_MPS_FALLBACK=1      # lets Demucs use the Apple GPU safely (ignored elsewhere)
PORT="${KAIRAOK_PORT:-8642}"
( sleep 3; open_url "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true ) &
exec "$PY" -m kairaok
