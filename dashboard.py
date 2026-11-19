"""
Web dashboard: http://localhost:8765/ui  (same host/port as the MCP server).

- GET /ui            the page (static HTML, contains no data -> no token needed)
- GET /api/...       JSON data      -> needs `Authorization: Bearer <MEMORY_TOKEN>`
- POST /api/action/  run a job now  -> needs the token too
The page asks for the token once and keeps it in the browser (localStorage).

Logs: the server's own output (print + uvicorn) is copied into an in-memory ring
buffer (last 3000 lines) by `capture_logs()`, so the UI can show and filter it.
The MLX log is read from a read-only mount of ~/Library/Logs/mlx-embed.log.
"""
import collections
import json
import os
import re
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import backup
import codesearch
import githistory
import jira
import tasks

LOG = collections.deque(maxlen=3000)
# Successful polling by the dashboard itself / health checks would flood the viewer.
# Kept: AI tool calls (POST /mcp), dashboard actions (POST /api/action), all errors.
_NOISE = re.compile(r'"GET /(api/|health|ui|favicon\.ico)[^"]*" (200|204)')
STARTED = time.time()
MLX_LOG = os.environ.get("MLX_LOG", "/logs/mlx-embed.log")
WEB_DIR = os.environ.get("WEB_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))


# ---------------------------------------------------------------- log capture
class _Tee:
    """Write-through stream: keeps normal stdout/stderr AND copies lines to LOG."""

    def __init__(self, stream, name):
        self.stream, self.name, self.buf = stream, name, ""

    def write(self, s):
        self.stream.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip() and not _NOISE.search(line):
                LOG.append({"t": time.time(), "src": self.name, "line": line.rstrip()})
        return len(s)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, a):
        return getattr(self.stream, a)


def capture_logs() -> None:
    """Call once at HTTP server start (before uvicorn configures logging)."""
    sys.stdout = _Tee(sys.stdout, "out")
    sys.stderr = _Tee(sys.stderr, "err")


def level_of(line: str) -> str:
    l = line.lower()
    if re.search(r"\b(error|failed|traceback|exception)\b", l):
        return "error"
    if re.search(r"\b(warn|warning|down|retry)\b", l):
        return "warn"
    return "info"


