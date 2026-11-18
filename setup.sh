#!/usr/bin/env bash
# ai-memory — one-command setup for a developer Mac.
#
#   ./setup.sh
#
# 1. checks prerequisites           6. connects Codex, Qwen Code, Junie
# 2. creates .env (token, Jira, repos)  7. connects GLM / ZCode (if installed)
# 3. installs the MLX GPU server    8. adds usage instructions for the AIs
# 4. builds + starts the container  9. prints what's left to do
# 5. connects Claude Code (+ session hook)
#
# Safe to re-run. Every config file it touches is backed up first (*.bak-aimemory).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
URL="http://localhost:8765/mcp"
INTERACTIVE=0; [ -t 0 ] && INTERACTIVE=1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
backup() { [ -f "$1" ] && cp "$1" "$1.bak-aimemory" || true; }
env_get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
env_set() {  # env_set KEY VALUE  (replace or append, keep file private)
  touch .env; chmod 600 .env
  if grep -qE "^$1=" .env; then
    python3 - "$1" "$2" <<'PY'
import re, sys
k, v = sys.argv[1], sys.argv[2]
t = open(".env").read()
open(".env", "w").write(re.sub(rf"^{re.escape(k)}=.*$", lambda m: f"{k}={v}", t, flags=re.M))
PY
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
ask() {  # ask "Question" default  -> echo answer
  local a=""
  if [ "$INTERACTIVE" = 1 ]; then read -r -p "$1 [$2]: " a </dev/tty || true; fi
  echo "${a:-$2}"
}

# ---------------------------------------------------------------- 1. prerequisites
say "1/9 Checking prerequisites"
missing=0
if [[ "$(uname -s)" != "Darwin" ]]; then warn "This setup is written for macOS."; fi
if [[ "$(uname -m)" != "arm64" ]]; then warn "Not Apple Silicon: MLX is skipped — code search by names/keywords, no Explain AI."; fi
for t in git python3 curl openssl; do
  command -v "$t" >/dev/null || { warn "missing: $t"; missing=1; }
done
DOCKER="$(command -v docker || true)"
if [ -z "$DOCKER" ] || ! docker info >/dev/null 2>&1; then
  warn "Docker is not installed or not running. Install one, start it, re-run ./setup.sh:"
  echo "    brew install --cask orbstack     # light and fast (recommended)"
  echo "    brew install --cask docker       # Docker Desktop"
  missing=1
fi
command -v claude >/dev/null || warn "Claude Code CLI not found — it will be skipped (install it and re-run)."
[ "$missing" = 0 ] || exit 1
echo "ok: docker, git, python3, curl, openssl"

# ---------------------------------------------------------------- 2. .env
say "2/9 Configuration (.env — private, never committed)"
[ -n "$(env_get MEMORY_TOKEN)" ] || env_set MEMORY_TOKEN "$(openssl rand -hex 24)"
TOKEN="$(env_get MEMORY_TOKEN)"

if [ -z "$(env_get PROJECTS_DIR)" ]; then
  env_set PROJECTS_DIR "$(ask 'Folder that contains your repos' "$HOME/Desktop/projects")"
fi
PROJECTS_DIR="$(env_get PROJECTS_DIR)"
if [ -z "$(env_get TZ)" ]; then  # logs and the backup schedule use the Mac's time zone
  MAC_TZ="$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##')"
  env_set TZ "${MAC_TZ:-UTC}"
  env_set BACKUP_TZ "${MAC_TZ:-UTC}"
fi
if [ -z "$(env_get CODE_PROJECTS)" ]; then
  env_set CODE_PROJECTS "$(ask 'Repo folder(s) to index for code search, comma-separated' 'my-app')"
fi
for p in $(env_get CODE_PROJECTS | tr ',' ' '); do
  [ -d "$PROJECTS_DIR/$p/.git" ] || warn "$PROJECTS_DIR/$p is not a git repo (clone it first, or fix CODE_PROJECTS in .env)"
done

if [ -z "$(env_get JIRA_API_TOKEN)" ]; then
  echo "Jira (optional): stories, daily sync, 'why' links. Create a token at"
  echo "  https://id.atlassian.com/manage-profile/security/api-tokens"
  if [ "$INTERACTIVE" = 1 ]; then
    JE="$(ask 'Your Jira e-mail (empty = skip Jira)' '')"
    if [ -n "$JE" ]; then
      read -r -s -p "Jira API token (input hidden): " JT </dev/tty; echo
      env_set JIRA_URL "$(ask 'Jira URL' 'https://your-company.atlassian.net')"
      env_set JIRA_EMAIL "$JE"
      env_set JIRA_SYNC_PROJECT "$(ask 'Jira project key to sync daily, e.g. PROJ (empty = no daily sync)' '')"
      [ -n "${JT:-}" ] && env_set JIRA_API_TOKEN "$JT"
    fi
  fi
fi
[ -n "$(env_get JIRA_API_TOKEN)" ] && echo "Jira: configured" || echo "Jira: off (add JIRA_URL / JIRA_EMAIL / JIRA_API_TOKEN to .env later)"

# ---------------------------------------------------------------- 3. MLX (GPU, outside Docker)
say "3/9 MLX server (Mac GPU: code-search meaning + local 'explain' model)"
mkdir -p "$HOME/Library/Logs"; touch "$HOME/Library/Logs/mlx-embed.log"   # Docker mounts this file
if [[ "$(uname -m)" != "arm64" ]] || [ "$(env_get MLX)" = "off" ]; then
  # No Apple GPU (or turned off): skip it. Everything works; code search uses names/keywords, no Explain AI.
  [ -n "$(env_get MLX)" ] || env_set MLX off
  echo "MLX: off — code search by names/keywords, Explain AI hidden (set MLX=auto in .env to use it)"
else
  "$DIR/scripts/install-mlx.sh" || warn "MLX not running — code search uses names/keywords until it runs. Re-run scripts/install-mlx.sh later, or set MLX=off in .env."
fi

# ---------------------------------------------------------------- 4. container
say "4/9 Building and starting ai-memory (Docker)"
mkdir -p data backups
docker compose up -d --build
for _ in $(seq 1 60); do curl -s http://localhost:8765/health | grep -q ok && break; sleep 1; done
curl -s http://localhost:8765/health | grep -q ok || { echo "Server did not start — see: docker compose logs"; exit 1; }
echo "Server is up: $URL · dashboard: http://localhost:8765/ui"

STDIO_ARGS='["exec","-i","ai-memory","python","/app/memory_server.py","--stdio"]'

# ---------------------------------------------------------------- 5. Claude Code (+ session hook)
say "5/9 Claude Code"
if command -v claude >/dev/null; then
  claude mcp remove ai-memory --scope user >/dev/null 2>&1 || true
  claude mcp add --transport http --scope user ai-memory "$URL" --header "Authorization: Bearer $TOKEN"
  SETTINGS="$HOME/.claude/settings.json"; mkdir -p "$HOME/.claude"; [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  backup "$SETTINGS"
  python3 - "$SETTINGS" "$DIR/hooks/session-start.sh" <<'PY'
import json, sys
path, cmd = sys.argv[1], sys.argv[2]
s = json.load(open(path))
groups = s.setdefault("hooks", {}).setdefault("SessionStart", [])
# drop older copies of our hook (any path), then add exactly one
for g in groups:
    g["hooks"] = [h for h in g.get("hooks", []) if not h.get("command", "").endswith("hooks/session-start.sh")]
groups[:] = [g for g in groups if g.get("hooks")]
target = next((g for g in groups if g.get("matcher", "") == ""), None)
if target is None:
    target = {"matcher": "", "hooks": []}; groups.append(target)
target["hooks"].append({"type": "command", "command": cmd, "timeout": 5})
json.dump(s, open(path, "w"), indent=2); open(path, "a").write("\n")
PY
  echo "connected; session hook added to $SETTINGS"
else
  echo "claude CLI not found — skipped"
fi

# ---------------------------------------------------------------- 6. Codex / Qwen Code / Junie
say "6/9 Codex, Qwen Code, Junie"
mkdir -p "$HOME/.codex"; CODEX="$HOME/.codex/config.toml"; touch "$CODEX"; backup "$CODEX"
python3 - "$CODEX" "$DOCKER" <<'PY'
import re, sys
path, docker = sys.argv[1], sys.argv[2]
text = open(path).read()
text = re.sub(r"\n?# >>> ai-memory >>>.*?# <<< ai-memory <<<\n?", "\n", text, flags=re.S)
block = f'''
# >>> ai-memory >>>
[mcp_servers.ai-memory]
command = "{docker}"
args = ["exec", "-i", "ai-memory", "python", "/app/memory_server.py", "--stdio"]
# <<< ai-memory <<<
'''
open(path, "w").write(text.rstrip() + "\n" + block)
PY
mkdir -p "$HOME/.qwen"; QWEN="$HOME/.qwen/settings.json"; backup "$QWEN"
python3 - "$QWEN" "$URL" "$TOKEN" <<'PY'
import json, sys
path, url, token = sys.argv[1:]
try: s = json.load(open(path))
except Exception: s = {}
s.setdefault("mcpServers", {})["ai-memory"] = {"httpUrl": url, "headers": {"Authorization": f"Bearer {token}"}, "timeout": 30000}
json.dump(s, open(path, "w"), indent=2)
PY
mkdir -p "$HOME/.junie/mcp"; JUNIE="$HOME/.junie/mcp/mcp.json"; backup "$JUNIE"
python3 - "$JUNIE" "$DOCKER" "$STDIO_ARGS" <<'PY'
import json, sys
path, docker, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
try: s = json.load(open(path))
except Exception: s = {}
s.setdefault("mcpServers", {})["ai-memory"] = {"command": docker, "args": args}
json.dump(s, open(path, "w"), indent=2)
PY
echo "updated $CODEX, $QWEN, $JUNIE"

# ---------------------------------------------------------------- 7. GLM / ZCode
say "7/9 GLM (ZCode)"
ZC="$HOME/.zcode/cli/config.json"
if [ -f "$ZC" ]; then
  backup "$ZC"
  python3 - "$ZC" "$TOKEN" <<'PY'
import json, sys
path, token = sys.argv[1], sys.argv[2]
s = json.load(open(path))
servers = s.setdefault("mcp", {}).setdefault("servers", {})
if "ai-memory" not in servers:  # keep an existing (working) entry untouched
    servers["ai-memory"] = {"command": "npx", "args": ["-y", "mcp-remote", "http://127.0.0.1:8765/mcp",
                                                       "--header", f"Authorization: Bearer {token}"]}
    json.dump(s, open(path, "w"), indent=2); print("added ai-memory to", path)
else:
    print("ai-memory already configured in", path)
PY
  echo "Auto-briefing for GLM is set per repo (only where .zcode/ is gitignored), then approve it in ZCode once:"
  echo "  mkdir -p <repo>/.zcode && sed \"s#\\\$HOME/Documents/ai-memory#$DIR#\" $DIR/hooks/zcode-config.example.json > <repo>/.zcode/config.json"
else
  echo "ZCode not installed — skipped"
fi

# ---------------------------------------------------------------- 8. instructions for the AIs
say "8/9 Usage instructions for the AIs"
for f in "$HOME/.claude/CLAUDE.md" "$HOME/.codex/AGENTS.md" "$HOME/.qwen/QWEN.md"; do
  mkdir -p "$(dirname "$f")"; touch "$f"; backup "$f"
  python3 - "$f" "$DIR/MEMORY_INSTRUCTIONS.md" <<'PY'
import re, sys
path, src = sys.argv[1], sys.argv[2]
text = open(path).read()
text = re.sub(r"\n?<!-- >>> ai-memory >>> -->.*?<!-- <<< ai-memory <<< -->\n?", "\n", text, flags=re.S)
block = "<!-- >>> ai-memory >>> -->\n" + open(src).read().strip() + "\n<!-- <<< ai-memory <<< -->\n"
open(path, "w").write((text.rstrip() + "\n\n" + block).lstrip())
PY
  echo "updated $f"
done

# ---------------------------------------------------------------- 9. done
say "9/9 Done"
cat <<EOF
  Dashboard:     http://localhost:8765/ui   (logs you in automatically)
  Health check:  curl -s http://localhost:8765/health
  Next:
   - Restart Claude Code / Codex / your JetBrains IDE / ZCode so they load the server.
   - Code index builds by itself within ~10 min (first time ~7 min on the GPU).
   - Jira stories sync within a few minutes if Jira is configured.
   - Junie: paste MEMORY_INSTRUCTIONS.md into each project's .junie/guidelines.md.
  Guide: GUIDE.md · Setup notes: SETUP.md
EOF
