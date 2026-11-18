# ai-memory — Setup for a new developer

**Time:** ~15 minutes (most of it is downloads). **Result:** your AI tools (Claude Code, GLM/ZCode,
Codex, Qwen, Junie) share one memory with your tasks, Jira stories, project rules and code search,
plus a dashboard at http://localhost:8765/ui.

> Everything runs **on your own Mac**. Each developer has their **own** server, data and tokens —
> nothing is shared between developers except the code of this project.

---

## 1. What you need to install

| # | What | Why | Install |
|---|---|---|---|
| 1 | **Mac with Apple Silicon** (M1–M4), 16 GB+ RAM | The GPU runs code search and the local "explain" AI | — |
| 2 | **Docker** (OrbStack or Docker Desktop) | Runs the ai-memory server | `brew install --cask orbstack` (recommended) or `brew install --cask docker` — then **start it** |
| 3 | **uv** | Installs `mlx-lm` (Apple's GPU ML library) for the MLX server | `brew install uv` (setup does this for you if Homebrew exists) |
| 4 | **git**, **python3**, **curl**, **openssl** | Used by setup and the server | Already on macOS (`xcode-select --install` if git is missing) |
| 5 | **Claude Code** CLI | Main AI client; gets the session hook | https://claude.com/claude-code |
| 6 | Your **repos** cloned into one folder | ai-memory reads them **read-only** | e.g. `~/Desktop/projects/my-app` |
| 7 | *(optional)* **Jira API token** | Stories, daily sync, the "why" behind code | https://id.atlassian.com/manage-profile/security/api-tokens |
| 8 | *(optional)* **ZCode** (GLM) | Second AI client | from Z.ai |
| 9 | *(optional)* **Node 24 + pnpm** | **Only** to change the dashboard UI. Docker builds it otherwise | `brew install node@24 pnpm` |

**Downloads on first run:** the embedding model (~350 MB) at setup, the local "explain" model
Qwen2.5-Coder-7B (~4 GB) the first time you click *Explain*. Both live in `~/.cache/huggingface`.

---

## 2. Install (one command)

```bash
cd ~/Documents
git clone <ai-memory repo URL> ai-memory     # or copy the folder (see "Sharing" below)
cd ai-memory
./setup.sh
```

`setup.sh` asks 3–4 questions (repos folder, which repos to index, optional Jira login) and then:

```
 1 checks prerequisites          → stops with clear instructions if Docker etc. is missing
 2 creates .env                  → your own MEMORY_TOKEN, repos, Jira (file is private, chmod 600)
 3 installs the MLX GPU server   → macOS service, starts at login, restarts on crash
 4 builds + starts ai-memory     → Docker container on http://localhost:8765
 5 connects Claude Code          → MCP server + session hook (auto task briefing)
 6 connects Codex, Qwen, Junie   → if installed
 7 connects GLM / ZCode          → if installed
 8 adds usage instructions       → ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.qwen/QWEN.md
 9 prints what's left            → restart your AI apps
```

Safe to re-run any time. Every config it touches is backed up first (`*.bak-aimemory`).

### After setup

1. **Restart** Claude Code, ZCode, Codex, JetBrains IDE so they load the server.
2. Open **http://localhost:8765/ui** — you're logged in automatically.
3. Wait ~10 min: the code index builds itself (first time ~7 min on the GPU); Jira syncs.
4. **GLM auto-briefing** (optional, per repo where `.zcode/` is gitignored): setup prints the
   exact command; then approve the hook in ZCode once.
5. **Junie:** paste `MEMORY_INSTRUCTIONS.md` into each project's `.junie/guidelines.md`.

### Check it works

```bash
curl -s http://localhost:8765/health                                   # → ok
curl -s http://127.0.0.1:18765/health                                  # → MLX server json
docker logs ai-memory 2>&1 | grep -E 'auto-index|auto-sync|auto-backup'
```

In Claude Code, ask: *"Use ai-memory: what tools do you have?"* — it should list `task_start`,
`code_search`, `explain_snippet`, …

---

## 3. What is per developer (never shared)

| File / place | Contains |
|---|---|
| `.env` | your `MEMORY_TOKEN`, **your Jira token**, your sync token and name, your paths |
| `data/memory.db` | your memories, task journals, Jira cache, code index |
| `backups/` | copies of your `memory.db` |
| `~/.local/share/mlx-embed/` | installed MLX server (copied from `mlx-server/`) |
| `~/Library/LaunchAgents/com.aimemory.mlx-embed.plist` | the MLX auto-start service |

All of these are created by `setup.sh` and are in `.gitignore`.

---

## 3a. Team memory sync (optional)

Share memories with teammates. Each person keeps their own ai-memory; a git repo in the
middle carries one small JSON file per shared memory, one folder per person.

```
your ai-memory ──push──► memories/alice/*.json ──pull──► teammate's ai-memory ("by alice", read-only)
               ◄──pull── memories/bob/*.json    ◄──push──
```

| Rule | Detail |
|---|---|
| Shared by default | Every memory is shared, except the ones marked **private** |
| Private | Personal ones (your preferences, your tools, notes about you): `remember(..., private=true)`, or the **Shared / Private** button on the dashboard Memories tab |
| Grace period | New or edited memories wait 10 min before they are shared, so you can still mark them private |
| Secrets | Text that looks like a token/password is never shared (and shouldn't be saved at all) |
| Teammates' memories | Read-only on your Mac; they disappear when the owner deletes them or makes them private |
| Only memories | Task journals, Jira cache and code index stay on each Mac |
| History | Removing a memory removes its file, but it stays in the repo's git history |

**Set up (once per team):** create an empty git repo `ai-memory-team` in Azure DevOps
(or GitHub / GitLab). Its `main` branch must accept **direct pushes**: no
"require a pull request / reviewers" branch policy on this repo. Teammates need **Contribute**
permission on it. Sync uses HTTPS + a token (the container has no SSH keys).

**Set up (each person):**

1. Create an Azure DevOps **Personal Access Token** with scope **Code: Read & write**.
2. Add to `.env`:
   ```bash
   SYNC_REPO_URL=https://dev.azure.com/<org>/<project>/_git/ai-memory-team
   SYNC_GIT_TOKEN=<your PAT>      # only here, never in the URL
   SYNC_AUTHOR=<you>              # unique per person, lower-case, e.g. alice
   ```
3. Review your memories in the dashboard (**Memories → Mine · shared**) and mark personal ones **Private**.
4. `docker compose up -d` — sync runs 90 s after start, then every 30 min. **Sync now** on the
   Memories tab or Overview runs it at once. Status: dashboard **Health → Team sync**.

| Health → Team sync says | Fix |
|---|---|
| `Authentication failed` / `401` | Token wrong, expired, or missing the **Code: Read & write** scope |
| `TF402455: Pushes to this branch are not permitted` | A branch policy on `main`: remove it for this repo |
| `not found` / `404` | Wrong URL (a space in the project name is `%20`) or no access to the repo |
| `SYNC_AUTHOR must be set` | Set a lower-case name, unique per person |

---

## 4. Sharing this project with the team

ai-memory is a git repo. `.gitignore` already keeps secrets and personal data out. Before
each commit, check the list:

```bash
cd ~/Documents/ai-memory
git status    # check: no .env, data/, backups/ in the list
```

**Never commit**: `.env`, `.env.*` (except `.env.example`), `data/`, `backups/`, `backup-*/`.

---

## 5. Requirements in short

```
macOS + Apple Silicon ── Docker ── uv (mlx-lm) ── git/python3 ── Claude Code
                                  optional: Jira token · ZCode · Node 24 + pnpm (UI dev only)
```

| Not available? | What still works |
|---|---|
| Intel Mac / no MLX | Everything except "meaning" code search and the local *Explain* AI: code and file search use names/keywords, the Explain AI card is hidden, health shows "MLX off". `setup.sh` sets `MLX=off` on Intel Macs |
| No Jira token | Everything except stories and the Jira "why" (git commit messages still give reasons) |
| No Claude Code | Use any other MCP client (GLM, Codex, Qwen) |

---

## 6. Uninstall

```bash
cd ~/Documents/ai-memory && docker compose down          # stop (data kept in ./data)
launchctl bootout gui/$(id -u)/com.aimemory.mlx-embed     # stop MLX auto-start
rm ~/Library/LaunchAgents/com.aimemory.mlx-embed.plist
claude mcp remove ai-memory --scope user
```
Then remove the `ai-memory` blocks from `~/.claude/CLAUDE.md` / `settings.json` (backups:
`*.bak-aimemory`).
