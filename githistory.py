"""
The "WHY" layer: links code files <-> git commits <-> Jira tickets <-> task decisions.

The reason code exists is never in the code itself. It is in:
  - commit messages   "PROJ-123: keep item position on rename, move to top on create"
  - the Jira story    business goal + acceptance criteria
  - task decisions    "sort on client, API has no sort param" (task journal)
This module reads git history (read-only), stores which commits touched which files,
and builds an "explanation package" so an AI can answer "what does this do and WHY".
"""
import os
import re
import subprocess
import time
from contextlib import closing

import codesearch
import jira
import tasks

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

SCHEMA = """
CREATE TABLE IF NOT EXISTS git_commits(
  project TEXT NOT NULL, hash TEXT NOT NULL, date REAL NOT NULL,
  author TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  keys TEXT NOT NULL DEFAULT '',            -- comma-separated Jira keys found in the message
  PRIMARY KEY (project, hash)
);
CREATE TABLE IF NOT EXISTS git_commit_files(
  project TEXT NOT NULL, hash TEXT NOT NULL, file TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS git_files_by_file ON git_commit_files(project, file);
CREATE INDEX IF NOT EXISTS git_commits_by_date ON git_commits(project, date);
"""


def sync_history(db, project: str) -> int:
    """Read `git log --all` and store commits we don't have yet. Returns new commit count."""
    root = os.path.join(codesearch.REPOS_DIR, project)
    fmt = "%x1e%H%x1f%at%x1f%an%x1f%s%x1f%b%x1f"
    out = subprocess.run(["git", "-C", root, "log", "--all", "--no-merges", f"--format={fmt}", "--name-only"],
                         capture_output=True, check=True).stdout.decode(errors="replace")
    with closing(db()) as con, con:
        known = {r[0] for r in con.execute("SELECT hash FROM git_commits WHERE project=?", (project,))}
        new = 0
        for rec in out.split("\x1e"):
            parts = rec.split("\x1f")
            if len(parts) < 6:
                continue
            h, at, author, subject, body, files = parts[0].strip(), parts[1], parts[2], parts[3], parts[4], parts[5]
            if not h or h in known:
                continue
            keys = sorted(set(KEY_RE.findall(subject + " " + body)))
            con.execute("INSERT OR IGNORE INTO git_commits(project,hash,date,author,subject,body,keys) "
                        "VALUES (?,?,?,?,?,?,?)", (project, h, float(at), author, subject, body.strip(), ",".join(keys)))
            con.executemany("INSERT INTO git_commit_files(project,hash,file) VALUES (?,?,?)",
                            [(project, h, f.strip()) for f in files.splitlines() if f.strip()])
            new += 1
    return new


def file_commits(con, project: str, file: str, limit: int = 10):
    return con.execute(
        "SELECT c.* FROM git_commit_files f JOIN git_commits c ON c.project=f.project AND c.hash=f.hash "
        "WHERE f.project=? AND f.file=? ORDER BY c.date DESC LIMIT ?", (project, file, limit)).fetchall()


def file_keys(con, project: str, file: str) -> list[str]:
    """Jira keys of the commits that touched a file, most commits first."""
    counts: dict[str, int] = {}
    for c in file_commits(con, project, file, 200):
        for k in filter(None, c["keys"].split(",")):
            counts[k] = counts.get(k, 0) + 1
    return sorted(counts, key=lambda k: -counts[k])


def related_note(con, project: str, file: str) -> str | None:
    """One line under each code_search result: tickets + commit activity."""
    commits = file_commits(con, project, file, 200)
    if not commits:
        return None
    keys = file_keys(con, project, file)[:3]
    labels = []
    for k in keys:
        s = con.execute("SELECT summary FROM jira_stories WHERE key=?", (k,)).fetchone()
        labels.append(f"{k} {s['summary'][:50]}" if s else k)
    return (f"_Why: {'; '.join(labels) or 'no ticket keys'} · {len(commits)} commits, "
            f"last {tasks.ago(commits[0]['date'])} — `explain_code` for details_")


def _overlap(text: str, qwords: set[str]) -> int:
    return len(qwords & set(re.findall(r"[a-z0-9]{3,}", text.lower())))


