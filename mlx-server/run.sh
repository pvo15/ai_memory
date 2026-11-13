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

exec "$PY" server.py
