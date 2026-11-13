# ai-memory — shared, self-hosted memory for your AI tools

One server on your Mac that **all your AI tools share**: Claude Code, GLM (ZCode), Codex,
Qwen and Junie. Your work survives when you switch task, AI tool or chat.

Everything runs locally: a Docker container plus a small server on the Mac GPU.
Your data is one SQLite file and never leaves your Mac.

## What it does

| Area | What you get |
|---|---|
| 🧠 **Memory** | Preferences, conventions and decisions, saved once and shared by every AI |
| 👥 **Team sync** | Share memories with teammates through a git repo; personal ones stay private ([SETUP.md](SETUP.md#3a-team-memory-sync-optional)) |
| 📒 **Task journal** | Per Jira ticket: current state, next steps, decisions. Created automatically when you open a session on a branch like `feature-PROJ-142` |
| 🎫 **Jira** | Stories fetched on demand and cached, with a daily sync and search across all stories |
| 📐 **Project rules** | `CLAUDE.md`, `AGENTS.md`, `.junie/guidelines.md`, `docs/`, agent files, searchable by topic |
| 🔎 **Code search** | By meaning (GPU embeddings) and by name, plus fuzzy file search (`ptp` → `PaymentTemplatesPanel`) |
| 🕰 **"Why" layer** | Paste code → the commits that wrote those exact lines → their Jira story |
| 📊 **Dashboard** | http://localhost:8765/ui: health, running jobs, Jira, code + local AI explain, memories, backups, logs, cheat sheet |
| 🛟 **Ops** | Backups Mon–Fri, health checks, stoppable background jobs |

## How it fits together

```
 Claude Code · GLM · Codex · Qwen · Junie        Dashboard (browser)
            │ MCP + token                          │ /ui (auto-login)
            └──────────────────┬───────────────────┘
                               ▼
        ai-memory — Docker, http://localhost:8765
        memories · task journal · Jira · rules · code search · git history
                │                                   │
                ▼                                   ▼ embeddings + "explain"
        data/memory.db (SQLite)          MLX server — Mac GPU, 127.0.0.1:18765
        backups/ (daily copies)          macOS LaunchAgent, starts at login
```

Docker on a Mac can't use the GPU, so the MLX server runs natively. If it is off, everything
still works; code search then matches names instead of meaning.

## Quick start

```bash
cd ~/Documents/ai-memory
./setup.sh                         # install / repair / update — safe to re-run
open http://localhost:8765/ui      # dashboard (automatic login)
curl -s localhost:8765/health      # → ok
```

Full install steps: [SETUP.md](SETUP.md). Needs macOS on Apple Silicon, Docker, `uv`, git and
python3. Jira, ZCode, and Node 24 + pnpm (only for dashboard development) are optional.

## Documentation

| Read | For |
|---|---|
| [SETUP.md](SETUP.md) | Installing on a new Mac, per-developer settings, uninstall |
| [GUIDE.md](GUIDE.md) | How it works, all AI tools, the dashboard, commands, troubleshooting |
| [MEMORY_INSTRUCTIONS.md](MEMORY_INSTRUCTIONS.md) | The text each AI tool gets so it uses ai-memory well |
| [.env.example](.env.example) | All settings |

## Project layout

| Path | What it is |
|---|---|
| `memory_server.py` | Server entry: MCP tools, auth (token + dashboard cookie), `/context`, schedulers |
| `tasks.py` · `jira.py` · `rules.py` | Task journal · Jira fetch/cache/sync · project rules |
| `codesearch.py` · `githistory.py` · `snippet.py` | Code index + search · git history · pasted-code explain |
| `dashboard.py` · `jobs.py` · `backup.py` | Dashboard API + logs + health · stoppable jobs · backups |
| `sync.py` | Team memory sync through a shared git repo |
| `web/` | Dashboard UI (React 19, TypeScript, Vite, TanStack Query), built inside Docker |
| `mlx-server/` · `scripts/install-mlx.sh` | GPU server (embeddings + local Qwen2.5-Coder) and its installer |
| `hooks/session-start.sh` | Session hook: injects the `<task-journal>` briefing into Claude / GLM |
| `setup.sh` · `Dockerfile` · `docker-compose.yml` | One-command setup and the container |

## Development

```bash
# Python change: test on a COPY of the database first, never on data/memory.db
sqlite3 data/memory.db ".backup '/tmp/aimem-test/memory.db'"

# Dashboard change (Node 24, pnpm)
cd web && pnpm typecheck && pnpm build && cd ..

# Deploy: back up, rebuild, check
sqlite3 data/memory.db ".backup 'backups/manual-before-<change>-$(date +%Y%m%d-%H%M).db'"
docker compose up -d --build && curl -s localhost:8765/health

# GPU server change: edit mlx-server/server.py, then
scripts/install-mlx.sh
```

For browser tests, run a second container on another port with a throwaway test token. Never
type your real token into a page.

## Never commit

`.env` and `.env.*` (except `.env.example`), `data/`, `backups/`, `backup-*/`. `.gitignore`
already excludes them. Each developer has their own token, Jira credentials and data.
