"""
"I have this piece of code — where is it, and WHICH STORY is it from?"

1. LOCATE   find the pasted code in the repo: exact line matching (whitespace-insensitive)
            against the indexed chunks, then the real file for precise line numbers.
            Fallback: meaning search (embedding of the code itself).
2. BLAME    `git blame -w` on exactly those lines -> the commits that WROTE them
            (much more precise than "all commits that ever touched the file").
3. STORIES  Jira keys from those commits, weighted by how many of your lines each wrote
            -> story + acceptance criteria (+ decisions from the task journal).
The result is an "explanation package": an AI (Claude/GLM via MCP, or the local model in
the dashboard) turns it into a plain explanation that cites the real reasons.
"""
import os
import re
import subprocess
import time
from contextlib import closing

import codesearch
import jira
import tasks

TRIVIAL = re.compile(r"^[\s{}()\[\];,.<>/=*'\"`:-]*$|^(return|else|try|finally|break|continue|end|\}\)?;?)$")
KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _distinctive(snippet: str) -> list[str]:
    """Lines worth matching: not empty / not just brackets or keywords."""
    out = []
    for l in snippet.splitlines():
        n = _norm(l)
        if len(n) >= 6 and not TRIVIAL.match(n):
            out.append(n)
    return out


def locate(db, project: str, snippet: str) -> dict | None:
    """-> {file, start, end, matched, total, method, score} or None."""
    lines = _distinctive(snippet)
    root = os.path.join(codesearch.REPOS_DIR, project)
    if lines:
        # Candidate files: chunks containing the most distinctive (longest) lines.
        raw = [l.strip() for l in snippet.splitlines() if _norm(l) in set(lines)]
        probes = sorted(set(raw), key=len, reverse=True)[:6]
        votes: dict[str, int] = {}
        with closing(db()) as con:
            for p in probes:  # a line's own text is contiguous inside its chunk
                needle = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                for (f,) in con.execute(
                        "SELECT DISTINCT file FROM code_chunks WHERE project=? AND text LIKE ? ESCAPE '\\' LIMIT 20",
                        (project, f"%{needle[:200]}%")):
                    votes[f] = votes.get(f, 0) + 1
        best = None
        for f in sorted(votes, key=lambda x: -votes[x])[:5]:
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    flines = [_norm(x) for x in fh.read().split("\n")]
            except OSError:
                continue
            want = set(lines)
            hits = [i for i, l in enumerate(flines) if l in want]
            if not hits:
                continue
            # densest window no longer than ~2x the snippet
            span = max(len(snippet.splitlines()) * 2 + 5, 10)
            j, best_win = 0, (0, 0, 0)
            for i in range(len(hits)):
                while hits[i] - hits[j] > span:
                    j += 1
                matched = len({flines[h] for h in hits[j:i + 1]})
                if matched > best_win[0]:
                    best_win = (matched, hits[j], hits[i])
            matched, s, e = best_win
            score = matched / max(1, len(want))
            if not best or score > best["score"]:
                best = {"file": f, "start": s + 1, "end": e + 1, "matched": matched,
                        "total": len(want), "method": "exact", "score": round(score, 2)}
        if best and best["score"] >= 0.4:
            return best

    # Fallback: meaning search with the code itself (document-to-document similarity).
    if not codesearch.mlx_up():
        return None  # meaning fallback needs the MLX GPU server
    try:
        m = codesearch._matrix(db, project)
        if m is None:
            return None
        ids, files, mat = m
        v = codesearch.embed([snippet[:6000]])[0]
        i = int((mat @ v).argmax())
        with closing(db()) as con:
            r = con.execute("SELECT file, start_line, end_line FROM code_chunks WHERE id=?",
                            (int(ids[i]),)).fetchone()
        return {"file": r["file"], "start": r["start_line"], "end": r["end_line"], "matched": 0,
                "total": len(lines), "method": "similar", "score": round(float((mat @ v)[i]), 2)}
    except Exception:
        return None


def blame(project: str, file: str, start: int, end: int) -> list[dict]:
    """Commits that wrote lines start..end, most lines first."""
    root = os.path.join(codesearch.REPOS_DIR, project)
    try:
        out = subprocess.run(["git", "-C", root, "blame", "-w", "-M", "-L", f"{start},{end}",
                              "--line-porcelain", "--", file],
                             capture_output=True, text=True, check=True, timeout=30).stdout
    except Exception:
        return []
    commits: dict[str, dict] = {}
    cur = None
    for line in out.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
        if m:
            cur = commits.setdefault(m.group(1), {"hash": m.group(1), "lines": [], "author": "",
                                                   "date": 0.0, "subject": ""})
            cur["lines"].append(int(m.group(2)))
        elif cur is not None:
            if line.startswith("author "):
                cur["author"] = line[7:]
            elif line.startswith("author-time "):
                cur["date"] = float(line[12:])
            elif line.startswith("summary "):
                cur["subject"] = line[8:]
    res = []
    for c in commits.values():
        uncommitted = c["hash"].startswith("0000000")
        c["keys"] = sorted(set(KEY_RE.findall(c["subject"])))
        c["uncommitted"] = uncommitted
        if uncommitted:
            c["subject"] = "Not committed yet (your local changes)"
        res.append(c)
    return sorted(res, key=lambda c: (-len(c["lines"]), -c["date"]))


