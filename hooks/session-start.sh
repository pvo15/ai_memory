#!/usr/bin/env bash
# Session hook for ai-memory.
#
#   session-start.sh          SessionStart, Claude Code (~/.claude/settings.json): plain text on stdout
#   session-start.sh --json   SessionStart, ZCode / GLM (<repo>/.zcode/config.json): JSON on stdout
#   session-start.sh --end    SessionEnd, Claude Code: prints nothing
#
# At start: injects the task briefing, the project's newest memories, and (in a repo ai-memory
# doesn't read yet) a "how to connect it" note. Runs on startup, resume, /clear and after
# auto-compaction (when context is lost).
# At start and end: copies Claude Code's own memory files for this folder into ai-memory, so the
# other AI tools see them too (one memory store).
# At end: adds a short "Session ended: ..." note to the branch's task journal.
# The client passes JSON on stdin (we use "cwd" and, at the end, "transcript_path").
# We never fail the session: any problem -> print nothing, exit 0.
set -uo pipefail

MODE=start
FORMAT=text
for a in "$@"; do
  case "$a" in
    --json) FORMAT=json ;;
    --end) MODE=end ;;
  esac
done

DIR="$(cd "$(dirname "$0")/.." && pwd)"
URL="${AI_MEMORY_URL:-http://localhost:8765}"
TOKEN="$(grep '^MEMORY_TOKEN=' "$DIR/.env" 2>/dev/null | cut -d= -f2)"
[ -n "$TOKEN" ] || exit 0

# Read stdin without hanging if the client keeps it open (wait at most 1s).
INPUT=""
[ -t 0 ] || IFS= read -r -d '' -t 1 INPUT || true
field() { printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get(sys.argv[1],""))' "$1" 2>/dev/null; }
CWD="$(field cwd)"
[ -n "$CWD" ] || CWD="$PWD"

BRANCH="$(git -C "$CWD" branch --show-current 2>/dev/null)"
TOP="$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)"
[ -n "$BRANCH" ] || exit 0                      # not a git repo / detached HEAD
PROJECT="$(basename "${TOP:-$CWD}")"
# Main checkout of the repo (a git worktree points back to it): lets the server tell
# whether this repo is connected to ai-memory, and add a "how to connect" note if not.
COMMON="$(git -C "$CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
REPO="${COMMON%/.git}"
[ -n "$COMMON" ] && [ "$REPO" != "$COMMON" ] || REPO="${TOP:-$CWD}"

post() {  # post <path>: JSON body on stdin
  curl -s -m 3 -X POST "$URL$1" -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" --data-binary @- >/dev/null 2>&1 || true
}

# Claude Code keeps its memory in ~/.claude/projects/<folder path, non-alphanumerics as "-">/memory.
# Send the whole folder; the server adds, updates and removes to match.
if [ "$FORMAT" = text ]; then
  python3 - "$CWD" "$TOP" "$REPO" <<'PY' 2>/dev/null | post /context/memory-files
import json, os, re, sys
cwd, top, repo = sys.argv[1:4]
base = os.path.expanduser("~/.claude/projects")
for p in (cwd, top, repo):
    d = os.path.join(base, re.sub(r"[^A-Za-z0-9]", "-", p), "memory") if p else ""
    if d and os.path.isdir(d):
        files = []
        for n in sorted(os.listdir(d)):
            if n.endswith(".md") and n != "MEMORY.md":
                with open(os.path.join(d, n), encoding="utf-8", errors="replace") as f:
                    files.append({"name": n, "text": f.read(20000)})
        print(json.dumps({"project": os.path.basename(repo.rstrip("/")), "dir": os.path.basename(os.path.dirname(d)), "files": files}))
        break
PY
fi

if [ "$MODE" = end ]; then
  # Where the session stopped: the last wrap-up recap (Claude desktop app), else the last reply.
  TRANSCRIPT="$(field transcript_path)"
  [ -f "$TRANSCRIPT" ] || exit 0
  python3 - "$TRANSCRIPT" "$BRANCH" <<'PY' 2>/dev/null | post /context/session-end
import json, sys
path, branch = sys.argv[1:3]
recap = text = ""
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        if '"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name", "").endswith("__wrap_up"):
                recap = (b.get("input") or {}).get("recap", "") or recap
            elif b.get("type") == "text" and b.get("text", "").strip():
                text = b["text"]
note = (recap or text).strip()
if note:
    print(json.dumps({"branch": branch, "note": note[:2000], "source": "claude-code (session end)"}))
PY
  exit 0
fi

CLIENT=claude
[ "$FORMAT" = json ] && CLIENT=zcode
BRIEF="$(curl -s -m 3 -G "$URL/context" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "branch=$BRANCH" \
  --data-urlencode "project=$PROJECT" \
  --data-urlencode "repo=$REPO" \
  --data-urlencode "client=$CLIENT" 2>/dev/null)" || exit 0
[ -n "$BRIEF" ] || exit 0

if [ "$FORMAT" = json ]; then
  printf '%s' "$BRIEF" | python3 -c 'import sys,json; print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": sys.stdin.read()}}))'
else
  printf '%s\n' "$BRIEF"
fi
exit 0
