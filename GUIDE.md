# ai-memory — Guide

A small server on your Mac that **remembers your work for every AI tool**. Claude Code and
GLM (ZCode) — plus Codex, Qwen, Junie — all connect to it, so when you switch task, switch AI,
or start a new chat, the AI **continues where you stopped** instead of starting from zero.

**New here? Install first → [SETUP.md](SETUP.md)** (one command: `./setup.sh`).

---

## 1. The problem it solves

An AI session is like a person with no long-term memory:

- every new chat starts **empty**;
- when a chat gets long, older parts are **compressed or forgotten**;
- Claude and GLM **don't share** anything with each other;
- the **reason** behind code (the Jira story, the decision) is never in the code itself.

**ai-memory is the AI's notebook** — outside the AI, shared by all of them, never lost.

---

## 2. The big picture

```
          YOU   (on branch feature-PROJ-123)
           │
 ┌─────────┴───────┐   ┌───────────────┐   ┌─────────────────────────┐
 │  Claude Code    │   │  GLM (ZCode)  │   │ Dashboard (your browser) │
 └─────────┬───────┘   └───────┬───────┘   └────────────┬────────────┘
           │ MCP + token       │ MCP + token            │ /ui (auto-login)
           └───────────────────┼────────────────────────┘
                               ▼
 ┌───────────────────────────────────────────────────────────────┐
 │ ai-memory  — Docker container, http://localhost:8765           │
 │                                                               │
 │  🧠 Memories       preferences, conventions, decisions         │
 │  📒 Task journal   what was done / decided / what's next       │
 │  🎫 Jira stories   cached, searchable, synced daily            │
 │  📐 Project rules  CLAUDE.md, guidelines, agent definitions    │
 │  🔎 Code search    by meaning + by name                        │
 │  🕰  Git history    file ⇄ commit ⇄ ticket, line-level blame    │
 │                                                               │
 │  ⏰ Jira sync 24 h · code + git every 10 min · backup Mon–Fri  │
 └──────────────┬──────────────────────────────┬─────────────────┘
                ▼                              ▼  "text → numbers", "explain"
     data/memory.db  (everything)    ┌───────────────────────────────────┐
     backups/        (daily copies)  │ MLX server — Mac GPU, :18765       │
                                     │ embeddings + local Qwen2.5-Coder   │
                                     │ macOS service, starts at login     │
                                     └───────────────────────────────────┘
```

**Why MLX runs outside Docker:** Docker on a Mac can't use the GPU. The GPU turns code and
questions into numbers (*embeddings*) and runs the local *explain* model; Docker stores the
results and does the fast maths. If MLX is off (not installed, Intel Mac, or `MLX=off`), everything
still works — code search matches by **names/keywords**, the Explain AI is hidden, and health
shows "MLX off" (grey, not an error). When MLX starts later, missing vectors are added automatically.

---

## 3. How one working day looks

```
 1. Open a session on branch feature-PROJ-123          (no "start task" needed)
        ▼
 2. Session hook runs automatically → ai-memory finds task PROJ-123
    first time on this branch? → creates the journal + fetches the Jira story itself
        ▼
 3. AI starts with a <task-journal> briefing:
      Jira story + acceptance criteria · current state · next steps · decisions · files
        ▼
 4. AI works and keeps the journal:
      task_log (each step) · task_log kind=decision (X over Y, and why) · task_checkpoint
        ▼
 5. Tomorrow — or in GLM instead of Claude — back to step 2. Nothing lost.
```

**The only habit:** before you stop or switch ticket, say *"checkpoint"* — so the next chat
knows exactly where you stopped. Branches without a ticket key (`main`, `develop`, `release-…`)
get no briefing; auto-journals are made only for `AUTO_TASK_PREFIXES` (default: `JIRA_SYNC_PROJECT`).

---

## 4. "Why is this code here?" — the explain flow

