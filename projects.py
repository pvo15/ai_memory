"""Connect a repo without editing .env or restarting (dashboard button or the connect_project tool).

The repos folder (PROJECTS_DIR, mounted read-only at REPOS_DIR) is already visible to the server;
"connecting" a repo only tells ai-memory to index and read it. Repos connected here are stored in
the database (table connected_projects) and merged with the ones from the environment:

    CODE_PROJECTS / PROJECT_GROUPS (.env or Nix)  +  connected_projects (database)
        -> codesearch.CODE_PROJECTS / codesearch.PROJECT_GROUPS (changed in place, so every
           module that imported those lists sees the new repo at once)

Repos from the environment can only be removed there; repos connected here can be disconnected
here. Disconnecting keeps the index, so connecting again is instant.
"""
import os
import re
import threading
import time
from contextlib import closing

import codesearch

SCHEMA = """
CREATE TABLE IF NOT EXISTS connected_projects(
  name TEXT PRIMARY KEY,                 -- folder inside PROJECTS_DIR, e.g. "my-app" or "team/my-app"
  linked_to TEXT NOT NULL DEFAULT '',    -- searched together with this repo (like PROJECT_GROUPS)
  added_at REAL NOT NULL,
  source TEXT NOT NULL DEFAULT ''
);
"""
# What the environment configured, before anything from the database is merged in.
ENV_PROJECTS = list(codesearch.CODE_PROJECTS)
ENV_GROUPS = [list(g) for g in codesearch.PROJECT_GROUPS]
_NAME = re.compile(r"^[\w.\- ]+(/[\w.\- ]+){0,2}$")
_lock = threading.Lock()


def _clean(name: str) -> str:
    return (name or "").strip().strip("/")


def _check_name(name: str) -> str:
    """'' if the name is a usable folder name, else the reason it is not."""
    if not name:
        return "Give the repo folder name, e.g. `my-app`."
    if not _NAME.match(name) or any(part in (".", "..") or part.startswith(".") for part in name.split("/")):
        return f"`{name}` is not a folder name inside the projects folder (no absolute paths, `..` or hidden folders)."
    return ""


def _is_repo(name: str) -> bool:
    # a real .git folder: a git worktree (.git file) points at a path that only exists on the Mac
    return os.path.isdir(os.path.join(codesearch.REPOS_DIR, name, ".git"))


def _link(groups: list[list[str]], a: str, b: str) -> None:
    """Put a and b in one group, merging groups if needed."""
    ga = next((g for g in groups if a in g), None)
    gb = next((g for g in groups if b in g), None)
    if ga is not None and ga is gb:
        return
    if ga is None and gb is None:
        groups.append([a, b])
    elif ga is None:
        gb.append(a)
    elif gb is None:
        ga.append(b)
    else:
        ga.extend(x for x in gb if x not in ga)
        groups.remove(gb)


def apply(db) -> None:
    """Rebuild the effective repo list and groups from the environment plus the database."""
    with closing(db()) as con:
        rows = con.execute("SELECT name, linked_to FROM connected_projects ORDER BY added_at").fetchall()
    with _lock:
        names = ENV_PROJECTS + [r["name"] for r in rows if r["name"] not in ENV_PROJECTS]
        groups = [list(g) for g in ENV_GROUPS]
        for r in rows:
            if r["linked_to"] and r["linked_to"] in names:
                _link(groups, r["linked_to"], r["name"])
        codesearch.CODE_PROJECTS[:] = names
        codesearch.PROJECT_GROUPS[:] = groups


def origin(name: str) -> str:
    """'env' for repos from CODE_PROJECTS (removable only there), 'dashboard' for the ones added here."""
    return "env" if name in ENV_PROJECTS else "dashboard"


