# ai-memory — shared, self-hosted memory for your AI tools

One server on your Mac that **all your AI tools share**: Claude Code, GLM (ZCode), Codex,
Qwen and Junie. Your work survives when you switch task, AI tool or chat.

Everything runs locally: a Docker container plus a small server on the Mac GPU.
Your data is one SQLite file and never leaves your Mac.

## What it does

| Area | What you get |
|---|---|
| 🧠 **Memory** | Preferences, conventions and decisions, saved once and shared by every AI |
| 👥 **Team sync** | Share memories with teammates through a git repo; personal ones stay private ([how to set it up](#team-sync)) |
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

Docker on a Mac can't use the GPU, so the MLX server runs natively. **MLX is optional:** without
it (Intel Mac, not installed, or `MLX=off` in `.env`) everything still works — code search and
file search use names/keywords, the local Explain AI is hidden, and the dashboard shows
"MLX off" instead of an error. Install it later and the meaning vectors are added automatically.

## Quick start

```bash
cd ~/Documents/ai-memory
./setup.sh                         # install / repair / update — safe to re-run
open http://localhost:8765/ui      # dashboard (automatic login)
curl -s localhost:8765/health      # → ok
```

Full install steps: [SETUP.md](SETUP.md). Needs macOS on Apple Silicon, Docker, `uv`, git and
python3. Jira, ZCode, and Node 24 + pnpm (only for dashboard development) are optional.

## First prompts

After `./setup.sh`, **restart** your AI tools (Claude Code, ZCode, Codex, your IDE) so they load
ai-memory. Then open a chat **in your project folder** and try these, in this order:

| # | Type this | What should happen |
|---|---|---|
| 1 | *"Use ai-memory: what tools do you have?"* | It lists `remember`, `recall`, `task_start`, `code_search`, … → connected |
| 2 | *"Remember: answer me in short, simple English. This is private."* | Saved as **private**: only on your Mac, and every AI tool now knows it |
| 3 | *"Remember for project my-app: we use pnpm, never npm."* | Saved as a **shared** project fact (teammates get it when team sync is on) |
| 4 | *"What do you remember about this project?"* | It calls `recall` and shows what it knows |
| 5 | *"Where is the login form validated?"* | `code_search` finds the code (by meaning with MLX, by names without it) |

**Start a ticket** — put the ticket key in the branch name, then open a **new** chat:

```bash
git checkout -b feature-PROJ-123
```

The AI starts with a `<task-journal>` briefing (Jira story, current state, next steps), so you
don't need to explain the task. A good first prompt:

> *"Summarise the story and the acceptance criteria, then plan the work."*

**While you work**

| When | Say |
|---|---|
| You chose X over Y | *"Decision: we sort on the client because the API has no sort parameter"* |
| Before you stop or switch ticket | *"Checkpoint"* |
| The ticket is finished | *"Final checkpoint and mark it done"* |
| Code you don't understand | *"What is this code and which story is it from?"* + paste the code |

More phrases, each with a **Copy** button: the dashboard **Cheat sheet** tab.

**The AI doesn't use ai-memory?**

| Check | Fix |
|---|---|
| `curl -s localhost:8765/health` doesn't say `ok` | Start it: `cd ~/Documents/ai-memory && docker compose up -d` |
| The tool was open during setup | Restart the AI tool |
| Claude Code: `claude mcp list` doesn't show `ai-memory: … ✔ Connected` | Re-run `./setup.sh` |
| It is connected but never calls `recall` | Re-run `./setup.sh` (it adds the usage instructions to `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md` and `~/.qwen/QWEN.md`), or start your prompt with *"Use ai-memory: …"*. Junie: paste `MEMORY_INSTRUCTIONS.md` into `.junie/guidelines.md` |
| You opened `http://localhost:8765/mcp` in the browser and it says *"not for the browser"* | Normal: that address is only for AI tools (they use your token). Your page is http://localhost:8765/ui |

## Team sync

Share what your AI tools learned with your teammates. Each person keeps their own ai-memory;
a shared git repo in the middle carries the **shared** memories, one folder per person.

```
 your ai-memory                     team git repo                       teammate's ai-memory
 shared memories ──push──►  memories/alice/<id>.json  ──pull──►  "teammate=alice", read-only
 "teammate=bob"  ◄──pull──  memories/bob/<id>.json    ◄──push──  shared memories
 private memories ✗ never leave your Mac
```

**What is shared**

| | |
|---|---|
| ✅ Shared | Every memory, unless it is marked **private** |
| 🔒 Private | Personal things: how you like the AI to work, your tools, notes about you. The AI saves them with `private=true`; you can switch any memory with the **Shared / Private** button on the Memories tab |
| ⏳ 10-minute wait | New or edited memories wait 10 min before they are shared, so you can still make them private |
| 🚫 Never shared | Anything that looks like a password or token; task journals, Jira cache, code index |
| 👀 Teammates' memories | Read-only for you; they disappear when the owner deletes them or makes them private |

**Step 1 — Create the team repo (once, one person)**

Create an **empty** git repo, e.g. `ai-memory-team`, in Azure DevOps, GitHub or GitLab.

- Its `main` branch must accept **direct pushes** (no "pull request required" rule).
- Give every teammate **write** access to it.

**Step 2 — Create your token (each person)**

| Where the repo is | Token | Permission |
|---|---|---|
| Azure DevOps | User settings → **Personal access tokens** → New | **Code: Read & write** |
| GitHub | Settings → Developer settings → **Fine-grained tokens** → only the team repo | **Contents: Read and write** |

Copy the token right away: it is shown only once.

**Step 3 — Add 3 lines to your `.env` (each person)**

```bash
SYNC_REPO_URL=https://dev.azure.com/<org>/<project>/_git/ai-memory-team   # or https://github.com/<org>/ai-memory-team.git
SYNC_GIT_TOKEN=<your token>   # only here, never inside the URL, never in chat
SYNC_AUTHOR=<you>             # your folder name: lower-case and unique, e.g. alice
```

**Step 4 — Check what you share, then start**

1. Open http://localhost:8765/ui#memories → **Mine · shared**. Click **Shared** on anything personal to make it **Private**.
2. Restart so the new settings are read:
   ```bash
   docker compose up -d --force-recreate
   ```
3. The first sync runs after ~90 s (then every 30 min), or click **Sync now** on the Memories tab.

**Step 5 — Check it works**

- Dashboard **Health → Team sync** is green, e.g. *"mine: 20 shared, +20 pushed"*.
- The team repo has a folder `memories/<you>/` with one `.json` file per shared memory.

**If something is wrong** (the message is in **Health → Team sync**)

| Message | Fix |
|---|---|
| `Authentication failed` / `401` | Token is wrong, expired, or has no write permission: make a new one (Step 2) |
| `TF402455` / `protected branch` | The repo's `main` needs a pull request: allow direct pushes (Step 1) |
| `not found` / `404` | Wrong URL (a space is written `%20`) or you have no access to the repo |
| `SYNC_AUTHOR must be set` | Add `SYNC_AUTHOR`: lower-case letters, digits, `.` `_` `-` |
| `off: SYNC_REPO_URL is not set` | Sync is off: add the Step 3 lines and restart |

Good to know: a deleted memory stays in the repo's **git history**. Settings you can change:
`SYNC_INTERVAL_MINUTES` (default 30, `0` = only the button), `SYNC_GRACE_MINUTES` (default 10),
`SYNC_BRANCH` (default `main`). More detail: [SETUP.md](SETUP.md#3a-team-memory-sync-optional).

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