```
 code (selected, pasted, or found)
        ▼ 1 FIND      exact line match in your repo (whitespace-insensitive), else closest similar code
 PaymentTemplatesPanel/index.tsx  lines 294–296
        ▼ 2 BLAME     git blame on exactly those lines → the commits that WROTE them
 "PROJ-123: cut template name at 64 characters on type and paste"
        ▼ 3 STORY     Jira PROJ-123 + acceptance criteria + decisions from the task journal
        ▼ 4 EXPLAIN   Claude / GLM — or the local AI on your GPU in the dashboard
 "What it does · Why it exists · Rule it implements"
```

Line-level blame means you get the story behind **your lines**, not every ticket that ever
touched the file. If those commits have no ticket key, it says so instead of guessing.

This is also why we don't *train* a model (LoRA) on the code: a trained model guesses and
must be re-trained after every change; this always reads the real, current sources.

---

## 5. What the AI can use (tools)

| Group | Tool | What it does |
|---|---|---|
| 🧠 Memory | `remember` / `recall` | Save / search facts, preferences, decisions |
| | `recent` / `update_memory` / `forget` | List / fix / delete |
| | `set_memory_private` | Keep a memory on this Mac, or share it with the team (`remember(private=true)` does it at save time) |
| 📒 Tasks | `task_start` | Start or reopen a ticket (fetches its Jira story) |
| | `task_log` | Add a step; `kind="decision"` for choices + reasons |
| | `task_checkpoint` | Save where you are + next steps (before switching) |
| | `task_resume` | Full briefing by key or branch |
| | `task_list` / `task_set_status` | List / pause, finish, reopen |
| 🎫 Jira | `jira_story` / `search_stories` | One story / search all cached stories |
| | `jira_sync` | Collect now (normally automatic) |
| 📐 Rules | `project_rules` | Architecture & style rules by topic, e.g. `"sx spacing"` |
| 🔎 Code | `code_search` | By meaning or name; each result shows the tickets behind the file |
| | `find_files` | By file **name / path** (fuzzy, like "Go to file") with the tickets behind each file |
| | `explain_snippet` | **Pasted code** → where it lives + commits that wrote it + story |
| | `explain_code` | A file or query → code + commits + stories + decisions |
| | `file_history` | Who changed a file, when, why |
| | `code_index` / `code_index_status` | Re-index now / index state |

You don't call these yourself — **ask the AI in plain words**:

> *"Start task PROJ-142"* · *"Checkpoint, I'm switching task"* · *"Project rules for forms?"* ·
> *"Find stories about FX rates"* · *"Where are templates saved?"* ·
> *"What is this code and which story is it from?"* + paste the code

**Search quality** (40 real questions from commit history, "right file in the top 5"):
hybrid search **92%** (meaning-only search: 72%), ~0.1 s per search.

---

## 6. Dashboard — http://localhost:8765/ui

Same server, same port, **logged in automatically** (nothing to paste).

| Tab | What you see / do |
|---|---|
| **Overview** | **Health** (8 checks: server, database, MLX GPU, Jira, code index, backups, errors, disk) and **Running jobs** with a **Stop** button each (re-index, Jira sync, backup, AI explanation; stops safely between steps, saved work is kept, the next run continues). Then counts; schedules with **Sync now / Re-index / Backup now**; charts |
| **Jira** | Search & filter cached issues → story + the commits that implemented it |
| **Code** | Toggle **Code** (by meaning or name) / **Files** (by file path, like "Go to file": name, folder, several words, initials like `ptp` = PaymentTemplatesPanel). Results show the "Why:" tickets; open a file to read it with line numbers and recent commits. **Select lines** anywhere → **Explain N lines** → location, git blame, story and a **local AI** explanation. Also **Explain this**, **Explain file**, **Paste code** |
| **Memories** | Browse / search saved facts · filter Mine shared / Mine private / From team · **Shared / Private** button per memory · **Team sync** card with **Sync now** |
| **Backups** | Files, next run, restore steps |
| **Logs** | Live server log (AI tool calls, jobs, errors) + MLX GPU log, filterable |
| **Cheat sheet** | What to say to Claude/GLM at each step — start, while working, switch, end of day, finish, plus Jira, code and memory phrases — each with a **Copy** button |

