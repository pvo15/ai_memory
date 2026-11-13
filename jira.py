"""
Jira stories, fetched on demand and cached in SQLite.

- `task_start` fetches the story for its key (if Jira is configured), so the
  briefing can show it.  The briefing itself only reads the CACHE -- it never
  calls Jira, so the SessionStart hook stays fast.
- `jira_story` returns a story (cached for CACHE_TTL, `refresh=True` re-fetches).
- `search_stories` = full-text search over every story fetched so far.

Config (.env):  JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import closing

import tasks

JIRA_URL = os.environ.get("JIRA_URL", "").rstrip("/")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
CACHE_TTL = 6 * 3600
BRIEF_STORY_CHARS = 2500  # how much of the story goes into the session briefing

SCHEMA = """
CREATE TABLE IF NOT EXISTS jira_stories(
  key        TEXT PRIMARY KEY,
  summary    TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT '',
  text       TEXT NOT NULL DEFAULT '',   -- the whole story as markdown
  raw        TEXT NOT NULL DEFAULT '',   -- original JSON, for later re-parsing
  fetched_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS jira_fts USING fts5(
  key, summary, text, content='jira_stories', content_rowid='rowid',
  tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS jira_ai AFTER INSERT ON jira_stories BEGIN
  INSERT INTO jira_fts(rowid, key, summary, text) VALUES (new.rowid, new.key, new.summary, new.text);
END;
CREATE TRIGGER IF NOT EXISTS jira_ad AFTER DELETE ON jira_stories BEGIN
  INSERT INTO jira_fts(jira_fts, rowid, key, summary, text) VALUES ('delete', old.rowid, old.key, old.summary, old.text);
END;
CREATE TRIGGER IF NOT EXISTS jira_au AFTER UPDATE ON jira_stories BEGIN
  INSERT INTO jira_fts(jira_fts, rowid, key, summary, text) VALUES ('delete', old.rowid, old.key, old.summary, old.text);
  INSERT INTO jira_fts(rowid, key, summary, text) VALUES (new.rowid, new.key, new.summary, new.text);
END;
"""


def fts_query(text: str) -> str:
    """Words -> FTS5 OR-query (same idea as `recall` in memory_server.py)."""
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:20])


def configured() -> bool:
    return bool(JIRA_URL and JIRA_EMAIL and JIRA_TOKEN)


# ---------------------------------------------------------------- Jira -> markdown
def adf_to_text(node, depth: int = 0) -> str:
    """Jira Cloud stores rich text as ADF (a JSON tree). Flatten it to markdown-ish text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    t = node.get("type")
    kids = "".join(adf_to_text(c, depth + 1) for c in node.get("content", []))
    if t == "text":
        return node.get("text", "")
    if t in ("hardBreak",):
        return "\n"
    if t == "heading":
        return "\n" + "#" * min(6, node.get("attrs", {}).get("level", 3) + 2) + " " + kids + "\n"
    if t in ("paragraph",):
        return kids + "\n"
    if t == "listItem":
        return "- " + kids.strip().replace("\n", "\n  ") + "\n"
    if t in ("bulletList", "orderedList"):
        return kids + "\n"
    if t == "codeBlock":
        return "```\n" + kids + "\n```\n"
    if t in ("mention", "emoji", "inlineCard"):
        a = node.get("attrs", {})
        return a.get("text") or a.get("shortName") or a.get("url", "")
    if t == "tableRow":
        return "| " + " | ".join(adf_to_text(c).strip() for c in node.get("content", [])) + " |\n"
    return kids


def field_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict) and value.get("type") == "doc":
        return adf_to_text(value).strip()
    if isinstance(value, dict):
        return value.get("value") or value.get("name") or value.get("displayName") or ""
    if isinstance(value, list):
        return ", ".join(field_text(v) for v in value if v)
    return str(value)


def story_markdown(issue: dict) -> tuple[str, str, str]:
    """-> (summary, status, markdown). Includes any custom field named like 'acceptance criteria'."""
    f, names = issue.get("fields", {}), issue.get("names", {})
    summary = f.get("summary", "")
    status = field_text(f.get("status"))
    meta = " · ".join(x for x in (
        field_text(f.get("issuetype")), status, field_text(f.get("priority")),
        f.get("assignee") and f"assignee {field_text(f.get('assignee'))}",
        f.get("parent") and f"parent {f['parent'].get('key')} {f['parent'].get('fields', {}).get('summary', '')}",
        f.get("labels") and "labels " + ", ".join(f["labels"])) if x)
    out = [f"### {issue.get('key')} — {summary}", meta]

    desc = field_text(f.get("description"))
    if desc:
        out.append("#### Description\n" + desc)
    for fid, name in names.items():  # custom fields such as "Acceptance Criteria"
        if fid.startswith("customfield_") and "acceptance" in name.lower() and f.get(fid):
            out.append(f"#### {name}\n" + field_text(f[fid]))
    subs = f.get("subtasks") or []
    if subs:
        out.append("#### Subtasks\n" + "\n".join(
            f"- {s['key']} [{field_text(s['fields'].get('status'))}] {s['fields'].get('summary', '')}" for s in subs))
    links = f.get("issuelinks") or []
    if links:
        rows = []
        for l in links:
            other = l.get("outwardIssue") or l.get("inwardIssue") or {}
            rel = l["type"]["outward"] if "outwardIssue" in l else l["type"]["inward"]
            rows.append(f"- {rel} {other.get('key')} {other.get('fields', {}).get('summary', '')}")
        out.append("#### Links\n" + "\n".join(rows))
    comments = (f.get("comment") or {}).get("comments", [])[-3:]
    if comments:
        out.append("#### Latest comments\n" + "\n".join(
            f"- {c.get('author', {}).get('displayName', '?')}: {field_text(c.get('body'))[:400]}" for c in comments))
    return summary, status, "\n\n".join(x for x in out if x)


def fetch_issue(key: str) -> dict:
    url = f"{JIRA_URL}/rest/api/3/issue/{key}?fields=*all&expand=names"
    auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def get_story(db, key: str, refresh: bool = False):
    """Cached row for `key`, fetching from Jira when missing/stale. Returns (row, error)."""
    key = tasks.norm_key(key)
    with closing(db()) as con:
        row = con.execute("SELECT * FROM jira_stories WHERE key=?", (key,)).fetchone()
    if row and not refresh and time.time() - row["fetched_at"] < CACHE_TTL:
        return row, ""
    if not configured():
        return row, "Jira is not configured (JIRA_URL / JIRA_EMAIL / JIRA_API_TOKEN in .env)."
    try:
        issue = fetch_issue(key)
    except urllib.error.HTTPError as e:
        return row, f"Jira returned HTTP {e.code} for {key}" + (" (not found or no access)" if e.code in (403, 404) else "")
    except Exception as e:  # network down etc. -> serve the stale copy if we have one
        return row, f"Could not reach Jira: {e}"
    with closing(db()) as con, con:
        save_issue(con, issue)
        return con.execute("SELECT * FROM jira_stories WHERE key=?", (key,)).fetchone(), ""


def save_issue(con, issue: dict) -> None:
    """Upsert one Jira issue (with `names` attached) into the cache + FTS index."""
    summary, status, text = story_markdown(issue)
    con.execute(
        "INSERT INTO jira_stories(key,summary,status,text,raw,fetched_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET summary=excluded.summary, status=excluded.status, "
        "text=excluded.text, raw=excluded.raw, fetched_at=excluded.fetched_at",
        (issue["key"], summary, status, text, json.dumps(issue), time.time()))


# ---------------------------------------------------------------- bulk sync
# Issue types that carry business context (sub-tasks and test runs are mostly noise).
SYNC_TYPES = os.environ.get(
    "JIRA_SYNC_TYPES", "Story,Epic,Task,Bug,Sub-Bug,Research Task,Design Task").split(",")
SYNC_PROJECT = os.environ.get("JIRA_SYNC_PROJECT", "")


def _jira(method: str, path: str, body: dict | None = None):
    auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    req = urllib.request.Request(
        JIRA_URL + path, method=method, data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def default_jql(days: int) -> str:
    types = ", ".join(f'"{t.strip()}"' for t in SYNC_TYPES if t.strip())
    return (f"project = {SYNC_PROJECT} AND issuetype in ({types}) "
            f"AND updated >= -{int(days)}d ORDER BY updated DESC")


def sync_jql(db, jql: str, limit: int = 1000, source: str = "auto") -> dict:
    """Fetch every issue matching `jql` (paged, 50 per request) into the cache.
    Stoppable from the dashboard between pages (returns stopped=True; nothing is lost)."""
    import jobs
    saved, token, types = 0, None, {}
    try:
        with jobs.job("jira_sync", "Jira sync", source) as j:
            names = {f["id"]: f["name"] for f in _jira("GET", "/rest/api/3/field")}  # for "Acceptance Criteria"
            while saved < limit:
                j.check()
                body = {"jql": jql, "fields": ["*all"], "maxResults": 50}
                if token:
                    body["nextPageToken"] = token
                page = _jira("POST", "/rest/api/3/search/jql", body)
                with closing(db()) as con, con:
                    for issue in page.get("issues", []):
                        issue["names"] = names
                        save_issue(con, issue)
                        t = issue["fields"].get("issuetype", {}).get("name", "?")
                        types[t] = types.get(t, 0) + 1
                        saved += 1
                j.progress = f"{saved} issues"
                token = page.get("nextPageToken")
                if not token or page.get("isLast"):
                    break
    except jobs.Stopped:
        return {"saved": saved, "types": types, "stopped": True}
    return {"saved": saved, "types": types}


# ---------------------------------------------------------------- daily auto-sync
# Runs inside the HTTP server process (a background thread). The time of the last
# successful sync is stored in the DB, so restarts don't re-sync and a sleeping Mac
# catches up on wake. Set JIRA_SYNC_INTERVAL_HOURS=0 to turn it off.
SYNC_INTERVAL_H = float(os.environ.get("JIRA_SYNC_INTERVAL_HOURS", "24"))
SYNC_DAYS = int(os.environ.get("JIRA_SYNC_DAYS", "90"))
CHECK_EVERY_S = int(os.environ.get("JIRA_SYNC_CHECK_SECONDS", "1800"))  # look every 30 min

STATE_SCHEMA = "CREATE TABLE IF NOT EXISTS sync_state(name TEXT PRIMARY KEY, value TEXT NOT NULL);"


def _state(con, name: str, value: str | None = None) -> str:
    if value is not None:
        con.execute("INSERT OR REPLACE INTO sync_state(name, value) VALUES (?,?)", (name, value))
        return value
    row = con.execute("SELECT value FROM sync_state WHERE name=?", (name,)).fetchone()
    return row["value"] if row else ""


def last_sync_info(db) -> str:
    with closing(db()) as con:
        at, result = _state(con, "jira_last_sync_at"), _state(con, "jira_last_sync_result")
    if not at:
        return "Never auto-synced yet."
    return f"Last sync {tasks.ago(float(at))}: {result}"


def start_scheduler(db) -> None:
    """Start the background auto-sync thread (call once, from the HTTP server only)."""
    if SYNC_INTERVAL_H <= 0 or not configured() or not SYNC_PROJECT:
        print("jira auto-sync: off" + ("" if SYNC_PROJECT else " (JIRA_SYNC_PROJECT not set)"), flush=True)
        return
    import threading

    def loop():
        time.sleep(int(os.environ.get("JIRA_SYNC_START_DELAY", "60")))  # let the server start
        while True:
            try:
                with closing(db()) as con:
                    last = float(_state(con, "jira_last_sync_at") or 0)
                if time.time() - last >= SYNC_INTERVAL_H * 3600:
                    t0 = time.time()
                    r = sync_jql(db, default_jql(SYNC_DAYS))
                    if r.get("stopped"):  # user pressed Stop: don't count it, retry next check
                        print(f"jira auto-sync: stopped by user after {r['saved']} issues", flush=True)
                        time.sleep(CHECK_EVERY_S)
                        continue
                    result = f"{r['saved']} issues in {time.time() - t0:.0f}s"
                    with closing(db()) as con, con:
                        _state(con, "jira_last_sync_at", str(time.time()))
                        _state(con, "jira_last_sync_result", result)
                    print(f"jira auto-sync: {result}", flush=True)
            except Exception as e:  # network down, Jira error... -> retry at next check
                print(f"jira auto-sync failed (will retry): {e}", flush=True)
            time.sleep(CHECK_EVERY_S)

    threading.Thread(target=loop, name="jira-auto-sync", daemon=True).start()
    print(f"jira auto-sync: every {SYNC_INTERVAL_H:g}h, last {SYNC_DAYS} days", flush=True)


# ---------------------------------------------------------------- wiring
def register(mcp, db) -> None:
    with closing(db()) as con, con:
        con.executescript(SCHEMA)
        con.executescript(STATE_SCHEMA)

    def briefing_section(con, t):
        """Story from the cache only (never calls Jira -> keeps the hook fast)."""
        row = con.execute("SELECT * FROM jira_stories WHERE key=?", (t["key"],)).fetchone()
        if not row:
            return None
        text = row["text"]
        if len(text) > BRIEF_STORY_CHARS:
            text = text[:BRIEF_STORY_CHARS] + f"\n… _(truncated — `jira_story('{t['key']}')` for the full story)_"
        return f"## Jira story (cached {tasks.ago(row['fetched_at'])})\n{text}"

    tasks.EXTRA_SECTIONS.append(briefing_section)
    # task_start fetches the story once, so later briefings have it.
    tasks.ON_START.append(lambda key: get_story(db, key))

    @mcp.tool()
    def jira_story(key: str, refresh: bool = False) -> str:
        """Get a Jira story (description, acceptance criteria, subtasks, links, latest comments).

        Cached locally for 6h; refresh=True re-fetches from Jira.
        key: e.g. "PROJ-123" (a branch name also works).
        """
        row, err = get_story(db, key, refresh)
        if row:
            note = f"_(cached {tasks.ago(row['fetched_at'])})_" + (f"\n_Warning: {err} — showing cached copy._" if err else "")
            return row["text"] + "\n\n" + note
        return err or f"No story {key}."

    @mcp.tool()
    def jira_sync(days: int = 90, jql: str = "") -> str:
        """Bulk-collect Jira issues into the local cache (business context for search_stories).

        Default: project JIRA_SYNC_PROJECT, business issue types (Story, Epic, Task, Bug, Sub-Bug,
        Research Task, Design Task) updated in the last `days` days. Pass `jql` to override.
        Takes ~1s per 50 issues. Re-running refreshes changed issues.
        """
        if not configured():
            return "Jira is not configured (JIRA_URL / JIRA_EMAIL / JIRA_API_TOKEN in .env)."
        if not jql.strip() and not SYNC_PROJECT:
            return "No Jira project to sync: set JIRA_SYNC_PROJECT in .env, or pass `jql`."
        q = jql.strip() or default_jql(days)
        t0 = time.time()
        try:
            r = sync_jql(db, q, source="ai")
            if r.get("stopped"):
                return f"Sync stopped by the user after {r['saved']} issues (already saved ones are kept)."
        except urllib.error.HTTPError as e:
            return f"Jira returned HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
        except Exception as e:
            return f"Sync failed: {e}"
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(r["types"].items(), key=lambda x: -x[1]))
        if not jql.strip() and days == SYNC_DAYS:  # a full default sync counts for the schedule
            with closing(db()) as con, con:
                _state(con, "jira_last_sync_at", str(time.time()))
                _state(con, "jira_last_sync_result", f"{r['saved']} issues (manual)")
        auto = f"every {SYNC_INTERVAL_H:g}h" if SYNC_INTERVAL_H > 0 else "off"
        return (f"Synced {r['saved']} issues in {time.time() - t0:.0f}s ({kinds}).\nJQL: {q}\n"
                f"Auto-sync: {auto}. {last_sync_info(db)}")

    @mcp.tool()
    def search_stories(query: str, limit: int = 8) -> str:
        """Full-text search over all Jira stories fetched so far (business context:
        e.g. "payment templates ordering", "IBAN validation")."""
        q = fts_query(query)
        if not q:
            return "Empty query."
        with closing(db()) as con:
            rows = con.execute(
                "SELECT s.key, s.summary, s.status, snippet(jira_fts, 2, '**', '**', '…', 20) AS snip "
                "FROM jira_fts JOIN jira_stories s ON s.rowid = jira_fts.rowid "
                "WHERE jira_fts MATCH ? ORDER BY bm25(jira_fts) LIMIT ?",
                (q, max(1, min(int(limit), 30)))).fetchall()
        if not rows:
            return "No cached story matches. Stories are cached when fetched (jira_story / task_start)."
        return "\n".join(f"- {r['key']} [{r['status']}] {r['summary']}\n  {r['snip']}" for r in rows)
