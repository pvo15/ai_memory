"""
Task journal: keeps the state of each task (e.g. Jira PROJ-123) OUTSIDE the AI,
so a new session -- or a different AI tool -- can continue where the last one stopped.

Two kinds of data per task:
  - current state  (tasks table)    summary / next_steps / files  -> OVERWRITTEN at each checkpoint
  - history        (task_log table) progress / decision / blocker -> APPEND-ONLY

`briefing()` merges both (plus anything other modules add, e.g. the Jira story)
into one text that is handed to the AI at the start of a session.
"""
import os
import re
import time
from contextlib import closing
from typing import Callable

KEY_RE = re.compile(r"([A-Za-z][A-Za-z0-9]+-\d+)")
# Branches whose ticket key starts with one of these get a journal automatically at session
# start (so "release-2026" never becomes a task). "*" = any key. Default: the synced Jira project.
AUTO_PREFIXES = {p.strip().upper() for p in os.environ.get(
    "AUTO_TASK_PREFIXES", os.environ.get("JIRA_SYNC_PROJECT", "")).split(",") if p.strip()}


def auto_start_allowed(key: str) -> bool:
    return bool(key) and ("*" in AUTO_PREFIXES or key.split("-", 1)[0] in AUTO_PREFIXES)


def auto_start(db, key: str, project: str, branch: str) -> bool:
    """Create the journal for `key` if it doesn't exist yet. Returns True if created."""
    now = time.time()
    with closing(db()) as con, con:
        if con.execute("SELECT 1 FROM tasks WHERE key=?", (key,)).fetchone():
            return False
        con.execute("INSERT INTO tasks(key,project,title,branch,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (key, project, "", branch, now, now))
        con.execute("INSERT INTO task_log(task_key,kind,content,source,created_at) VALUES (?,?,?,?,?)",
                    (key, "note", f"Task started automatically (session on branch {branch})", "auto", now))
    return True


def set_title_if_empty(db, key: str, title: str) -> None:
    if title:
        with closing(db()) as con, con:
            con.execute("UPDATE tasks SET title=? WHERE key=? AND title=''", (title, key))
LOG_KINDS = ("progress", "decision", "blocker", "note")

# Other modules (jira.py, ...) append functions here to add sections to the briefing.
# Each gets (con, task_row) and returns markdown text or None.
BriefingSection = Callable[..., "str | None"]
EXTRA_SECTIONS: list[BriefingSection] = []
# Called with the task key when a task is started (e.g. jira.py fetches the story).
ON_START: list[Callable[[str], object]] = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  key        TEXT PRIMARY KEY,               -- e.g. PROJ-123
  project    TEXT NOT NULL DEFAULT '',
  title      TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'active',  -- active | paused | done
  branch     TEXT NOT NULL DEFAULT '',
  summary    TEXT NOT NULL DEFAULT '',        -- current state, overwritten
  next_steps TEXT NOT NULL DEFAULT '',        -- overwritten
  files      TEXT NOT NULL DEFAULT '',        -- comma-separated, merged
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_log(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_key   TEXT NOT NULL,
  kind       TEXT NOT NULL,                   -- progress | decision | blocker | note | checkpoint
  content    TEXT NOT NULL,
  files      TEXT NOT NULL DEFAULT '',
  source     TEXT NOT NULL DEFAULT '',        -- which AI tool wrote it
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS task_log_by_task ON task_log(task_key, created_at);
"""


# ---------------------------------------------------------------- helpers
def norm_key(key: str) -> str:
    m = KEY_RE.search(key or "")
    return m.group(1).upper() if m else (key or "").strip().upper()


STATUSES = ("active", "paused", "done")


def set_status(db, key: str, status: str, note: str = "", source: str = "") -> tuple[bool, str]:
    """Set a task's status and log it. Shared by the MCP tool and the dashboard.
    Returns (ok, message)."""
    if status not in STATUSES:
        return False, "status must be active, paused or done."
    k = norm_key(key)
    now = time.time()
    with closing(db()) as con, con:
        cur = con.execute("UPDATE tasks SET status=?, updated_at=? WHERE key=?", (status, now, k))
        if not cur.rowcount:
            return False, f"No task {k}."
        con.execute("INSERT INTO task_log(task_key,kind,content,source,created_at) VALUES (?,?,?,?,?)",
                    (k, "note", f"Status → {status}" + (f": {note}" if note else ""), source, now))
    return True, f"{k} is now {status}."


def key_from_branch(branch: str) -> str:
    """feature-PROJ-123 / bugfix/proj-42-fix-x  ->  PROJ-123 / PROJ-42"""
    m = KEY_RE.search(branch or "")
    return m.group(1).upper() if m else ""


def merge_files(old: str, new: list[str] | None) -> str:
    files = [f for f in old.split(",") if f]
    for f in new or []:
        f = f.strip()
        if f and f not in files:
            files.append(f)
    return ",".join(files)


def ago(ts: float) -> str:
    s = int(time.time() - ts)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def get_task(con, key: str):
    return con.execute("SELECT * FROM tasks WHERE key=?", (norm_key(key),)).fetchone()


def resolve_task(con, key: str = "", branch: str = "", project: str = ""):
    """Find the task to resume: explicit key > key in branch name > latest active task in project."""
    k = norm_key(key) or key_from_branch(branch)
    if k:
        return get_task(con, k)
    if branch:
        row = con.execute("SELECT * FROM tasks WHERE branch=? ORDER BY updated_at DESC LIMIT 1",
                          (branch,)).fetchone()
        if row:
            return row
    sql, args = "SELECT * FROM tasks WHERE status='active'", []
    if project:
        sql += " AND project=?"
        args.append(project)
    return con.execute(sql + " ORDER BY updated_at DESC LIMIT 1", args).fetchone()


def briefing(con, t) -> str:
    """Everything an AI needs to continue this task, as compact markdown."""
    head = f"# Task {t['key']}" + (f" — {t['title']}" if t["title"] else "")
    meta = " · ".join(x for x in (t["status"], t["project"], t["branch"] and f"branch {t['branch']}",
                                  f"updated {ago(t['updated_at'])}") if x)
    out = [head, meta]

    for section in EXTRA_SECTIONS:  # e.g. the Jira story
        text = section(con, t)
        if text:
            out.append(text)

    out.append("## Current state\n" + (t["summary"] or "_No checkpoint yet._"))
    if t["next_steps"]:
        out.append("## Next steps\n" + t["next_steps"])

    decisions = con.execute(
        "SELECT * FROM task_log WHERE task_key=? AND kind='decision' ORDER BY created_at",
        (t["key"],)).fetchall()
    if decisions:
        out.append("## Decisions (keep these)\n" + "\n".join(
            f"- {d['content']}  _({d['source'] or '?'}, {ago(d['created_at'])})_" for d in decisions))

    recent = con.execute(
        "SELECT * FROM task_log WHERE task_key=? AND kind!='decision' "
        "ORDER BY created_at DESC LIMIT 12", (t["key"],)).fetchall()
    if recent:
        out.append("## Recent activity (newest first)\n" + "\n".join(
            f"- [{r['kind']}] {r['content']}  _({r['source'] or '?'}, {ago(r['created_at'])})_"
            for r in recent))

    if t["files"]:
        out.append("## Files touched\n" + "\n".join(f"- `{f}`" for f in t["files"].split(",")))

    out.append("_Log progress with `task_log`, decisions with kind=decision; "
               "call `task_checkpoint` before switching task or ending the session._")
    return "\n\n".join(out)


# ---------------------------------------------------------------- MCP tools
def register(mcp, db) -> None:
    """Add the task tools to the FastMCP server. `db` returns a sqlite connection."""
    with closing(db()) as con, con:
        con.executescript(SCHEMA)

    @mcp.tool()
    def task_start(key: str, project: str = "", title: str = "", branch: str = "",
                   source: str = "") -> str:
        """Start (or re-open) a task and get its briefing. Call when beginning work on a ticket.

        key: task id, usually the Jira key, e.g. "PROJ-123" (also accepts a branch name).
        project: repo folder name, e.g. "my-app".
        title: short human title of the task.
        branch: current git branch.
        source: your tool name, e.g. "claude-code", "glm".
        If the task already exists, its saved state is returned -- continue from it.
        """
        k = norm_key(key)
        if not k:
            return "A task key is required, e.g. PROJ-123."
        for fn in ON_START:  # e.g. fetch + cache the Jira story; never blocks the start
            try:
                fn(k)
            except Exception:
                pass
        now = time.time()
        with closing(db()) as con, con:
            t = get_task(con, k)
            if t:
                con.execute(
                    "UPDATE tasks SET status='active', updated_at=?,"
                    " project=COALESCE(NULLIF(?,''),project), title=COALESCE(NULLIF(?,''),title),"
                    " branch=COALESCE(NULLIF(?,''),branch) WHERE key=?",
                    (now, project, title, branch, k))
                prefix = "Resuming existing task.\n\n"
            else:
                con.execute("INSERT INTO tasks(key,project,title,branch,created_at,updated_at)"
                            " VALUES (?,?,?,?,?,?)", (k, project, title, branch, now, now))
                con.execute("INSERT INTO task_log(task_key,kind,content,source,created_at)"
                            " VALUES (?,?,?,?,?)", (k, "note", "Task started", source, now))
                prefix = "New task created.\n\n"
            return prefix + briefing(con, get_task(con, k))

    @mcp.tool()
    def task_log(key: str, content: str, kind: str = "progress", files: list[str] | None = None,
                 source: str = "") -> str:
        """Append one entry to a task's history. Call after each meaningful step.

        kind: "progress" (did something), "decision" (chose X over Y and why -- these are
              always shown in the briefing), "blocker" (stuck on / waiting for), "note".
        content: one or two self-contained sentences.
        files: repo-relative paths touched in this step.
        """
        k = norm_key(key)
        if kind not in LOG_KINDS:
            return f"kind must be one of {', '.join(LOG_KINDS)}."
        now = time.time()
        with closing(db()) as con, con:
            t = get_task(con, k)
            if not t:
                return f"No task {k}. Call task_start first."
            con.execute("INSERT INTO task_log(task_key,kind,content,files,source,created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (k, kind, content.strip(), ",".join(files or []), source, now))
            con.execute("UPDATE tasks SET files=?, updated_at=? WHERE key=?",
                        (merge_files(t["files"], files), now, k))
        return f"Logged {kind} for {k}."

    @mcp.tool()
    def task_checkpoint(key: str, summary: str, next_steps: str = "",
                        files: list[str] | None = None, source: str = "") -> str:
        """Save WHERE THE TASK STANDS NOW, so another session or AI can continue it.

        Call before switching to another task, before ending a session, or when your
        context is getting long. It replaces the previous summary/next steps.
        summary: what is done and what state the code is in (a few sentences or bullets).
        next_steps: concrete remaining steps, as a bullet list.
        """
        k = norm_key(key)
        now = time.time()
        with closing(db()) as con, con:
            t = get_task(con, k)
            if not t:
                return f"No task {k}. Call task_start first."
            con.execute("UPDATE tasks SET summary=?, next_steps=?, files=?, updated_at=? WHERE key=?",
                        (summary.strip(), next_steps.strip(), merge_files(t["files"], files), now, k))
            con.execute("INSERT INTO task_log(task_key,kind,content,source,created_at)"
                        " VALUES (?,?,?,?,?)", (k, "checkpoint", summary.strip()[:300], source, now))
        return f"Checkpoint saved for {k}."

    @mcp.tool()
    def task_resume(key: str = "", branch: str = "", project: str = "") -> str:
        """Get the briefing to CONTINUE a task: story, current state, next steps, decisions,
        recent activity, files. Call at the start of a session or after switching tasks.

        Give any of: key ("PROJ-123"), branch ("feature-PROJ-123"), or project
        (returns the most recently active task there).
        """
        with closing(db()) as con:
            t = resolve_task(con, key, branch, project)
            if t:
                return briefing(con, t)
            active = con.execute("SELECT key,title,project FROM tasks WHERE status='active' "
                                 "ORDER BY updated_at DESC LIMIT 10").fetchall()
        hint = "\n".join(f"- {a['key']} {a['title']} ({a['project']})" for a in active)
        return "No matching task. Start one with task_start." + (f"\nActive tasks:\n{hint}" if hint else "")

    @mcp.tool()
    def task_list(project: str = "", status: str = "active") -> str:
        """List tasks. status: active | paused | done | all."""
        sql, args = "SELECT * FROM tasks WHERE 1=1", []
        if status != "all":
            sql += " AND status=?"
            args.append(status)
        if project:
            sql += " AND project=?"
            args.append(project)
        with closing(db()) as con:
            rows = con.execute(sql + " ORDER BY updated_at DESC LIMIT 50", args).fetchall()
        if not rows:
            return "No tasks."
        return "\n".join(f"- {r['key']} [{r['status']}] {r['title']} ({r['project']}, "
                         f"updated {ago(r['updated_at'])})" for r in rows)

    @mcp.tool()
    def task_set_status(key: str, status: str, note: str = "", source: str = "") -> str:
        """Pause, finish or re-activate a task. status: active | paused | done."""
        return set_status(db, key, status, note, source)[1]