def available() -> list[dict]:
    """Git repos inside the projects folder (top level, and one level down in plain folders)."""
    root = codesearch.REPOS_DIR
    out: list[dict] = []
    if not os.path.isdir(root):
        return out

    def scan(rel: str, depth: int) -> None:
        try:
            entries = sorted(os.listdir(os.path.join(root, rel) if rel else root), key=str.lower)
        except OSError:
            return
        for e in entries:
            if e.startswith(".") or len(out) >= 300:
                continue
            name = f"{rel}/{e}" if rel else e
            if not os.path.isdir(os.path.join(root, name)) or _check_name(name):
                continue
            if _is_repo(name):
                out.append({"name": name, "connected": name in codesearch.CODE_PROJECTS})
            elif depth < 1:
                scan(name, depth + 1)

    scan("", 0)
    return out


def _index_now(db, name: str) -> None:
    def run():
        import githistory
        try:
            n = githistory.sync_history(db, name)
            st = codesearch.index_project(db, name)
            print(f"connected {name}: {n} commits, {st.get('done', 0)} chunks indexed"
                  + (f" — {st['error']}" if st.get("error") else ""), flush=True)
        except Exception as e:
            print(f"connected {name}: first index failed: {e}", flush=True)

    threading.Thread(target=run, name=f"connect-{name}", daemon=True).start()


def connect(db, name: str, linked_to: str = "", source: str = "") -> tuple[bool, str]:
    name, linked_to = _clean(name), _clean(linked_to)
    err = _check_name(name)
    if err:
        return False, err
    if not _is_repo(name):
        return False, (f"No git repo `{name}` in the projects folder. ai-memory only sees repos inside "
                       "PROJECTS_DIR: move or clone it there first.")
    if linked_to:
        if linked_to == name:
            return False, "A repo can't be linked to itself."
        if linked_to not in codesearch.CODE_PROJECTS:
            return False, f"`{linked_to}` is not connected, so `{name}` can't be searched together with it."
    known = name in codesearch.CODE_PROJECTS
    with closing(db()) as con, con:
        row = con.execute("SELECT linked_to FROM connected_projects WHERE name=?", (name,)).fetchone()
        if known and (not linked_to or (row and row["linked_to"] == linked_to)
                      or linked_to in codesearch.group_of(name)):
            return True, f"`{name}` is already connected."
        con.execute("INSERT INTO connected_projects(name, linked_to, added_at, source) VALUES (?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET linked_to=excluded.linked_to",
                    (name, linked_to, time.time(), source))
    apply(db)
    if known:
        return True, f"`{name}` is now searched together with `{linked_to}`."
    _index_now(db, name)
    return True, (f"Connected `{name}`" + (f", searched together with `{linked_to}`" if linked_to else "")
                  + ". The first index runs now in the background (a few minutes for a big repo).")


def disconnect(db, name: str) -> tuple[bool, str]:
    name = _clean(name)
    if name in ENV_PROJECTS:
        return False, f"`{name}` comes from CODE_PROJECTS in the settings: remove it there and restart."
    with closing(db()) as con, con:
        n = con.execute("DELETE FROM connected_projects WHERE name=?", (name,)).rowcount
        # a repo that was linked to this one keeps being connected, just no longer linked
        con.execute("UPDATE connected_projects SET linked_to='' WHERE linked_to=?", (name,))
    if not n:
        return False, f"`{name}` is not connected."
    apply(db)
    return True, f"Disconnected `{name}`. Its index is kept, so connecting it again is instant."


def register(mcp, db) -> None:
    with closing(db()) as con, con:
        con.executescript(SCHEMA)
    apply(db)

    @mcp.tool()
    def connect_project(name: str, linked_to: str = "", source: str = "") -> str:
        """Connect a repo to ai-memory (code search, find_files, explain_code, project_rules),
        without a restart. Use it only when the user asks to connect a repo.

        name: the repo's folder inside the projects folder (PROJECTS_DIR), e.g. "my-app".
        linked_to: optional connected repo that is the same product (e.g. the API of a web
                   app); the two are then searched together.
        source: which tool is asking, e.g. "claude-code".
        """
        ok, msg = connect(db, name, linked_to, source)
        if not ok:
            avail = [r["name"] for r in available() if not r["connected"]][:30]
            if avail:
                msg += " Repos you can connect: " + ", ".join(avail) + "."
        return msg
