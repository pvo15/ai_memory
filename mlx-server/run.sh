#!/usr/bin/env bash
# Starts the MLX server (embeddings + local chat model) with a Python that has mlx-lm.
# Normally started by the LaunchAgent that scripts/install-mlx.sh installs.
# Override with MLX_PYTHON=/path/to/python.
set -euo pipefail
cd "$(dirname "$0")"

PY="${MLX_PYTHON:-}"
if [[ -z "$PY" ]]; then
  for candidate in "$HOME/.local/share/uv/tools/mlx-lm/bin/python" python3; do
    if "$candidate" -c 'import mlx_lm' 2>/dev/null; then PY="$candidate"; break; fi
  done
fi
if [[ -z "$PY" ]]; then
  echo "No Python with mlx-lm found. Install it: uv tool install mlx-lm  (or set MLX_PYTHON)" >&2
  exit 1
fi

# Watchdog: launchd restarts the server when it EXITS, but not when it HANGS (e.g. the Mac ran
# out of memory and the process got stuck). After the server has answered once, check /health
# every WATCHDOG_EVERY seconds; after WATCHDOG_FAILS misses in a row, kill it and exit, so
# launchd starts a fresh one. (launchd also stops the server whenever this script ends.)
EVERY="${WATCHDOG_EVERY:-60}"; FAILS="${WATCHDOG_FAILS:-3}"; PORT="${MCS_MLX_PORT:-18765}"
"$PY" server.py &
PID=$!
trap 'kill "$PID" 2>/dev/null' TERM INT
seen=0; misses=0
while kill -0 "$PID" 2>/dev/null; do
  sleep "$EVERY" & wait $! || true          # wait (not plain sleep) so TERM is handled at once
  kill -0 "$PID" 2>/dev/null || break
  if /usr/bin/curl -s -m 20 -o /dev/null "http://127.0.0.1:$PORT/health"; then
    seen=1; misses=0
  elif [[ "$seen" == 1 ]]; then
    misses=$((misses + 1))
    if (( misses >= FAILS )); then
      echo "watchdog: /health did not answer $misses times in a row - restarting the server" >&2
      kill -9 "$PID" 2>/dev/null || true
      break
    fi
  fi
done
wait "$PID" 2>/dev/null || true
exit 1  # launchd (KeepAlive) starts it again
