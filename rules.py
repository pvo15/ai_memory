"""
Project rules: architecture, code style, conventions and agent roles, served to ANY AI.

Claude Code loads CLAUDE.md by itself, but other tools (GLM, sub-agents, Codex ...)
don't -- and even Claude benefits from pulling just the relevant section.

Source files (read-only mount of your projects folder -> /repos/<project>/...):
  CLAUDE.md, AGENTS.md, .junie/guidelines.md, docs/**/*.md, .claude/agents/*.md, .claude/rules/**/*.md
Linked repos (PROJECT_GROUPS, e.g. web + API) are read together as one project.
Each file is split into sections at markdown headings and indexed with FTS5.
Before every query, file mtimes are compared, and only changed files are re-indexed --
so edits to CLAUDE.md are visible immediately, without a restart.
"""
import glob
import os
import re
from contextlib import closing

import codesearch

REPOS_DIR = os.environ.get("REPOS_DIR", "/repos")
RULE_GLOBS = os.environ.get(
    "RULES_GLOBS",
    "CLAUDE.md,AGENTS.md,.junie/guidelines.md,docs/**/*.md,.claude/agents/*.md,.claude/rules/**/*.md",
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
        project: repo folder name, e.g. "my-app". Repos linked to it (PROJECT_GROUPS, e.g.
                 frontend + backend) are read together; their paths start with the repo name.
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

        ps = [p for p in codesearch.group_of(project) if os.path.isdir(os.path.join(REPOS_DIR, p))]
        many = len(ps) > 1

        def label(p: str, f: str) -> str:
            return f"{p}/{f}" if many else f

        with closing(db()) as con, con:
            for p in ps:
                sync(con, p)

            if file:
                head, _, rest = file.partition("/")
                fp, ff = (head, rest) if head in ps and rest else (project, file)
                if ff not in rule_files(fp):  # only known rules files, never arbitrary paths
                    return f"{file} is not a rules file of {project}. Call with no topic to list them."
                with open(os.path.join(REPOS_DIR, fp, ff), encoding="utf-8", errors="replace") as fh:
                    return f"# {fp}/{ff}\n\n" + fh.read()[:20000]

            marks = ",".join("?" * len(ps))
            if not topic.strip():
                rows = con.execute(f"SELECT project, file, heading FROM rules_fts WHERE project IN ({marks})",
                                   ps).fetchall()
                if not rows:
                    return f"No rules files found in {', '.join(ps)} ({', '.join(RULE_GLOBS)})."
                toc: dict[str, list[str]] = {}
                for r in rows:
                    toc.setdefault(label(r["project"], r["file"]), []).append(r["heading"])
                return f"# Rules in {' + '.join(ps)}\n" + "\n".join(
                    f"\n## {f}\n" + "\n".join(f"- {h}" for h in hs) for f, hs in toc.items())

            q = fts_query(topic)
            rows = con.execute(
                f"SELECT project, file, heading, body FROM rules_fts WHERE rules_fts MATCH ? "
                f"AND project IN ({marks}) "
                "ORDER BY bm25(rules_fts, 0, 0, 4.0, 1.0) LIMIT ?",  # heading matches weigh 4x
                (q, *ps, max(1, min(int(limit), 15)))).fetchall() if q else []
        if not rows:
            return f"No rule section matches '{topic}'. Call with no topic for the table of contents."
        out = []
        for r in rows:
            body = r["body"]
            name = label(r["project"], r["file"])
            if len(body) > MAX_SECTION_CHARS:
                body = body[:MAX_SECTION_CHARS] + f"\n… _(truncated — use file=\"{name}\")_"
            out.append(f"## {name} › {r['heading']}\n{body}")
        return "\n\n".join(out)