def analyse(db, snippet: str, project: str = "") -> dict:
    p = codesearch.default_project(project)
    t0 = time.time()
    loc = locate(db, p, snippet)
    if not loc:
        return {"project": p, "location": None, "commits": [], "stories": [], "decisions": [],
                "ms": int((time.time() - t0) * 1000)}
    commits = blame(p, loc["file"], loc["start"], loc["end"])
    # add keys from commit bodies we already imported (subject may lack the key)
    with closing(db()) as con:
        for c in commits:
            r = con.execute("SELECT keys FROM git_commits WHERE project=? AND hash=?", (p, c["hash"])).fetchone()
            if r and r["keys"]:
                c["keys"] = sorted(set(c["keys"]) | set(r["keys"].split(",")))
    weight: dict[str, int] = {}
    for c in commits:
        for k in c["keys"]:
            weight[k] = weight.get(k, 0) + len(c["lines"])
    keys = sorted(weight, key=lambda k: -weight[k])[:3]
    stories, decisions = [], []
    for k in keys:
        row, _ = jira.get_story(db, k)  # cached; fetched once if missing
        if row:
            stories.append({"key": k, "summary": row["summary"], "status": row["status"],
                            "text": row["text"], "lines": weight[k]})
        with closing(db()) as con:
            for d in con.execute("SELECT content, source, created_at FROM task_log WHERE task_key=? "
                                 "AND kind='decision' ORDER BY created_at", (k,)):
                decisions.append({"key": k, **dict(d)})
    # current code of the located lines (with real line numbers)
    code = ""
    try:
        with open(os.path.join(codesearch.REPOS_DIR, p, loc["file"]), encoding="utf-8", errors="replace") as fh:
            flines = fh.read().split("\n")
        code = "\n".join(f"{i:>5}  {flines[i - 1]}" for i in range(loc["start"], min(loc["end"], len(flines)) + 1))
    except OSError:
        pass
    return {"project": p, "location": loc, "code": code, "commits": commits[:8], "stories": stories,
            "decisions": decisions, "ms": int((time.time() - t0) * 1000)}


def to_markdown(a: dict, story_chars: int = 2500) -> str:
    """The package as markdown (for MCP tools and as the local model's prompt)."""
    if not a.get("location"):
        return "Couldn't find this code in the indexed repo (is it committed / indexed? is the project right?)."
    loc = a["location"]
    how = (f"exact match, {loc['matched']}/{loc['total']} lines" if loc["method"] == "exact"
           else f"closest similar code (similarity {loc['score']}) — the pasted code may have changed")
    code = a.get("code") or ""
    cl = code.split("\n")
    if len(cl) > 150:  # whole-file explains: keep the model's input small
        code = "\n".join(cl[:150]) + f"\n… ({len(cl) - 150} more lines)"
    out = [f"# Where: `{loc['file']}` lines {loc['start']}–{loc['end']} ({how})",
           f"```\n{code}\n```" if code else ""]
    if a["commits"]:
        out.append("## Commits that wrote these lines (git blame, most lines first)\n" + "\n".join(
            f"- {len(c['lines'])} line(s) · {time.strftime('%Y-%m-%d', time.localtime(c['date'])) if c['date'] else ''} "
            f"{c['author']}: {c['subject']}" for c in a["commits"]))
    for s in a["stories"]:
        t = s["text"]
        out.append(f"## Story {s['key']} [{s['status']}] — wrote {s['lines']} of these lines\n"
                   + (t[:story_chars] + ("\n…" if len(t) > story_chars else "")))
    if a["decisions"]:
        out.append("## Decisions recorded in the task journal\n" + "\n".join(
            f"- {d['key']}: {d['content']}" for d in a["decisions"]))
    if not a["stories"]:
        out.append("_No Jira ticket found in the commits that wrote these lines._")
    return "\n\n".join(x for x in out if x)


def register(mcp, db) -> None:
    @mcp.tool()
    def explain_snippet(code: str, project: str = "") -> str:
        """The user pasted a piece of code and wants to know what it does and WHY / which
        story it belongs to. Finds it in the repo, runs git blame on exactly those lines, and
        returns the commits that wrote them + their Jira stories + recorded decisions.
        Then explain: what the code does, and why it exists (cite the story and commits)."""
        return to_markdown(analyse(db, code, project))