def explanation_package(db, project: str, file: str, snippet: str = "", query: str = "",
                        seen: set | None = None) -> str:
    """Code + the RELEVANT commits and stories for one file, kept compact:
    - commits matching the question's words come first;
    - tickets ranked by (commits matching the question, then commit count); one-off
      tickets with no match are dropped as noise;
    - a story already shown for another file is referenced, not repeated (`seen`)."""
    seen = seen if seen is not None else set()
    qwords = set(re.findall(r"[a-z0-9]{3,}", query.lower())) - {"the", "and", "why", "how", "does", "what", "for", "with"}
    with closing(db()) as con:
        all_commits = file_commits(con, project, file, 200)
    commits = sorted(all_commits[:60], key=lambda c: (-_overlap(c["subject"], qwords), -c["date"]))[:8]
    score: dict[str, list[int]] = {}  # key -> [best word overlap of its commits, commit count]
    for c in all_commits:
        m = _overlap(c["subject"], qwords)
        for k in filter(None, c["keys"].split(",")):
            s = score.setdefault(k, [0, 0])
            s[0] = max(s[0], m)
            s[1] += 1
    ranked = sorted(score, key=lambda k: (-score[k][0], -score[k][1]))
    top = score[ranked[0]][0] if ranked else 0
    # Keep tickets whose best commit matches the question about as well as the best one
    # (one shared word like "templates" is not enough); if nothing matches, the main ticket.
    keys = [k for k in ranked if top >= 2 and score[k][0] >= max(2, top - 1)][:2] or ranked[:1]

    out = [f"# {file}"]
    if snippet:
        lines = snippet.split("\n")
        out.append("## Code\n```\n" + "\n".join(lines[:60]) + ("\n…" if len(lines) > 60 else "") + "\n```")
    if commits:
        out.append("## Commits that shaped it (most relevant first)\n" + "\n".join(
            f"- {time.strftime('%Y-%m-%d', time.localtime(c['date']))} {c['author']}: {c['subject']}"
            for c in commits))
    for k in keys:
        if k in seen:
            out.append(f"## Story {k} — shown above")
            continue
        seen.add(k)
        row, _ = jira.get_story(db, k)  # cached; fetched once if missing
        if row:
            text = row["text"]
            out.append(f"## Story {k} [{row['status']}]\n" + (text[:1500] + ("\n…" if len(text) > 1500 else "")))
        with closing(db()) as con:
            decisions = con.execute("SELECT content, source, created_at FROM task_log "
                                    "WHERE task_key=? AND kind='decision' ORDER BY created_at", (k,)).fetchall()
        if decisions:
            out.append(f"## Decisions recorded for {k}\n" + "\n".join(
                f"- {d['content']} _({d['source'] or '?'}, {tasks.ago(d['created_at'])})_" for d in decisions))
    if len(out) == 1 or (not commits and not snippet):
        out.append("_No history found for this file (not committed yet, or outside the indexed repo)._")
    return "\n\n".join(out)


def register(mcp, db) -> None:
    with closing(db()) as con, con:
        con.executescript(SCHEMA)
    codesearch.FILE_NOTES.append(related_note)

    @mcp.tool()
    def explain_code(query: str = "", file: str = "", project: str = "") -> str:
        """Everything needed to explain WHAT code does and WHY it was built that way:
        the code + the commits that changed it + their Jira stories + recorded decisions.

        Give `file` (repo-relative path) or a `query` (finds the top matching files).
        Then explain to the user, citing the tickets and commit messages as the reasons.
        """
        ps = codesearch.group_of(project)
        targets: list[tuple[str, str, str]] = []  # (project, file, code)
        if file:
            found = codesearch.resolve_file(db, ps, file)
            if not found:
                return f"{file} is not an indexed file of {', '.join(ps)}."
            p, f = found
            with closing(db()) as con:
                rows = con.execute("SELECT text FROM code_chunks WHERE project=? AND file=? ORDER BY start_line",
                                   (p, f)).fetchall()
            snippet = "\n".join(r["text"] for r in rows)
            lines = snippet.split("\n")
            targets.append((p, f, "\n".join(lines[:120]) + ("\n…" if len(lines) > 120 else "")))
        elif query:
            # linked repos (web + API): up to 4 files, so both sides of a feature can show up
            results, _ = codesearch.search(db, query, ps, limit=8)
            seen = []
            for r in results:
                if (r["project"], r["file"]) not in seen:
                    seen.append((r["project"], r["file"]))
                    targets.append((r["project"], r["file"], r["text"]))
                if len(targets) >= (4 if len(ps) > 1 else 3):
                    break
        else:
            return "Give a file path or a query."
        if not targets:
            return "No matching code found."
        seen: set = set()
        return "\n\n---\n\n".join(
            (f"_Repo: {p}_\n" if len(ps) > 1 else "") + explanation_package(db, p, f, s, query, seen)
            for p, f, s in targets)

    @mcp.tool()
    def file_history(file: str, project: str = "", limit: int = 15) -> str:
        """Git history of one file: dates, authors, commit messages, Jira keys."""
        ps = codesearch.group_of(project)
        p, file = codesearch.resolve_file(db, ps, file) or (ps[0], file)
        with closing(db()) as con:
            rows = file_commits(con, p, file, max(1, min(int(limit), 100)))
        if not rows:
            return f"No commits found for {file} in {p}."
        return "\n".join(f"- {time.strftime('%Y-%m-%d', time.localtime(c['date']))} {c['hash'][:8]} "
                         f"{c['author']}: {c['subject']}" for c in rows)


def start_scheduler(db) -> None:
    """Refresh git history together with the code index (cheap: only new commits are stored)."""
    import threading

    def loop():
        time.sleep(20)
        while True:
            for p in list(codesearch.CODE_PROJECTS):
                try:
                    n = sync_history(db, p)
                    if n:
                        print(f"git history {p}: {n} new commits", flush=True)
                except Exception as e:
                    print(f"git history {p} failed: {e}", flush=True)
            time.sleep(codesearch.REINDEX_EVERY_S or 600)

    threading.Thread(target=loop, name="git-history", daemon=True).start()
