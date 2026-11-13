#!/usr/bin/env bash
# Installs the MLX server (code-search embeddings + local "explain" model) as a macOS
# service that starts at every login and restarts if it crashes.
#
#   Why outside Docker: MLX needs the Apple GPU (Metal); Docker on a Mac can't use it.
#   Why ~/.local/share:  macOS blocks background services from reading Desktop/Documents/
#                        Downloads, so the server runs from an unprotected folder.
# Safe to re-run (updates the server and restarts it).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.aimemory.mlx-embed"
INSTALL="$HOME/.local/share/mlx-embed"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/mlx-embed.log"
UID_="$(id -u)"
say() { printf '\033[1m%s\033[0m\n' "$*"; }

# 1. Apple Silicon only
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "MLX needs a Mac with Apple Silicon (M1/M2/M3/M4). Skipping: code search works by names only."
  exit 0
fi

# 2. mlx-lm (via uv)
PY="$HOME/.local/share/uv/tools/mlx-lm/bin/python"
if ! "$PY" -c 'import mlx_lm' 2>/dev/null; then
  if ! command -v uv >/dev/null; then
    if command -v brew >/dev/null; then say "Installing uv (Python tool manager)…"; brew install uv
    else echo "Please install uv first: https://docs.astral.sh/uv/  (or: brew install uv)"; exit 1; fi
  fi
  say "Installing mlx-lm…"; uv tool install mlx-lm
fi

# 3. Server files -> unprotected folder; log file must exist before Docker mounts it
mkdir -p "$INSTALL" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$DIR/mlx-server/server.py" "$DIR/mlx-server/run.sh" "$INSTALL/"
chmod +x "$INSTALL/run.sh"
touch "$LOG"

# 4. LaunchAgent (replace the old one if present)
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- ai-memory MLX server (Mac GPU, http://127.0.0.1:18765). Installed by scripts/install-mlx.sh.
     Starts at login, restarts on crash. Off: launchctl bootout gui/\$(id -u)/$LABEL -->
<plist version="1.0">
<dict>
  <key>Label</key>             <string>$LABEL</string>
  <key>ProgramArguments</key>  <array><string>/bin/bash</string><string>$INSTALL/run.sh</string></array>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>ThrottleInterval</key>  <integer>30</integer>
  <key>ProcessType</key>       <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>            <string>$HOME</string>
    <key>PATH</key>            <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>StandardOutPath</key>   <string>$LOG</string>
  <key>StandardErrorPath</key> <string>$LOG</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$UID_" "$PLIST"

# 5. Wait until it answers (first start downloads the embedding model, ~350 MB)
say "Starting MLX server (first run downloads the model)…"
for _ in $(seq 1 150); do curl -s -m 2 http://127.0.0.1:18765/health >/dev/null && break; sleep 2; done
if curl -s -m 2 http://127.0.0.1:18765/health >/dev/null; then
  echo "MLX server is up: $(curl -s http://127.0.0.1:18765/health)"
else
  echo "MLX server did not answer yet — check: tail -30 $LOG"; exit 1
fi
