"""
Team memory sync through a shared git repo (e.g. on Azure DevOps).

Every teammate runs their own ai-memory. Sync makes their SHARED memories visible to each
other; PRIVATE memories (personal preferences, notes) never leave the Mac.

Repo layout -- one small JSON file per memory, one folder per person:

    memories/<author>/<uid>.json

Each instance writes ONLY its own folder (SYNC_AUTHOR), so two people can never produce a
git merge conflict. One run:

    fetch ─► reset to the remote ─► import the other folders ─► rewrite my folder from the DB
          ─► commit + push   (push rejected because someone pushed meanwhile? repeat)

  * Import mirrors: a teammate's file added / changed / removed -> the local read-only copy
    (author=<name>) is added / changed / removed.
  * Export mirrors: my folder = my shared memories. Forget a memory or mark it private and
    its file is removed at the next sync (it stays in git history -- that's git).
  * New or edited memories wait SYNC_GRACE_MINUTES before they are published, so you can
    still mark them private. Anything that looks like a secret is never exported.
  * The git token is passed to git through environment variables, never written to
    .git/config or shown in logs.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import closing

REPO_URL = os.environ.get("SYNC_REPO_URL", "").strip()
GIT_TOKEN = os.environ.get("SYNC_GIT_TOKEN", "").strip()
AUTHOR = os.environ.get("SYNC_AUTHOR", "").strip().lower()
BRANCH = os.environ.get("SYNC_BRANCH", "main").strip() or "main"
INTERVAL_MIN = float(os.environ.get("SYNC_INTERVAL_MINUTES", "30"))
GRACE_MIN = float(os.environ.get("SYNC_GRACE_MINUTES", "10"))
REPO_DIR = os.environ.get("SYNC_DIR", os.path.join(os.path.dirname(os.environ.get("MEMORY_DB", "/data/memory.db")), "sync-repo"))

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
UID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_CONTENT, MAX_FIELD = 4000, 200
# Never publish text that looks like a credential (memories should never hold one anyway).
SECRET_RE = re.compile(
    r"ATATT[0-9A-Za-z_=-]{10,}|ghp_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,}|figd_[0-9A-Za-z_-]{10,}"
    r"|xox[abprs]-[0-9A-Za-z-]{10,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{20,}"
    r"|(?i:\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|token)\s*[:=]\s*['\"]?[^\s'\"<>]{8,})")

_lock = threading.Lock()


# ---------------------------------------------------------------- config
def config_error() -> str:
    """'' when sync can run, else why not (shown in the dashboard)."""
    if not REPO_URL:
        return "off: SYNC_REPO_URL is not set"
    if not NAME_RE.match(AUTHOR):
        return "SYNC_AUTHOR must be set: lower-case letters, digits, . _ - (e.g. alice)"
    if re.match(r"^[a-z]+://[^/@]*:[^/@]*@", REPO_URL):
        return "SYNC_REPO_URL must not contain a password; put the token in SYNC_GIT_TOKEN"
    return ""


def configured() -> bool:
    return not config_error()


def _repo_label() -> str:
    """The repo URL without any user part, for display."""
    return re.sub(r"^([a-z]+://)[^/@]*@", r"\1", REPO_URL)


# ---------------------------------------------------------------- git
def _env() -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    if GIT_TOKEN and REPO_URL.startswith("https://"):
        basic = base64.b64encode(f"ai-memory:{GIT_TOKEN}".encode()).decode()  # PAT as password; any user name works (Azure DevOps, GitHub, GitLab)
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    return env


def _scrub(text: str) -> str:
    for s in filter(None, (GIT_TOKEN, GIT_TOKEN and base64.b64encode(f"ai-memory:{GIT_TOKEN}".encode()).decode())):
        text = text.replace(s, "***")
    return text


def _git(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=REPO_DIR, env=_env(), capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(_scrub(f"git {args[0]}: {(p.stderr or p.stdout).strip()[:400]}"))
    return p


def _ensure_repo() -> None:
    if os.path.isdir(os.path.join(REPO_DIR, ".git")):
        if _git("remote", "get-url", "origin", check=False).stdout.strip() != REPO_URL:
            _git("remote", "set-url", "origin", REPO_URL)
        return
    if os.path.exists(REPO_DIR):
        shutil.rmtree(REPO_DIR)
    os.makedirs(REPO_DIR)
    _git("init", "-q", "-b", BRANCH)
    _git("remote", "add", "origin", REPO_URL)
    _git("config", "user.name", f"{AUTHOR} (ai-memory)")
    _git("config", "user.email", f"{AUTHOR}@ai-memory.local")


def _to_remote() -> bool:
    """Fetch and make the work tree exactly the remote branch. False = the remote is still empty.
    Throwing away local state is safe: my folder is rebuilt from the database right after."""
    _git("fetch", "-q", "--prune", "origin")
    if _git("rev-parse", "--verify", "-q", f"refs/remotes/origin/{BRANCH}", check=False).returncode:
        return False
    _git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
    _git("reset", "-q", "--hard", f"origin/{BRANCH}")
    _git("clean", "-q", "-fd")
    return True


# ---------------------------------------------------------------- import / export
def _valid(rec, uid: str) -> bool:
    if not isinstance(rec, dict) or rec.get("uid") != uid:
        return False
    c = rec.get("content")
    if not isinstance(c, str) or not c.strip() or len(c) > MAX_CONTENT:
        return False
    for f in ("tags", "project", "source"):
        if not isinstance(rec.get(f, ""), str) or len(rec.get(f, "")) > MAX_FIELD:
            return False
    return all(isinstance(rec.get(f, 0), (int, float)) for f in ("created_at", "updated_at"))


def _import(db) -> dict:
    """Mirror every other person's folder into read-only local copies."""
    root = os.path.join(REPO_DIR, "memories")
    seen: dict[str, tuple[str, dict]] = {}
    bad = 0
    for author in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        folder = os.path.join(root, author)
        if author == AUTHOR or not NAME_RE.match(author) or not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            uid = fn[:-5]
            if not fn.endswith(".json") or not UID_RE.match(uid):
                continue
            try:
                with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                rec = None
            if _valid(rec, uid):
                seen[uid] = (author, rec)
            else:
                bad += 1
    added = changed = removed = 0
    now = time.time()

    def ts(v) -> float:  # a teammate's clock or a hand-edited file: keep only believable times
        return float(v) if isinstance(v, (int, float)) and 1.5e9 < v <= now + 86400 else now
    with closing(db()) as con, con:
        local = {r["uid"]: r for r in con.execute(
            "SELECT id, uid, author, content, tags, project, source FROM memories WHERE uid IS NOT NULL")}
        for uid, (author, rec) in seen.items():
            row = local.get(uid)
            vals = (rec["content"].strip(), rec.get("tags", ""), rec.get("project", ""), rec.get("source", ""))
            if row is None:
                con.execute("INSERT INTO memories(content,tags,project,source,created_at,updated_at,uid,private,author)"
                            " VALUES (?,?,?,?,?,?,?,0,?)",
                            (*vals, ts(rec.get("created_at")), ts(rec.get("updated_at")), uid, author))
                added += 1
            elif not row["author"]:
                continue  # same uid as one of MY memories: never let a file overwrite it
            elif (row["content"], row["tags"], row["project"], row["source"], row["author"]) != (*vals, author):
                con.execute("UPDATE memories SET content=?, tags=?, project=?, source=?, author=?, updated_at=? WHERE id=?",
                            (*vals, author, ts(rec.get("updated_at")), row["id"]))
                changed += 1
        for uid, row in local.items():
            if row["author"] and uid not in seen:
                con.execute("DELETE FROM memories WHERE id=?", (row["id"],))
                removed += 1
    return {"added": added, "changed": changed, "removed": removed, "invalid": bad, "teammates": len({a for a, _ in seen.values()})}


