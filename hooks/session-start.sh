#!/usr/bin/env bash
# SessionStart hook: inject the current task's briefing into a new AI session.
#
# Used by:
#   Claude Code  (~/.claude/settings.json)        -> plain text on stdout
#   ZCode / GLM  (<repo>/.zcode/config.json)      -> run with --json
# Runs on startup, resume, /clear and after auto-compaction (when context is lost).
# The client may pass JSON on stdin (we use "cwd"). We never fail the session:
# any problem -> print nothing, exit 0.
set -uo pipefail

FORMAT=text
[ "${1:-}" = "--json" ] && FORMAT=json

DIR="$(cd "$(dirname "$0")/.." && pwd)"
URL="${AI_MEMORY_URL:-http://localhost:8765}"
TOKEN="$(grep '^MEMORY_TOKEN=' "$DIR/.env" 2>/dev/null | cut -d= -f2)"
[ -n "$TOKEN" ] || exit 0

# Read stdin without hanging if the client keeps it open (wait at most 1s).
INPUT=""
[ -t 0 ] || IFS= read -r -d '' -t 1 INPUT || true
CWD="$(printf '%s' "$INPUT" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null)"
[ -n "$CWD" ] || CWD="$PWD"

BRANCH="$(git -C "$CWD" branch --show-current 2>/dev/null)"
TOP="$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)"
[ -n "$BRANCH" ] || exit 0                      # not a git repo / detached HEAD
PROJECT="$(basename "${TOP:-$CWD}")"

BRIEF="$(curl -s -m 3 -G "$URL/context" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "branch=$BRANCH" \
  --data-urlencode "project=$PROJECT" 2>/dev/null)" || exit 0
[ -n "$BRIEF" ] || exit 0

if [ "$FORMAT" = json ]; then
  printf '%s' "$BRIEF" | /usr/bin/python3 -c 'import sys,json; print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": sys.stdin.read()}}))'
else
  printf '%s\n' "$BRIEF"
fi
exit 0
