"""What the session hook (hooks/session-start.sh) gets from the server, besides the task briefing.

- memories_block: the project's newest memories as one-liners, injected at session start, so
  the AI has them without having to remember to call `recall`.
- import_memory_files: Claude Code's own memory files (~/.claude/projects/<dir>/memory/*.md),
  copied into ai-memory as private memories, so GLM, Codex and the others see them too.
  One memory store: files that change are updated, files that are deleted are removed.
- session_end: when a Claude Code session ends on a ticket branch, a short note of where it
  stopped goes into that task's journal, so the next session (any AI) can pick it up.
"""
import hashlib
import os
import re
import time
from contextlib import closing

import codesearch
import tasks

SESSION_MEMORIES = int(os.environ.get("SESSION_MEMORIES", "15") or 0)  # 0 = off
FILE_SOURCE = "claude-file"  # `source` of memories copied from Claude Code memory files
MAX_FILES = 200
MAX_FILE_CHARS = 20_000
MAX_NOTE_CHARS = 600


def _projects(project: str) -> list[str]:
    return codesearch.group_of(project) if project else []


def _one_line(text: str, width: int = 160) -> str:
    text = re.sub(r"\*\*|`|^\s*(#+|>)\s*", "", text, flags=re.M)  # markdown noise, keeps snake_case
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def memories_block(con, project: str, client: str) -> str:
    """The newest memories of this project (and its linked repos) plus global ones, one line each.

    client="claude": skip the ones copied from Claude Code's own memory files: Claude
    already has those in its context.
    """
    if SESSION_MEMORIES <= 0:
        return ""
    ps = _projects(project)
    where = (f"(project IN ({','.join('?' * len(ps))}) OR project='')" if ps else "project=''")
    args: list = list(ps)
    if client == "claude":
        where += " AND source != ?"
        args.append(FILE_SOURCE)
    total = con.execute(f"SELECT count(*) FROM memories WHERE {where}", args).fetchone()[0]
    rows = con.execute(f"SELECT id, content, author FROM memories WHERE {where} "
                       "ORDER BY updated_at DESC LIMIT ?", args + [SESSION_MEMORIES]).fetchall()
    if not rows:
        return ""
    lines = [f"- [{r['id']}] {_one_line(r['content'])}"
             + (f" _(teammate {r['author']}, read-only)_" if r["author"] else "") for r in rows]
    more = (f" Call `recall(query, project=\"{project}\")` for the full text or the other {total - len(rows)}."
            if total > len(rows) else " Call `recall` for the full text.")
    return ("<ai-memory>\n"
            f"Saved memories for {project or 'all projects'} (newest {len(rows)} of {total}, shortened). "
            "Follow them; they are facts and conventions the user already gave you." + more
            + "\n" + "\n".join(lines) + "\n</ai-memory>")


# ---------------------------------------------------------------- Claude Code memory files
def _frontmatter(text: str) -> tuple[dict, str]:
    """`---\\nkey: value\\n---\\nbody` -> ({key: value}, body). Nested keys are flattened."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.strip().partition(":")
        if sep and v.strip():
            meta[k.strip()] = v.strip().strip("'\"")
    return meta, m.group(2)


def _uid(dir_key: str, name: str) -> str:
    # 32 hex like every other uid, so team sync accepts it if the user later shares the memory
    return hashlib.sha1(f"claude-file:{dir_key}/{name}".encode()).hexdigest()[:32]


def import_memory_files(db, payload: dict) -> str:
    """payload: {"project": repo name, "dir": memory folder name, "files": [{"name", "text"}]}.

    The full set of files of one folder: new ones are added, changed ones updated, and the
    ones no longer there are removed. Always private: they stay on this Mac.
    """
    project = str(payload.get("project") or "").strip()
    dir_key = str(payload.get("dir") or "").strip()
    files = payload.get("files") or []
    if not dir_key or not isinstance(files, list):
        return "nothing to import"
    dir_tag = "ccdir-" + hashlib.sha1(dir_key.encode()).hexdigest()[:8]
    now = time.time()
    seen, added, updated = set(), 0, 0
    with closing(db()) as con, con:
        for f in files[:MAX_FILES]:
            name = str((f or {}).get("name") or "")
            if not name.endswith(".md") or name == "MEMORY.md" or "/" in name:
                continue
            meta, body = _frontmatter(str(f.get("text") or "")[:MAX_FILE_CHARS])
            content = body.strip() or meta.get("description", "")
            if not content:
                continue
            uid = _uid(dir_key, name)
            seen.add(uid)
            tags = ",".join(x for x in ("claude-memory", meta.get("type", ""), dir_tag) if x)
            row = con.execute("SELECT id, content, tags, project FROM memories WHERE uid=?", (uid,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO memories(content,tags,project,source,created_at,updated_at,uid,private)"
                    " VALUES (?,?,?,?,?,?,?,1)", (content, tags, project, FILE_SOURCE, now, now, uid))
                added += 1
            elif (row["content"], row["tags"], row["project"]) != (content, tags, project):
                con.execute("UPDATE memories SET content=?, tags=?, project=?, updated_at=? WHERE id=?",
                            (content, tags, project, now, row["id"]))
                updated += 1
        gone = [r["id"] for r in con.execute(
            "SELECT id, uid FROM memories WHERE source=? AND (',' || tags || ',') LIKE ?",
            (FILE_SOURCE, f"%,{dir_tag},%")) if r["uid"] not in seen]
        for mid in gone:
            con.execute("DELETE FROM memories WHERE id=?", (mid,))
    return f"memory files: {added} added, {updated} updated, {len(gone)} removed"


# ---------------------------------------------------------------- session end
def _task_for(con, branch: str):
    k = tasks.key_from_branch(branch)
    if k:
        return tasks.get_task(con, k)
    if branch:
        return con.execute("SELECT * FROM tasks WHERE branch=? ORDER BY updated_at DESC LIMIT 1",
                           (branch,)).fetchone()
    return None


def session_end(db, payload: dict) -> str:
    """payload: {"branch", "note", "source"}. Adds "Session ended: <note>" to the task's journal."""
    branch = str(payload.get("branch") or "").strip()
    note = _one_line(str(payload.get("note") or ""), MAX_NOTE_CHARS)
    if not branch or not note:
        return "nothing to log"
    content = f"Session ended: {note}"
    with closing(db()) as con, con:
        t = _task_for(con, branch)
        if t is None:
            return "no task for this branch"
        last = con.execute("SELECT content FROM task_log WHERE task_key=? ORDER BY created_at DESC LIMIT 1",
                           (t["key"],)).fetchone()
        if last and last["content"] == content:
            return "already logged"
        now = time.time()
        con.execute("INSERT INTO task_log(task_key,kind,content,source,created_at) VALUES (?,?,?,?,?)",
                    (t["key"], "note", content, str(payload.get("source") or "session-end hook"), now))
        con.execute("UPDATE tasks SET updated_at=? WHERE key=?", (now, t["key"]))
    return f"logged for {t['key']}"
