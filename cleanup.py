"""Clean-up view: what in the memory store probably needs a look.

- Never recalled: memories `recall` has not returned once since tracking started (and older than
  NEVER_RECALLED_DAYS), so they may be noise. Counted in memory_stats, a separate table, so a
  recall never changes a memory's updated_at (team sync uses it to spot edits).
- Possible duplicates: pairs where most words of the shorter memory are in the other one.
- Stale tasks: tasks still "active" with no journal entry for STALE_TASK_DAYS.

Nothing here deletes or changes anything by itself; the dashboard asks first.
"""
import os
import re
import time
from contextlib import closing

import codesearch
import tasks

STALE_TASK_DAYS = int(os.environ.get("STALE_TASK_DAYS", "7") or 7)
NEVER_RECALLED_DAYS = int(os.environ.get("NEVER_RECALLED_DAYS", "14") or 14)
# Two memories are "possible duplicates" when most words of the shorter one are in the other
# (rewordings of one fact are common: an AI saves it again in its own words) and they are not
# just two short notes on the same topic (Jaccard floor).
DUP_OVERLAP = 0.75
DUP_JACCARD = 0.25
FILE_SOURCE = "claude-file"  # copied from Claude Code memory files (session_hooks.py)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_stats(
  id INTEGER PRIMARY KEY,          -- memories.id
  recalls INTEGER NOT NULL DEFAULT 0,
  last_recalled REAL
);
"""
_STOP = set("the and for with that this from into are was were has have not but its it's use uses used "
            "when then than also only all any can will should must you your our they them their"
            " via per one two new old".split())


def init(db) -> None:
    with closing(db()) as con, con:
        con.executescript(SCHEMA)
        con.execute("INSERT OR IGNORE INTO sync_state(name, value) VALUES ('recall_tracking_since', ?)",
                    (str(time.time()),))


def note_recalls(con, ids) -> None:
    """Count one recall for each memory `recall` returned (call inside the recall's connection)."""
    now = time.time()
    con.executemany("INSERT INTO memory_stats(id, recalls, last_recalled) VALUES (?, 1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET recalls = recalls + 1, last_recalled = excluded.last_recalled",
                    [(i, now) for i in ids])


def _words(text: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) > 2 and w not in _STOP)


def _mem(r) -> dict:
    return {"id": r["id"], "content": r["content"], "project": r["project"], "source": r["source"],
            "author": r["author"], "private": r["private"], "created_at": r["created_at"],
            "updated_at": r["updated_at"], "from_file": r["source"] == FILE_SOURCE}


def duplicates(rows, limit: int = 40) -> list[dict]:
    """Pairs of memories with mostly the same words. Same project (or a global one) only."""
    sets = {r["id"]: _words(r["content"]) for r in rows}
    by_id = {r["id"]: r for r in rows}
    index: dict[str, list[int]] = {}
    for i, ws in sets.items():
        for w in ws:
            index.setdefault(w, []).append(i)
    common = max(20, len(rows) // 5)  # words in this many memories don't make two of them alike
    shared: dict[tuple[int, int], int] = {}
    for ids in index.values():
        if len(ids) > common:
            continue
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                k = (ids[x], ids[y])
                shared[k] = shared.get(k, 0) + 1
    out = []
    for (a, b), n in shared.items():
        ra, rb = by_id[a], by_id[b]
        if ra["author"] and rb["author"]:
            continue  # both teammates': nothing to clean up here
        if ra["project"] and rb["project"] and ra["project"] != rb["project"] \
                and rb["project"] not in codesearch.group_of(ra["project"]):
            continue
        small = min(len(sets[a]), len(sets[b]))
        both = len(sets[a] & sets[b])
        if small < 5 or n < 3:
            continue
        score = both / small
        if score >= DUP_OVERLAP and both / len(sets[a] | sets[b]) >= DUP_JACCARD:
            out.append({"score": round(score, 2), "a": _mem(ra), "b": _mem(rb)})
    out.sort(key=lambda d: -d["score"])
    return out[:limit]


def report(db) -> dict:
    now = time.time()
    with closing(db()) as con:
        since = float(con.execute("SELECT value FROM sync_state WHERE name='recall_tracking_since'")
                      .fetchone()[0] or now)
        rows = con.execute("SELECT m.*, coalesce(s.recalls, 0) AS recalls, s.last_recalled "
                           "FROM memories m LEFT JOIN memory_stats s ON s.id = m.id").fetchall()
        cut = now - NEVER_RECALLED_DAYS * 86400
        never = [{**_mem(r), "recalls": 0} for r in rows
                 if not r["recalls"] and not r["author"] and r["created_at"] < cut]
        never.sort(key=lambda m: m["created_at"])
        stale = []
        for t in con.execute("SELECT * FROM tasks WHERE status='active' AND updated_at < ? ORDER BY updated_at",
                             (now - STALE_TASK_DAYS * 86400,)):
            last = con.execute("SELECT kind, content, created_at FROM task_log WHERE task_key=? "
                               "ORDER BY created_at DESC LIMIT 1", (t["key"],)).fetchone()
            stale.append({"key": t["key"], "title": t["title"], "project": t["project"], "branch": t["branch"],
                          "updated_at": t["updated_at"],
                          "last": dict(last) if last else None})
    tracked_days = (now - since) / 86400
    return {
        "tracking_since": since,
        # "never recalled" means little until recalls were counted for a while
        "tracking_ready": tracked_days >= min(NEVER_RECALLED_DAYS, 7),
        "never_recalled": never[:100],
        "never_recalled_total": len(never),
        "never_recalled_days": NEVER_RECALLED_DAYS,
        "duplicates": duplicates(rows),
        "stale_tasks": stale,
        "stale_task_days": STALE_TASK_DAYS,
        "total_memories": len(rows),
    }


def delete_memory(db, mid: int) -> tuple[bool, str]:
    """Dashboard delete: same rules as `forget`, plus memories copied from Claude Code's files
    (they come back at the next session start unless the file is deleted)."""
    with closing(db()) as con, con:
        r = con.execute("SELECT author, source, tags FROM memories WHERE id=?", (mid,)).fetchone()
        if not r:
            return False, f"No memory with id {mid}."
        if r["author"]:
            return False, f"[{mid}] belongs to {r['author']} (read-only here)."
        if r["source"] == FILE_SOURCE:
            return False, (f"[{mid}] is a copy of a Claude Code memory file (~/.claude/projects/…/memory). "
                           "Delete that file instead; the copy goes with it at the next session start.")
        con.execute("DELETE FROM memories WHERE id=?", (mid,))
        con.execute("DELETE FROM memory_stats WHERE id=?", (mid,))
    return True, f"Deleted [{mid}]."


def set_task_status(db, key: str, status: str) -> tuple[bool, str]:
    return tasks.set_status(db, key, status, "from the dashboard clean-up view", "dashboard")