Header: **server · MLX · Jira** health with ✓ / ! / ✕, plus an overall **all healthy / N issues** pill (hover for details, shows running jobs), live refresh, light/dark.

The GPU is shared fairly: a newer *Explain* replaces an older unfinished one, generation hands the GPU back after every word (code search never waits behind it), and closing the Explain window stops generation.

**Local explain model:** Qwen2.5-Coder-7B (4-bit, ~4 GB). Loads on the first *Explain*
(~5 s), answers in ~10 s, and frees its memory after 10 minutes without use.

**Why automatic login is safe:** only requests addressed to `localhost`/`127.0.0.1` are served
(blocks DNS-rebinding sites); the login cookie is `HttpOnly` + `SameSite=Strict` (other sites
can't send it, scripts can't read it); buttons check the request comes from the page (CSRF);
the cookie is *derived* from the token and opens only the dashboard API — never the AI endpoints.

**Built with** React 19 + TypeScript + Vite + TanStack Query (`web/`). Docker compiles it; no
Node runs in production; nothing loads from the internet. To change the UI:

```bash
cd ~/Documents/ai-memory/web && pnpm install
pnpm dev                        # http://localhost:5180/ui (API proxied; paste the token once here)
pnpm typecheck && pnpm build
cd .. && docker compose up -d --build
```

---

## 7. Commands

> Run from the project folder: `cd ~/Documents/ai-memory`

### 7.1 Start · stop · status

| What | Command |
|---|---|
| Start / apply changes | `docker compose up -d` |
| Stop | `docker compose stop` |
| Restart | `docker compose restart` |
| Rebuild after code changes | `docker compose up -d --build` |
| Running? | `docker ps --filter name=ai-memory` |
| Health (`ok`) | `curl -s http://localhost:8765/health` |

**Auto-start:** ai-memory starts whenever Docker starts. Only if **you** stopped it,
start it once with `docker compose up -d`. Data is never lost by stopping — it's in `data/`.

### 7.2 Logs

| What | Command |
|---|---|
| Last 30 lines | `docker logs --tail 30 ai-memory` |
| Follow live | `docker logs -f ai-memory` |
| Background jobs | `docker logs ai-memory 2>&1 \| grep -E 'auto-sync\|auto-backup\|auto-index\|git history'` |
| MLX server | `tail -30 ~/Library/Logs/mlx-embed.log` |