def _export(db) -> dict:
    """Make my folder exactly my shared memories (the ones out of their grace period)."""
    folder = os.path.join(REPO_DIR, "memories", AUTHOR)
    os.makedirs(folder, exist_ok=True)
    cutoff = time.time() - GRACE_MIN * 60
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM memories WHERE author='' AND private=0 AND uid IS NOT NULL").fetchall()
    keep: set[str] = set()
    written = waiting = secret = 0
    for r in rows:
        path = os.path.join(folder, f"{r['uid']}.json")
        if SECRET_RE.search(r["content"]) or SECRET_RE.search(r["tags"] or ""):
            secret += 1
            continue  # not kept -> an earlier published copy is removed too
        if r["updated_at"] > cutoff:  # still in its grace period
            if os.path.exists(path):
                keep.add(r["uid"])  # leave the published version as it is for now
            else:
                waiting += 1
            continue
        keep.add(r["uid"])
        text = json.dumps({"uid": r["uid"], "content": r["content"], "tags": r["tags"], "project": r["project"],
                           "source": r["source"], "created_at": r["created_at"], "updated_at": r["updated_at"]},
                          ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        try:
            with open(path, encoding="utf-8") as fh:
                same = fh.read() == text
        except OSError:
            same = False
        if not same:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            written += 1
    deleted = 0
    for fn in os.listdir(folder):
        if fn.endswith(".json") and fn[:-5] not in keep:
            os.remove(os.path.join(folder, fn))
            deleted += 1
    return {"shared": len(keep), "written": written, "deleted": deleted, "waiting": waiting, "secret": secret}


def _commit_push(summary: str) -> bool:
    mine = f"memories/{AUTHOR}"
    _git("add", "-A", "--", mine)
    if not _git("status", "--porcelain", "--", mine).stdout.strip():
        return False
    _git("commit", "-q", "-m", f"ai-memory: {AUTHOR} {summary}")
    _git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    return True


# ---------------------------------------------------------------- one run
def _state(con, name: str, value: str | None = None) -> str:
    if value is not None:
        con.execute("INSERT OR REPLACE INTO sync_state(name, value) VALUES (?,?)", (name, value))
        return value
    row = con.execute("SELECT value FROM sync_state WHERE name=?", (name,)).fetchone()
    return row["value"] if row else ""


def sync(db, source: str = "auto") -> str:
    """One full sync. Returns a one-line summary; raises on failure (also saved for the dashboard)."""
    err = config_error()
    if err:
        raise RuntimeError(err)
    import jobs
    if not _lock.acquire(blocking=False):
        raise RuntimeError("a memory sync is already running")
    try:
        with jobs.job("memory_sync", "Team memory sync", source) as j:
            _ensure_repo()
            pushed = False
            for attempt in range(3):
                j.progress = "fetching" if not attempt else f"retry {attempt}: someone pushed meanwhile"
                _to_remote()
                imp = _import(db)
                exp = _export(db)
                try:
                    pushed = _commit_push(f"+{exp['written']} -{exp['deleted']}")
                    break
                except RuntimeError as e:
                    if attempt == 2 or "push" not in str(e):
                        raise
            parts = [f"team: +{imp['added']} ~{imp['changed']} -{imp['removed']} from {imp['teammates']} teammate(s)",
                     f"mine: {exp['shared']} shared" + (f", +{exp['written']} -{exp['deleted']} pushed" if pushed else "")]
            if exp["waiting"]:
                parts.append(f"{exp['waiting']} new waiting {GRACE_MIN:g} min")
            if exp["secret"]:
                parts.append(f"{exp['secret']} not shared: look like secrets")
            if imp["invalid"]:
                parts.append(f"{imp['invalid']} invalid file(s) skipped")
            result = " · ".join(parts)
        with closing(db()) as con, con:
            _state(con, "memsync_last_at", str(time.time()))
            _state(con, "memsync_last_result", result)
            _state(con, "memsync_last_error", "")
        print(f"memory sync ({source}): {result}", flush=True)
        return result
    except Exception as e:
        msg = _scrub(str(e))
        with closing(db()) as con, con:
            _state(con, "memsync_last_error", f"{time.time()}|{msg}")
        print(f"memory sync ({source}) error: {msg}", flush=True)
        raise RuntimeError(msg) from None
    finally:
        _lock.release()


def status(db) -> dict:
    with closing(db()) as con:
        c = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        counts = {"shared": c("SELECT count(*) FROM memories WHERE author='' AND private=0"),
                  "private": c("SELECT count(*) FROM memories WHERE author='' AND private=1"),
                  "team": c("SELECT count(*) FROM memories WHERE author!=''"),
                  "teammates": c("SELECT count(DISTINCT author) FROM memories WHERE author!=''")}
        at, result, err = (_state(con, n) for n in ("memsync_last_at", "memsync_last_result", "memsync_last_error"))
    err_at, _, err_msg = err.partition("|")
    return {"configured": configured(), "problem": config_error(), "author": AUTHOR, "repo": _repo_label(),
            "branch": BRANCH, "interval_min": INTERVAL_MIN, "grace_min": GRACE_MIN,
            "last_at": float(at) if at else None, "last_result": result,
            "error": err_msg, "error_at": float(err_at) if err_at else None, **counts}


def start_scheduler(db) -> None:
    """Background sync every SYNC_INTERVAL_MINUTES (HTTP server only). 0 = only the Sync button."""
    if not configured() or INTERVAL_MIN <= 0:
        print(f"memory sync: {'manual only' if configured() else config_error()}", flush=True)
        return

    def loop():
        time.sleep(int(os.environ.get("SYNC_START_DELAY", "90")))
        while True:
            try:
                sync(db)
            except Exception:
                pass  # already logged and shown in the dashboard; try again next interval
            time.sleep(INTERVAL_MIN * 60)

    threading.Thread(target=loop, name="memory-sync", daemon=True).start()
    print(f"memory sync: every {INTERVAL_MIN:g} min as '{AUTHOR}' → {_repo_label()}", flush=True)