# ---------------------------------------------------------------- data
def _q(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def _branch(project: str) -> str:
    """Current branch, read from .git/HEAD (no git process; the repo is mounted read-only)."""
    git = os.path.join(codesearch.REPOS_DIR, project, ".git")
    if os.path.isfile(git):
        return "(git worktree)"
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return ""
    return head[16:] if head.startswith("ref: refs/heads/") else f"detached {head[:8]}"


def connected_repos(db) -> list[dict]:
    """The repo folders ai-memory reads (CODE_PROJECTS), with index state and links."""
    import rules
    host_dir = (os.environ.get("PROJECTS_HOST_DIR") or os.environ.get("PROJECTS_DIR", "")).rstrip("/")
    out = []
    with closing(db()) as con:
        for p in codesearch.CODE_PROJECTS:
            found = os.path.isdir(os.path.join(codesearch.REPOS_DIR, p))
            files = _q(con, "SELECT count(*) FROM code_files WHERE project=?", p)
            chunks, vecs = con.execute("SELECT count(*), count(vec) FROM code_chunks WHERE project=?",
                                       (p,)).fetchone()
            commits, last = con.execute("SELECT count(*), max(date) FROM git_commits WHERE project=?",
                                        (p,)).fetchone()
            st = codesearch._status.get(p, {})
            out.append({
                "name": p,
                "path": f"{host_dir}/{p}" if host_dir else p,
                "found": found,
                "branch": _branch(p) if found else "",
                "files": files, "chunks": chunks, "vectors": vecs,
                "commits": commits, "last_commit": last,
                "rules_files": len(rules.rule_files(p)) if found else 0,
                "linked": [x for x in codesearch.group_of(p) if x != p],
                "index": {k: v for k, v in st.items() if k in ("state", "done", "total", "finished", "error")},
            })
    return out


def overview(db) -> dict:
    with closing(db()) as con:
        counts = {
            "memories": _q(con, "SELECT count(*) FROM memories"),
            "tasks_active": _q(con, "SELECT count(*) FROM tasks WHERE status='active'"),
            "tasks_total": _q(con, "SELECT count(*) FROM tasks"),
            "jira": _q(con, "SELECT count(*) FROM jira_stories"),
            "code_files": _q(con, "SELECT count(*) FROM code_files"),
            "code_chunks": _q(con, "SELECT count(*) FROM code_chunks"),
            "commits": _q(con, "SELECT count(*) FROM git_commits"),
            "commits_keyed": _q(con, "SELECT count(*) FROM git_commits WHERE keys != ''"),
        }
        state = {r["name"]: r["value"] for r in con.execute("SELECT name, value FROM sync_state")}
        jira_status = [dict(r) for r in con.execute(
            "SELECT status AS label, count(*) AS n FROM jira_stories GROUP BY status ORDER BY n DESC")]
        jira_type = [dict(r) for r in con.execute(
            "SELECT json_extract(raw,'$.fields.issuetype.name') AS label, count(*) AS n "
            "FROM jira_stories GROUP BY label ORDER BY n DESC")]
        week0 = time.time() - 12 * 7 * 86400
        weeks = {}
        for (d,) in con.execute("SELECT date FROM git_commits WHERE date >= ?", (week0,)):
            monday = datetime.fromtimestamp(d).date() - timedelta(days=datetime.fromtimestamp(d).weekday())
            weeks[monday.isoformat()] = weeks.get(monday.isoformat(), 0) + 1
        start = datetime.fromtimestamp(week0).date()
        start -= timedelta(days=start.weekday())
        commits_week = [{"label": (start + timedelta(weeks=i)).isoformat(),
                         "n": weeks.get((start + timedelta(weeks=i)).isoformat(), 0)} for i in range(13)]
        folders = {}
        for f, n in con.execute("SELECT file, count(*) FROM code_chunks GROUP BY file"):
            parts = f.split("/")
            key = "/".join(parts[:2]) if parts[0] == "src" and len(parts) > 2 else parts[0]
            folders[key] = folders.get(key, 0) + n
        code_folders = [{"label": k, "n": v} for k, v in sorted(folders.items(), key=lambda x: -x[1])[:10]]
        top_tickets = [dict(r) for r in con.execute(
            "SELECT g.keys AS label, count(*) AS n, max(s.summary) AS summary FROM git_commits g "
            "LEFT JOIN jira_stories s ON s.key = g.keys WHERE g.keys != '' AND instr(g.keys, ',') = 0 "
            "AND g.date >= ? GROUP BY g.keys ORDER BY n DESC LIMIT 8", (week0,))]
    db_size = os.path.getsize(os.environ.get("MEMORY_DB", "/data/memory.db"))
    now = datetime.now(backup.BACKUP_TZ)
    nxt = now.replace(hour=backup.BACKUP_HOUR, minute=0, second=0, microsecond=0)
    while nxt <= now or nxt.weekday() not in backup.BACKUP_DAYS or (
            nxt.date() == now.date() and os.path.exists(backup.path_for(now))):
        nxt = (nxt + timedelta(days=1)).replace(hour=backup.BACKUP_HOUR, minute=0)
    last_sync = float(state.get("jira_last_sync_at") or 0)
    return {
        "counts": counts,
        "server": {"uptime_s": int(time.time() - STARTED), "db_bytes": db_size},
        "mlx": {"up": codesearch.mlx_up(), "state": codesearch.mlx_state(db), "mode": codesearch.MLX_MODE,
                "url": codesearch.EMBED_URL},
        "jira": {"configured": jira.configured(), "last_sync": last_sync or None,
                 "last_result": state.get("jira_last_sync_result", ""),
                 "next_sync": (last_sync + jira.SYNC_INTERVAL_H * 3600) if last_sync and jira.SYNC_INTERVAL_H > 0 else None},
        "backup": {"next": nxt.timestamp(), "tz": backup.BACKUP_TZ.key,
                   "count": len([f for f in os.listdir(backup.BACKUP_DIR) if f.endswith(".db")])
                   if os.path.isdir(backup.BACKUP_DIR) else 0},
        "code": {"projects": codesearch.CODE_PROJECTS, "status": {
            p: {k: v for k, v in codesearch._status.get(p, {}).items() if k in
                ("state", "done", "total", "finished", "changed", "error")} for p in codesearch.CODE_PROJECTS}},
        "repos": connected_repos(db),
        "charts": {"jira_status": jira_status, "jira_type": jira_type, "commits_week": commits_week,
                   "code_folders": code_folders, "top_tickets": top_tickets},
    }


def list_jira(db, q: str, status: str, limit: int = 100) -> list:
    with closing(db()) as con:
        if q.strip():
            fq = jira.fts_query(q)
            rows = con.execute(
                "SELECT s.key, s.summary, s.status, s.fetched_at, json_extract(s.raw,'$.fields.issuetype.name') AS type, "
                "json_extract(s.raw,'$.fields.updated') AS updated FROM jira_fts JOIN jira_stories s ON s.rowid=jira_fts.rowid "
                "WHERE jira_fts MATCH ? ORDER BY bm25(jira_fts) LIMIT ?", (fq, limit)).fetchall() if fq else []
        else:
            sql = ("SELECT key, summary, status, fetched_at, json_extract(raw,'$.fields.issuetype.name') AS type, "
                   "json_extract(raw,'$.fields.updated') AS updated FROM jira_stories")
            args = []
            if status:
                sql += " WHERE status=?"
                args.append(status)
            rows = con.execute(sql + " ORDER BY updated DESC LIMIT ?", args + [limit]).fetchall()
    return [dict(r) for r in rows]


def jira_detail(db, key: str) -> dict:
    with closing(db()) as con:
        r = con.execute("SELECT key, summary, status, text, fetched_at FROM jira_stories WHERE key=?",
                        (tasks.norm_key(key),)).fetchone()
        commits = [dict(c) for c in con.execute(
            "SELECT hash, date, author, subject FROM git_commits WHERE ','||keys||',' LIKE ? "
            "ORDER BY date DESC LIMIT 30", (f"%,{tasks.norm_key(key)},%",))]
    return {"story": dict(r) if r else None, "commits": commits}


def list_memories(db, q: str) -> list:
    with closing(db()) as con:
        if q.strip():
            fq = jira.fts_query(q)
            rows = con.execute("SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                               "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT 100", (fq,)).fetchall() if fq else []
        else:
            rows = con.execute("SELECT * FROM memories ORDER BY updated_at DESC LIMIT 200").fetchall()
    import sync
    return [{**dict(r), "looks_secret": bool(sync.SECRET_RE.search(r["content"]))} for r in rows]


def set_private(db, mid: int, private: bool) -> tuple[bool, str]:
    """Dashboard toggle: same rules as the set_memory_private tool (teammates' copies are read-only)."""
    with closing(db()) as con, con:
        r = con.execute("SELECT author FROM memories WHERE id=?", (mid,)).fetchone()
        if not r:
            return False, f"No memory with id {mid}."
        if r["author"]:
            return False, f"[{mid}] belongs to {r['author']} (read-only here)."
        con.execute("UPDATE memories SET private=?, updated_at=? WHERE id=?", (int(private), time.time(), mid))
    return True, f"[{mid}] is now " + ("private." if private else "shared with the team at the next sync.")


def code_search(db, q: str, path: str, project: str) -> dict:
    p = codesearch.default_project(project)
    t0 = time.time()
    res, note = codesearch.search(db, q, p, path, 8)
    with closing(db()) as con:
        for r in res:
            r["why"] = githistory.related_note(con, p, r["file"]) or ""
    return {"results": res, "note": note, "ms": int((time.time() - t0) * 1000)}


def list_files_api(db, q: str, project: str) -> dict:
    p = codesearch.default_project(project)
    t0 = time.time()
    rows = codesearch.search_files(db, p, q, 40)
    with closing(db()) as con:
        total = con.execute("SELECT count(*) FROM code_files WHERE project=?", (p,)).fetchone()[0]
        for r in rows:
            r["why"] = githistory.related_note(con, p, r["file"]) or ""
    return {"results": rows, "total": total, "ms": int((time.time() - t0) * 1000)}


def file_api(db, path: str, project: str) -> dict:
    p = codesearch.default_project(project)
    f = codesearch.read_file(p, path, db)
    if "error" in f:
        return f
    with closing(db()) as con:
        f["why"] = githistory.related_note(con, p, path) or ""
        f["commits"] = [{"hash": c["hash"], "date": c["date"], "author": c["author"], "subject": c["subject"]}
                        for c in githistory.file_commits(con, p, path, 8)]
    return f


def list_backups() -> list:
    d = backup.BACKUP_DIR
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if f.endswith(".db"):
            p = os.path.join(d, f)
            out.append({"file": f, "bytes": os.path.getsize(p), "mtime": os.path.getmtime(p)})
    return out


def logs(source: str, q: str, level: str, n: int) -> list:
    if source == "mlx":
        try:
            with open(MLX_LOG, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()[-5000:]
            mtime = os.path.getmtime(MLX_LOG)
            items = [{"t": None, "src": "mlx", "line": l} for l in lines if l.strip()]
        except OSError as e:
            return [{"t": time.time(), "src": "mlx", "line": f"MLX log not available: {e}", "level": "warn"}]
    else:
        items = list(LOG)
    out = []
    for it in items:
        lv = level_of(it["line"])
        if level and level != "all" and lv != level:
            continue
        if q and q.lower() not in it["line"].lower():
            continue
        out.append({**it, "level": lv})
    return out[-n:]


# ---------------------------------------------------------------- actions
_jobs: dict[str, dict] = {}


def run_action(db, name: str) -> dict:
    def go(fn):
        def wrap():
            _jobs[name] = {"state": "running", "started": time.time()}
            try:
                res = fn()
                _jobs[name] = {"state": "done", "finished": time.time(), "result": res}
                print(f"dashboard action {name}: done {res}", flush=True)
            except Exception as e:
                _jobs[name] = {"state": "error", "finished": time.time(), "result": str(e)}
                print(f"dashboard action {name} failed: {e}", flush=True)
        if _jobs.get(name, {}).get("state") == "running":
            return {"ok": False, "message": f"{name} is already running"}
        threading.Thread(target=wrap, daemon=True).start()
        return {"ok": True, "message": f"{name} started"}

    if name == "jira_sync":
        if not jira.SYNC_PROJECT:
            return {"ok": False, "message": "Set JIRA_SYNC_PROJECT in .env first"}
        def f():
            r = jira.sync_jql(db, jira.default_jql(jira.SYNC_DAYS), source="dashboard")
            if r.get("stopped"):
                return f"stopped after {r['saved']} issues (saved ones kept)"
            with closing(db()) as con, con:
                jira._state(con, "jira_last_sync_at", str(time.time()))
                jira._state(con, "jira_last_sync_result", f"{r['saved']} issues (dashboard)")
            return f"{r['saved']} issues"
        return go(f)
    if name == "code_index":
        def f():
            msgs = []
            for p in codesearch.CODE_PROJECTS:
                n = githistory.sync_history(db, p)
                st = codesearch.index_project(db, p)
                msgs.append(f"{p}: {st.get('changed', 0)} changed files, {n} new commits"
                            + (f" — {st['error']}" if st.get("error") else ""))
            return "; ".join(msgs)
        return go(f)
    if name == "memory_sync":
        import sync
        if not sync.configured():
            return {"ok": False, "message": f"Team sync is {sync.config_error()}"}
        return go(lambda: sync.sync(db, source="dashboard"))
    if name == "backup_now":
        def f():
            import jobs
            import sqlite3
            now = datetime.now(backup.BACKUP_TZ)
            out = os.path.join(backup.BACKUP_DIR, f"manual-{now:%Y-%m-%d-%H%M%S}.db")
            with jobs.job("backup", "Backup now", "dashboard"):
                os.makedirs(backup.BACKUP_DIR, exist_ok=True)
                src, dst = sqlite3.connect(os.environ.get("MEMORY_DB", "/data/memory.db"), timeout=30), sqlite3.connect(out)
                try:
                    src.backup(dst)
                    dst.execute("PRAGMA journal_mode=DELETE")
                finally:
                    dst.close()
                    src.close()
            return os.path.basename(out)
        return go(f)
    return {"ok": False, "message": f"unknown action {name}"}


# ---------------------------------------------------------------- health checks
_qc = {"at": 0.0, "result": "ok"}


def health(db) -> dict:
    """A small set of checks for the dashboard's Health panel (cheap: runs every few seconds)."""
    import json as _json
    import shutil
    import urllib.request
    import jobs
    checks = []
    add = lambda name, status, detail: checks.append({"name": name, "status": status, "detail": detail})  # noqa: E731

    add("ai-memory server", "good", f"up {int(time.time() - STARTED)} s")
    # database integrity: quick_check, cached for 10 minutes
    if time.time() - _qc["at"] > 600:
        with closing(db()) as con:
            _qc.update(at=time.time(), result=con.execute("PRAGMA quick_check").fetchone()[0])
    add("Database", "good" if _qc["result"] == "ok" else "critical",
        f"integrity {_qc['result']} · {os.path.getsize(os.environ.get('MEMORY_DB', '/data/memory.db')) / 1e6:.1f} MB")
    # MLX server (GPU): embeddings + local explain model
    mlx = codesearch.mlx_state(db)
    if mlx == "up":
        try:
            with urllib.request.urlopen(f"{codesearch.EMBED_URL}/health", timeout=3) as r:
                m = _json.load(r)
        except Exception:
            m = {}
        mem, parts = m.get("mem") or {}, ["up"]
        mac = mem.get("mac") or {}
        if "active_mb" in mem:
            parts.append(f"uses {(mem['active_mb'] + mem.get('cache_mb', 0)) / 1024:.1f} GB")
        parts.append("explain model " + ("loaded" if m.get("llm_loaded") else "idle (loads on first Explain)"))
        if mac:
            parts.append(f"Mac {mac['free_pct']}% memory free")
        if mac and mac["free_pct"] < 10:
            add("MLX GPU server", "warning", " · ".join(parts) + f" — the Mac is almost out of memory (swap "
                f"{mac.get('swap_used_gb', '?')} GB used): the Explain model won't load. Close other local model "
                "servers or big apps.")
        else:
            add("MLX GPU server", "good", " · ".join(parts))
    elif mlx == "off":
        add("MLX GPU server", "off", ("turned off (MLX=off)" if codesearch.MLX_MODE == "off" else "not installed")
            + " — code search by keywords; Explain AI hidden. Optional, Apple Silicon only: scripts/install-mlx.sh")
    elif codesearch._mlx.get("why") == "stuck":
        add("MLX GPU server", "warning", "not answering (stuck) — usually the Mac ran out of memory. Keyword search "
            "meanwhile; it restarts itself within ~4 min, or now: launchctl kickstart -k gui/$(id -u)/com.aimemory.mlx-embed")
    else:
        add("MLX GPU server", "warning", "not running (it worked before) — keyword search until it is back. "
            "Start: launchctl kickstart -k gui/$(id -u)/com.aimemory.mlx-embed")
    # Jira
    if not jira.configured():
        add("Jira sync", "off", "not configured")
    else:
        with closing(db()) as con:
            last = float(jira._state(con, "jira_last_sync_at") or 0)
        age_h = (time.time() - last) / 3600 if last else None
        lim = max(jira.SYNC_INTERVAL_H, 1) * 1.5
        add("Jira sync", "good" if age_h is not None and age_h < lim else "warning",
            f"last {age_h:.1f} h ago" if age_h is not None else "never synced")
    # code index
    for p in codesearch.CODE_PROJECTS:
        s = codesearch._status.get(p, {})
        st = s.get("state")
        if st == "error":
            add(f"Code index · {p}", "critical", s.get("error", "error")[:160])
        elif st == "indexing":
            add(f"Code index · {p}", "good", f"indexing {s.get('done', 0)}/{s.get('total', 0)}")
        elif st == "stopped":
            add(f"Code index · {p}", "warning", "stopped by you — continues at the next run")
        else:
            with closing(db()) as con:
                n = con.execute("SELECT count(*) FROM code_chunks WHERE project=?", (p,)).fetchone()[0]
            add(f"Code index · {p}", "good" if n else "warning", f"{n} chunks" + (" · waiting for first run" if not n else ""))
    # backups: on a backup day after the backup hour, today's file should exist
    now = datetime.now(backup.BACKUP_TZ)
    due_today = now.weekday() in backup.BACKUP_DAYS and now.hour >= backup.BACKUP_HOUR
    files = [f for f in os.listdir(backup.BACKUP_DIR) if f.endswith(".db")] if os.path.isdir(backup.BACKUP_DIR) else []
    if due_today and not os.path.exists(backup.path_for(now)):
        add("Backups", "warning", "today's backup hasn't run yet")
    else:
        add("Backups", "good" if files else "warning", f"{len(files)} kept")
    # team memory sync (git repo)
    import sync
    s = sync.status(db)
    if not s["configured"]:
        add("Team sync", "off" if s["problem"].startswith("off") else "warning", s["problem"])
    elif s["error"] and (s["error_at"] or 0) >= (s["last_at"] or 0):
        add("Team sync", "critical", f"failed {tasks.ago(s['error_at'])}: {s['error'][:160]}")
    elif s["last_at"]:
        late = s["interval_min"] > 0 and time.time() - s["last_at"] > 3 * s["interval_min"] * 60
        add("Team sync", "warning" if late else "good", f"{tasks.ago(s['last_at'])} · {s['last_result'][:160]}")
    else:
        add("Team sync", "good", "waiting for the first sync")
    # recent errors in the server log (last hour)
    errs = [l for l in LOG if l["t"] and time.time() - l["t"] < 3600 and level_of(l["line"]) == "error"]
    add("Errors (last hour)", "good" if not errs else "warning", f"{len(errs)}" + (f" — latest: {errs[-1]['line'][:120]}" if errs else ""))
    # disk space where the data lives
    du = shutil.disk_usage(os.path.dirname(os.environ.get("MEMORY_DB", "/data/memory.db")))
    free_gb = du.free / 1e9
    add("Disk", "good" if free_gb > 5 else "warning" if free_gb > 1 else "critical", f"{free_gb:.0f} GB free")

    worst = "critical" if any(c["status"] == "critical" for c in checks) else \
        "warning" if any(c["status"] == "warning" for c in checks) else "good"
    return {"overall": worst, "checks": checks, "jobs": jobs.snapshot(), "at": time.time()}


# ---------------------------------------------------------------- ASGI handler
async def _send_json(send, obj, status=200):
    body = json.dumps(obj, default=str).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".ico": "image/x-icon", ".json": "application/json", ".woff2": "font/woff2", ".map": "application/json"}
SECURITY = [(b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"), (b"x-content-type-options", b"nosniff"),
            (b"content-security-policy",
             b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")]


async def serve_ui(send, path: str = "/ui", set_cookie: bytes = b""):
    """The React app (built into WEB_DIR by the Dockerfile). /ui -> index.html,
    /ui/assets/<hashed file> -> static file (cached long: names change every build)."""
    rel = path[len("/ui"):].lstrip("/") or "index.html"
    root = os.path.realpath(WEB_DIR)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):  # no ../ escapes, no dirs
        full, rel = os.path.join(root, "index.html"), "index.html"
        if not os.path.isfile(full):
            await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"dashboard not built (web/dist missing)"})
            return
    with open(full, "rb") as fh:
        body = fh.read()
    ctype = TYPES.get(os.path.splitext(full)[1], "application/octet-stream")
    cache = b"public, max-age=31536000, immutable" if rel.startswith("assets/") else b"no-store"
    extra = [(b"set-cookie", set_cookie)] if set_cookie and rel == "index.html" else []
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", ctype.encode()), (b"cache-control", cache)] + SECURITY + extra})
    await send({"type": "http.response.body", "body": body})


EXPLAIN_SYSTEM = (
    "You are a senior engineer explaining code to a teammate. Use ONLY the material given: "
    "the code, the git commits that wrote these exact lines, the Jira story and recorded decisions. "
    "Answer in markdown with three short sections:\n"
    "**What it does** - 2-4 sentences in plain words.\n"
    "**Why it exists** - the business reason, citing the Jira key and the commit message(s).\n"
    "**Rule it implements** - the acceptance criterion or decision it satisfies, if one is visible.\n"
    "If no story is given, say the reason isn't recorded instead of guessing. Be concise."
)


async def _read_body(receive) -> dict:
    chunks, more = [], True
    while more:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        more = msg.get("more_body", False)
    raw = b"".join(chunks)
    return json.loads(raw) if raw else {}


async def _stream_explanation(db, body: dict, send, receive=None) -> None:
    """Analyse the snippet, then stream the local model's explanation (MLX server) to the browser."""
    import asyncio
    import urllib.request
    import snippet
    loop = asyncio.get_running_loop()
    a = await loop.run_in_executor(None, snippet.analyse, db, body.get("code", ""), body.get("project", ""))
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store"),
                            (b"x-accel-buffering", b"no")]})
    if not a.get("location"):
        await send({"type": "http.response.body", "body": b"Couldn't find this code in the indexed repo.", "more_body": False})
        return
    if not codesearch.mlx_up():
        await send({"type": "http.response.body", "more_body": False,
                    "body": b"The local AI explanation needs the MLX GPU server (Apple Silicon). "
                            b"Location, git blame and the Jira story above work without it."})
        return
    prompt = snippet.to_markdown(a, story_chars=1800)
    q: asyncio.Queue = asyncio.Queue()
    import jobs
    import threading
    halt = threading.Event()

    def pump():  # runs in a thread: read the MLX stream, hand pieces to the event loop
        try:
            req = urllib.request.Request(
                f"{codesearch.EMBED_URL}/api/generate", method="POST",
                data=json.dumps({"system": EXPLAIN_SYSTEM, "prompt": prompt, "max_tokens": 700}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                # Closing this connection (Stop) makes the MLX server stop generating.
                while not halt.is_set() and (chunk := r.read1(256)):
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
        except urllib.error.HTTPError as e:  # e.g. 503: not enough free memory -- show the server's reason
            try:
                msg = json.loads(e.read() or b"{}").get("error") or str(e)
            except Exception:
                msg = str(e)
            if not halt.is_set():
                loop.call_soon_threadsafe(q.put_nowait, f"\n\n_{msg}_".encode())
        except Exception as e:
            if not halt.is_set():
                loop.call_soon_threadsafe(q.put_nowait, f"\n\n_Local model unavailable: {e}_".encode())
        loop.call_soon_threadsafe(q.put_nowait, None)

    async def watch_disconnect():  # browser closed the popup / pressed Stop / left the page
        while receive is not None:
            msg = await receive()
            if msg.get("type") == "http.disconnect":
                halt.set()
                return

    watcher = asyncio.create_task(watch_disconnect())
    loc = a["location"]
    try:
        with jobs.job("explain", f"AI explanation · {loc['file'].rsplit('/', 1)[-1]}:{loc['start']}", "dashboard") as j:
            loop.run_in_executor(None, pump)
            n = 0
            while (chunk := await q.get()) is not None:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
                n += len(chunk)
                j.progress = f"{n} characters"
                if j.stopped:
                    halt.set()
                    await send({"type": "http.response.body", "body": b"\n\n_Stopped._", "more_body": True})
                    break
                if halt.is_set():  # client went away: stop now
                    break
    finally:
        halt.set()
        watcher.cancel()
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def handle_api(db, scope, send, receive=None):
    """Called by BearerAuth for /api/* AFTER the token was checked."""
    import asyncio
    path, method = scope["path"], scope.get("method", "GET")
    if method == "POST" and path == "/api/explain":
        import snippet
        body = await _read_body(receive)
        a = await asyncio.get_running_loop().run_in_executor(None, snippet.analyse, db, body.get("code", ""), body.get("project", ""))
        return await _send_json(send, a)
    if method == "POST" and path == "/api/explain/ai":
        try:
            return await _stream_explanation(db, await _read_body(receive), send, receive)
        except Exception as e:
            print(f"dashboard explain/ai error: {e}", flush=True)
            return
    q = {k: v[0] for k, v in parse_qs(scope.get("query_string", b"").decode()).items()}
    loop = asyncio.get_running_loop()

    def run(fn, *a):  # keep DB / embedding calls off the event loop
        return loop.run_in_executor(None, fn, *a)

    try:
        if path == "/api/overview":
            return await _send_json(send, await run(overview, db))
        if path == "/api/jira":
            return await _send_json(send, await run(list_jira, db, q.get("q", ""), q.get("status", "")))
        if path == "/api/jira/story":
            return await _send_json(send, await run(jira_detail, db, q.get("key", "")))
        if path == "/api/memories":
            return await _send_json(send, await run(list_memories, db, q.get("q", "")))
        if path == "/api/code/files":
            return await _send_json(send, await run(list_files_api, db, q.get("q", ""), q.get("project", "")))
        if path == "/api/code/file":
            return await _send_json(send, await run(file_api, db, q.get("path", ""), q.get("project", "")))
        if path == "/api/code/search":
            return await _send_json(send, await run(code_search, db, q.get("q", ""), q.get("path", ""), q.get("project", "")))
        if path == "/api/backups":
            return await _send_json(send, {"files": list_backups(), "dir": "~/Documents/ai-memory/backups"})
        if path == "/api/logs":
            return await _send_json(send, logs(q.get("source", "server"), q.get("q", ""), q.get("level", "all"),
                                               max(10, min(int(q.get("n", 400)), 3000))))
        if path == "/api/jobs":
            return await _send_json(send, _jobs)
        if path == "/api/health":
            return await _send_json(send, await run(health, db))
        if path.startswith("/api/jobs/stop/") and method == "POST":
            import jobs
            target = path.rsplit("/", 1)[-1]
            n = jobs.stop_all() if target == "all" else int(jobs.stop(int(target))) if target.isdigit() else 0
            return await _send_json(send, {"ok": n > 0, "message": f"stopping {n} job(s)" if n else "nothing to stop"})
        if path == "/api/sync":
            import sync
            return await _send_json(send, await run(sync.status, db))
        if path == "/api/memory/private" and method == "POST":
            body = await _read_body(receive)
            ok, msg = await run(set_private, db, int(body.get("id", 0)), bool(body.get("private")))
            return await _send_json(send, {"ok": True, "message": msg} if ok else {"ok": False, "error": msg},
                                    200 if ok else 400)
        if path.startswith("/api/action/") and method == "POST":
            return await _send_json(send, run_action(db, path.rsplit("/", 1)[-1]))
        return await _send_json(send, {"error": "not found"}, 404)
    except Exception as e:
        print(f"dashboard api {path} error: {e}", flush=True)
        return await _send_json(send, {"error": str(e)}, 500)