(Or the dashboard's **Logs** tab.)

### 7.3 Data

| What | Command |
|---|---|
| Counts | `sqlite3 data/memory.db "select (select count(*) from memories),(select count(*) from tasks),(select count(*) from jira_stories),(select count(*) from code_chunks);"` |
| Tasks | `sqlite3 -header -column data/memory.db "select key, status, title from tasks;"` |
| Sync state | `sqlite3 data/memory.db "select * from sync_state;"` |

### 7.4 Backups

Automatic **Mon–Fri 10:00** local time (missed → runs when the server is back, same day);
newest **30** kept in `backups/`. Manual: dashboard **Backup now**, or
`sqlite3 data/memory.db ".backup 'backups/manual-$(date +%Y%m%d-%H%M).db'"`.

**Restore:**
```bash
cd ~/Documents/ai-memory
docker compose stop
cp data/memory.db data/memory-before-restore.db
cp backups/<file>.db data/memory.db
rm -f data/memory.db-wal data/memory.db-shm
docker compose up -d
```

### 7.5 MLX server (GPU)

A macOS service (`com.aimemory.mlx-embed`): starts at login, restarts on crash, runs from
`~/.local/share/mlx-embed/` (macOS doesn't let background services read Desktop/Documents).

**Memory rules** (the GPU shares the Mac's RAM with your apps and other local models):

| Rule | Why |
|---|---|
| MLX buffer cache limited to 512 MB (`MLX_CACHE_LIMIT_MB`) | Without a limit it grew to 16+ GB over days of indexing, filled the swap and the server got stuck |
| The Explain model (~4.5 GB) loads only when the Mac has 6 GB free (`LLM_MIN_FREE_GB`) | Otherwise Explain says "Not enough free memory…" instead of pushing the Mac into swap |
| Watchdog in `run.sh`: no answer to `/health` 3 times in a row → restart | launchd restarts a crashed server, not a stuck one; this restarts it within ~4 min |
| Health shows the server's memory and the Mac's free memory | "Mac … memory free" turns into a warning below 10% |

Set `MLX_CACHE_LIMIT_MB` / `LLM_MIN_FREE_GB` / `WATCHDOG_EVERY` / `WATCHDOG_FAILS` in the
LaunchAgent's `EnvironmentVariables` if you need other values. Other local model servers
(e.g. IDE agents running their own `mlx_lm.server`) use the same RAM: stop the ones you don't use.

| What | Command |
|---|---|
| Up? | `curl -s http://127.0.0.1:18765/health` |
| Service state | `launchctl print gui/$(id -u)/com.aimemory.mlx-embed \| grep -E 'state\|pid'` |
| Restart | `launchctl kickstart -k gui/$(id -u)/com.aimemory.mlx-embed` |
| Install / update / repair | `scripts/install-mlx.sh` |
| Turn auto-start off | `launchctl bootout gui/$(id -u)/com.aimemory.mlx-embed` |

The source is `mlx-server/server.py` and `mlx-server/run.sh` in this project; after editing them,
re-run `scripts/install-mlx.sh` (or copy both to `~/.local/share/mlx-embed/` and run the Restart command).

### 7.6 Code search & repos

Every **10 min** only changed files are re-embedded and new commits read. Add a repo
(e.g. the backend): clone it into `PROJECTS_DIR`, add it to `CODE_PROJECTS` in `.env`,
run `docker compose up -d`. Search it with `project="<Name>"`. C#/Java/Kotlin are chunked by
method. Full re-index: ask the AI *"run code_index with force"* (~7 min on the GPU).

### 7.7 Session hooks (auto briefing)

| What | Command |
|---|---|
| What Claude receives | `echo "{\"cwd\":\"$HOME/Desktop/projects/my-app\"}" \| hooks/session-start.sh` |
| What GLM receives | same, with `--json` at the end |

Empty output = not a git branch, server down, or nothing to say (never an error).

---

## 8. AI tools — how each connects

| Tool | Connection | Auto briefing | Instructions |
|---|---|---|---|
| Claude Code | `claude mcp` → `ai-memory` (HTTP + token) | hook in `~/.claude/settings.json` | `~/.claude/CLAUDE.md` block |
| GLM / ZCode | `~/.zcode/cli/config.json` → `ai-memory` | `<repo>/.zcode/config.json` (approve once in ZCode) | `<repo>/AGENTS.md` block |
| Codex | `~/.codex/config.toml` (stdio via docker exec) | — | `~/.codex/AGENTS.md` block |
| Qwen Code | `~/.qwen/settings.json` (HTTP) | — | `~/.qwen/QWEN.md` block |
| Junie | `~/.junie/mcp/mcp.json` (stdio) | — | paste into `.junie/guidelines.md` |

All written by `setup.sh` (backups `*.bak-aimemory`). Check GLM: ask it *"call ai-memory
task_list"*.

---

## 9. Files

```
ai-memory/
├── setup.sh                one-command setup for a developer Mac   → SETUP.md
├── .env.example            every setting, no secrets (copy → .env)
├── memory_server.py        server: memories, auth, routes, starts background jobs
├── tasks.py                task journal + briefing
├── jira.py                 Jira fetch / cache / search / daily sync
├── rules.py                project rules search (repos mounted read-only)
├── codesearch.py           code search: smart chunks, MLX vectors, hybrid ranking
├── githistory.py           file ⇄ commit ⇄ ticket, explain_code, file_history
├── snippet.py              pasted code → locate + line blame + stories (explain_snippet)
├── backup.py               weekday backups
├── dashboard.py            dashboard API, serves the web app, log capture
├── web/                    dashboard: React 19 + TypeScript + Vite + TanStack Query
├── mlx-server/             GPU server source (embeddings + local explain model)
├── scripts/install-mlx.sh  installs MLX server as a macOS service
├── hooks/                  session-start.sh (+ zcode-config.example.json)
├── MEMORY_INSTRUCTIONS.md  how AIs should use ai-memory (copied into CLAUDE.md etc.)
├── Dockerfile · docker-compose.yml
├── data/memory.db          ⭐ all your data            (personal, gitignored)
├── backups/                ⭐ daily copies              (personal, gitignored)
└── .env                    🔒 your tokens and paths      (personal, gitignored)
```

---

## 10. Settings (`.env`)

Edit `.env`, then `docker compose up -d`. Full list with comments: `.env.example`.

| Setting | Default | Meaning |
|---|---|---|
| `MEMORY_TOKEN` | generated | Protects the server (AI tools send it) |
| `PROJECTS_DIR` | `~/Desktop/projects` | Folder with your repos (read-only) |
| `CODE_PROJECTS` | — | Repos to index, comma-separated |
| `MLX` | `auto` | `auto` = use the MLX GPU server when it runs; `off` = never (keyword code search, no Explain AI) |
| `JIRA_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` | — | Your Jira access (optional) |
| `JIRA_SYNC_PROJECT` / `_DAYS` / `_INTERVAL_HOURS` / `_TYPES` | — (no daily sync) / `90` / `24` / business types | What and how often to sync |
| `AUTO_TASK_PREFIXES` | = `JIRA_SYNC_PROJECT` | Ticket prefixes that get a journal automatically from the branch (`*` = any) |
| `CODE_REINDEX_SECONDS` | `600` | Code + git refresh (`0` = off) |
| `CODE_TEST_PENALTY` | `0.5` | Test files rank lower unless you ask about tests |
| `BACKUP_HOUR` / `_WEEKDAYS` / `_KEEP` / `_ENABLED` | `10` / Mon–Fri / `30` / on | Backups |

MLX server (environment of the service): `MCS_LLM_MODEL` (default Qwen2.5-Coder-7B-Instruct-4bit),
`LLM_IDLE_SECONDS` (600).

---

## 11. Problems → fixes

| You see | Do this |
|---|---|
| AI says ai-memory tools are missing | `curl -s localhost:8765/health`; if not `ok`: `docker compose up -d`; restart the AI app |
| Docker paused / quit | Start / unpause Docker; ai-memory comes back by itself |
| "keyword results only" / **MLX down** in the dashboard | `scripts/install-mlx.sh` (repairs + restarts), then `tail ~/Library/Logs/mlx-embed.log` |
| Explain says "local model unavailable" | Same as above (MLX server down) |
| No `<task-journal>` at session start | Not on a git branch? Test the hook (7.7). ZCode: hook approved? |
| No briefing on my ticket branch | Branch name must contain the key (e.g. `feature-PROJ-142`) with a prefix from `AUTO_TASK_PREFIXES` |
| Story outdated | *"jira_story PROJ-… with refresh"* or dashboard **Sync now** |
| `401 unauthorized` in an AI tool | Its token ≠ `MEMORY_TOKEN` in `.env` → re-run `./setup.sh` |
| New code not found | Wait ≤ 10 min, or dashboard **Re-index** |
| Something broke after a change | Restore a backup (7.4) |

---

## 12. Safety

- Reachable only from **this Mac**; AI tools need the token; the dashboard uses a safe cookie.
- Repos are mounted **read-only** — ai-memory never changes your code.
- `.env`, `data/`, `backups/` are personal and gitignored — never commit or share them.
- Data stays on your Mac. What an AI reads from it goes to that AI's provider, like any chat;
  the dashboard's *Explain* runs **fully offline** on your GPU.
