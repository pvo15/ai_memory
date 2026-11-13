"""
Project rules: architecture, code style, conventions and agent roles, served to ANY AI.

Claude Code loads CLAUDE.md by itself, but other tools (GLM, sub-agents, Codex ...)
don't -- and even Claude benefits from pulling just the relevant section.

Source files (read-only mount of your projects folder -> /repos/<project>/...):
  CLAUDE.md, AGENTS.md, .junie/guidelines.md, docs/**/*.md, .claude/agents/*.md
Each file is split into sections at markdown headings and indexed with FTS5.
Before every query, file mtimes are compared, and only changed files are re-indexed --
so edits to CLAUDE.md are visible immediately, without a restart.
"""
import glob
import os
import re
from contextlib import closing

REPOS_DIR = os.environ.get("REPOS_DIR", "/repos")
RULE_GLOBS = os.environ.get(
    "RULES_GLOBS",
    "CLAUDE.md,AGENTS.md,.junie/guidelines.md,docs/**/*.md,.claude/agents/*.md",
).split(",")
MAX_SECTION_CHARS = 2500
PROJECT_RE = re.compile(r"^[\w.\-]+$")  # a plain folder name: no "/" or ".."

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules_files(
  project TEXT NOT NULL, file TEXT NOT NULL, mtime REAL NOT NULL,
  PRIMARY KEY (project, file)
);
CREATE VIRTUAL TABLE IF NOT EXISTS rules_fts USING fts5(
  project UNINDEXED, file UNINDEXED, heading, body, tokenize='porter unicode61'
);
"""


def fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:20])


def rule_files(project: str) -> list[str]:
    root = os.path.join(REPOS_DIR, project)
    found: list[str] = []
    for pattern in RULE_GLOBS:
        for path in sorted(glob.glob(os.path.join(root, pattern.strip()), recursive=True)):
            if os.path.isfile(path) and path not in found:
                found.append(path)
    return [os.path.relpath(p, root) for p in found]


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown at #, ## and ### headings -> [(heading path, body)]."""
    sections, stack, body = [], [], []
    def flush():
        content = "\n".join(body).strip()
        if content:
            sections.append((" > ".join(stack) or "(top)", content))
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            body = []
            level = len(m.group(1))
            stack = stack[: level - 1] + [m.group(2).strip()]
        else:
            body.append(line)
    flush()
    return sections


def sync(con, project: str) -> int:
    """Re-index files whose mtime changed; drop deleted ones. Returns files re-indexed."""
    root = os.path.join(REPOS_DIR, project)
    current = {f: os.path.getmtime(os.path.join(root, f)) for f in rule_files(project)}
    known = {r["file"]: r["mtime"] for r in
             con.execute("SELECT file, mtime FROM rules_files WHERE project=?", (project,))}
    changed = 0
    for f in set(known) - set(current):
        con.execute("DELETE FROM rules_fts WHERE project=? AND file=?", (project, f))
        con.execute("DELETE FROM rules_files WHERE project=? AND file=?", (project, f))
    for f, mtime in current.items():
        if known.get(f) == mtime:
            continue
        with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
            sections = split_sections(fh.read())
        con.execute("DELETE FROM rules_fts WHERE project=? AND file=?", (project, f))
        con.executemany("INSERT INTO rules_fts(project, file, heading, body) VALUES (?,?,?,?)",
                        [(project, f, h, b) for h, b in sections])
        con.execute("INSERT OR REPLACE INTO rules_files(project, file, mtime) VALUES (?,?,?)",
                    (project, f, mtime))
        changed += 1
    return changed


def register(mcp, db) -> None:
    with closing(db()) as con, con:
        con.executescript(SCHEMA)

    @mcp.tool()
    def project_rules(project: str, topic: str = "", file: str = "", limit: int = 5) -> str:
        """Get the project's rules: architecture, code style, conventions, testing, agent roles.

        Use BEFORE writing code in a project, and whenever unsure how something is done there.
        project: repo folder name, e.g. "my-app".
        topic: what you need, e.g. "form validation", "sx spacing", "test coverage", "api requests".
               Returns the best-matching sections. Empty topic = table of contents.
        file: return one whole rules file instead (path as listed in the table of contents).
        """
        if not PROJECT_RE.match(project or ""):
            return "project must be a repo folder name, e.g. my-app"
        if not os.path.isdir(os.path.join(REPOS_DIR, project)):
            avail = sorted(d for d in os.listdir(REPOS_DIR)
                           if not d.startswith(".") and os.path.isdir(os.path.join(REPOS_DIR, d)))[:40] \
                if os.path.isdir(REPOS_DIR) else []
            return f"Unknown project {project}. Available: {', '.join(avail) or '(none mounted)'}"

        with closing(db()) as con, con:
            sync(con, project)

            if file:
                if file not in rule_files(project):  # only known rules files, never arbitrary paths
                    return f"{file} is not a rules file of {project}. Call with no topic to list them."
                with open(os.path.join(REPOS_DIR, project, file), encoding="utf-8", errors="replace") as fh:
                    return f"# {project}/{file}\n\n" + fh.read()[:20000]

            if not topic.strip():
                rows = con.execute("SELECT file, heading FROM rules_fts WHERE project=?", (project,)).fetchall()
                if not rows:
                    return f"No rules files found in {project} ({', '.join(RULE_GLOBS)})."
                toc: dict[str, list[str]] = {}
                for r in rows:
                    toc.setdefault(r["file"], []).append(r["heading"])
                return f"# Rules in {project}\n" + "\n".join(
                    f"\n## {f}\n" + "\n".join(f"- {h}" for h in hs) for f, hs in toc.items())

            q = fts_query(topic)
            rows = con.execute(
                "SELECT file, heading, body FROM rules_fts WHERE rules_fts MATCH ? AND project=? "
                "ORDER BY bm25(rules_fts, 0, 0, 4.0, 1.0) LIMIT ?",  # heading matches weigh 4x
                (q, project, max(1, min(int(limit), 15)))).fetchall() if q else []
        if not rows:
            return f"No rule section matches '{topic}'. Call with no topic for the table of contents."
        out = []
        for r in rows:
            body = r["body"]
            if len(body) > MAX_SECTION_CHARS:
                body = body[:MAX_SECTION_CHARS] + f"\n… _(truncated — use file=\"{r['file']}\")_"
            out.append(f"## {r['file']} › {r['heading']}\n{body}")
        return "\n\n".join(out)
