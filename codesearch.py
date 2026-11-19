"""
Semantic + keyword code search over your repos (read-only mount: /repos/<project>).

HOW IT WORKS
  index:  git ls-files -> smart chunks -> MLX embeddings (Mac GPU) -> SQLite
  search: query -> embedding -> cosine (numpy)  ┐
          query -> words  -> BM25 (SQLite FTS5) ├-> merged (Reciprocal Rank Fusion)
                                                ┘
WHY EACH PART
  - Smart chunks: cut at functions/components/types, not every N lines, so one
    result = one meaningful unit of code.
  - Embeddings run on the Mac GPU (mlx-server, host.docker.internal:18765). Docker
    can't use the GPU; it only stores vectors and does the fast maths.
  - Hybrid: vectors find MEANING ("where are tokens added"), keywords find exact
    NAMES ("useAuthStore"). Each alone misses things; together they rarely do.
  - Vectors are stored as float16 (2 KB per chunk) and kept in memory as ONE
    normalised numpy matrix, so a search is a single matrix-vector product (~1 ms).
  - Incremental + resumable: only changed files are re-embedded, and each batch is
    committed, so an interrupted index continues where it stopped.
  - Without MLX (Intel Mac, not installed, or MLX=off) the index is built WITHOUT vectors and
    search uses keywords only. When MLX appears later, the missing vectors are added.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from contextlib import closing
from typing import Callable

import numpy as np

REPOS_DIR = os.environ.get("REPOS_DIR", "/repos")
EMBED_URL = os.environ.get("EMBED_URL", "http://host.docker.internal:18765")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ")
# auto = use the MLX GPU server when it answers; off = never (Intel Mac / no GPU server)
MLX_MODE = "off" if os.environ.get("MLX", "auto").strip().lower() in ("off", "0", "false", "no") else "auto"
CODE_PROJECTS = [p.strip() for p in os.environ.get("CODE_PROJECTS", "").split(",") if p.strip()]
# Repos that form ONE product, searched together: "web+api" (several groups: "a+b;c+d").
PROJECT_GROUPS = [[p.strip() for p in g.split("+") if p.strip()]
                  for g in os.environ.get("PROJECT_GROUPS", "").split(";") if "+" in g]
REINDEX_EVERY_S = int(os.environ.get("CODE_REINDEX_SECONDS", "600"))

EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".md", ".css", ".scss", ".html",
        ".yml", ".yaml", ".cs", ".java", ".kt", ".py", ".go", ".sql", ".graphql", ".proto"}
SKIP = re.compile(r"(^|/)(node_modules|dist|build|coverage|bin|obj)/|\.min\.|(^|/)(pnpm-lock\.yaml|package-lock\.json|yarn\.lock)$")
MAX_FILE_BYTES = 200_000
MAX_CHUNK_LINES, WINDOW, OVERLAP, MIN_CHUNK_LINES = 80, 60, 10, 6
BATCH = 16
QUERY_INSTRUCT = "Instruct: Given a code search query, retrieve relevant code snippets\nQuery: "
TEST_FILE = re.compile(r"\.(test|spec)\.[jt]sx?$|/__tests__/|/tests?/|Tests?\.(cs|java|kt)$")
TEST_PENALTY = float(os.environ.get("CODE_TEST_PENALTY", "0.5"))

# Other modules add one-line notes per result file (githistory.py: "Related: PROJ-123 ...").
FILE_NOTES: list[Callable] = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS code_files(
  project TEXT NOT NULL, file TEXT NOT NULL, sha TEXT NOT NULL,
  PRIMARY KEY (project, file)
);
CREATE TABLE IF NOT EXISTS code_chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL, file TEXT NOT NULL,
  start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
  text TEXT NOT NULL,
  vec BLOB                         -- float16 embedding, NULL if MLX was down
);
CREATE INDEX IF NOT EXISTS code_chunks_by_file ON code_chunks(project, file);
CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(
  project UNINDEXED, file UNINDEXED, words, tokenize='unicode61'
);
"""


# ---------------------------------------------------------------- files & chunks
def list_files(project: str) -> list[str]:
    root = os.path.join(REPOS_DIR, project)
    try:  # tracked files only = respects .gitignore
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
        files = [f for f in out.decode(errors="replace").split("\0") if f]
    except Exception:
        files = [os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs]
    keep = []
    for f in files:
        if os.path.splitext(f)[1].lower() not in EXTS or SKIP.search(f):
            continue
        p = os.path.join(root, f)
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= MAX_FILE_BYTES:
                keep.append(f)
        except OSError:
            pass
    return keep


# A line that starts a new top-level unit (function, component, type, class, test ...)
TOP = re.compile(r"^(export\s|async\s+function\s|function\s|class\s|interface\s|enum\s|type\s+\w+|"
                 r"const\s+\w+|let\s+\w+|describe\(|it\(|test\(|def\s|@\w)")
MEMBER = re.compile(r"^\s{0,8}(public|private|protected|internal|static|override)\s.*[({]\s*$")  # C#/Java/Kotlin


def boundaries(lines: list[str], ext: str) -> list[int]:
    if ext == ".md":
        return [i for i, l in enumerate(lines) if re.match(r"^#{1,3}\s", l)]
    if ext in (".json", ".yml", ".yaml", ".css", ".scss", ".html", ".sql"):
        return []
    starts = []
    for i, l in enumerate(lines):
        if TOP.match(l) or (ext in (".cs", ".java", ".kt") and MEMBER.match(l)):
            j = i  # pull leading comments / decorators into the same chunk
            while j > 0 and re.match(r"^\s*(//|/\*|\*|@|#\[)", lines[j - 1]):
                j -= 1
            if not starts or j > starts[-1]:
                starts.append(j)
    return starts


def chunk_file(text: str, ext: str) -> list[tuple[int, int, str]]:
    """-> [(start_line, end_line, text)] with 1-based inclusive lines."""
    lines = text.split("\n")
    starts = [0] + [s for s in boundaries(lines, ext) if s > 0]
    spans = [(s, (starts[i + 1] if i + 1 < len(starts) else len(lines))) for i, s in enumerate(starts)]
    merged: list[list[int]] = []  # glue tiny pieces (imports, one-liners) to the previous one
    for s, e in spans:
        if merged and (e - s < MIN_CHUNK_LINES or merged[-1][1] - merged[-1][0] < MIN_CHUNK_LINES) \
                and e - merged[-1][0] <= MAX_CHUNK_LINES:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    out = []
    for s, e in merged:
        if e - s <= MAX_CHUNK_LINES:
            pieces = [(s, e)]
        else:  # a very long function: overlapping windows
            pieces = [(a, min(a + WINDOW, e)) for a in range(s, e, WINDOW - OVERLAP)]
        for a, b in pieces:
            body = "\n".join(lines[a:b]).strip()
            if body:
                out.append((a + 1, b, body))
    return out


def words(text: str, path: str) -> str:
    """Keyword text for BM25: identifiers split into parts (useAuthStore -> use auth store)."""
    toks = re.findall(r"[A-Za-z][A-Za-z0-9]*", path + " " + text)
    out = []
    for t in toks:
        low = t.lower()
        out.append(low)
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", t).lower().split()
        if len(parts) > 1:
            out.extend(parts)
    return " ".join(out)


# ---------------------------------------------------------------- embeddings (Mac GPU)
def embed(texts: list[str]) -> np.ndarray:
    req = urllib.request.Request(f"{EMBED_URL}/api/embed", method="POST",
                                 data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        vecs = np.asarray(json.load(r)["embeddings"], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


_mlx = {"at": 0.0, "up": False, "why": ""}  # why: "stuck" (port open, no answer) or "stopped"


def mlx_up() -> bool:
    """Is the MLX GPU server usable right now? Cached 10 s, so a missing server costs nothing."""
    if MLX_MODE == "off":
        return False
    if time.time() - _mlx["at"] < 10:
        return _mlx["up"]
    why = ""
    try:
        with urllib.request.urlopen(f"{EMBED_URL}/health", timeout=3) as r:
            up = r.status == 200
    except Exception as e:
        up = False
        timeout = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
        why = "stuck" if timeout else "stopped"
    _mlx.update(at=time.time(), up=up, why=why)
    return up


def mlx_state(db) -> str:
    """'up'; 'down' = it worked before (vectors exist) but doesn't answer now;
    'off' = turned off (MLX=off) or never installed -- a normal mode, not an error."""
    if MLX_MODE == "off":
        return "off"
    if mlx_up():
        return "up"
    with closing(db()) as con:
        had = con.execute("SELECT 1 FROM code_chunks WHERE vec IS NOT NULL LIMIT 1").fetchone()
    return "down" if had else "off"


# ---------------------------------------------------------------- index
_locks: dict[str, threading.Lock] = {}
_status: dict[str, dict] = {}
_cache: dict[str, tuple] = {}  # project -> (ids, files, matrix)
_cache_lock = threading.Lock()


def index_project(db, project: str, force: bool = False) -> dict:
    lock = _locks.setdefault(project, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"error": "already indexing"}
    st = _status[project] = {"state": "indexing", "started": time.time(), "done": 0, "total": 0}
    try:
        use_vec = mlx_up()  # no MLX: index for keyword search only, vectors are added later
        root = os.path.join(REPOS_DIR, project)
        files = list_files(project)
        with closing(db()) as con:
            known = {r["file"]: r["sha"] for r in
                     con.execute("SELECT file, sha FROM code_files WHERE project=?", (project,))}
        pending = []  # (file, sha, chunks)
        for f in files:
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            sha = hashlib.sha1(text.encode()).hexdigest()
            if force or known.get(f) != sha:
                pending.append((f, sha, chunk_file(text, os.path.splitext(f)[1].lower())))
        removed = set(known) - set(files)
        with closing(db()) as con, con:
            for f in removed:
                _delete_file(con, project, f)
        st["total"] = sum(len(c) for _, _, c in pending)

        if pending:  # only real work shows up as a (stoppable) job
            import jobs
            try:
                with jobs.job("code_index", f"Index {project} ({len(pending)} changed files)",
                              "manual" if force else "auto") as j:
                    group, size = [], 0
                    for item in pending + [None]:  # None flushes the last group
                        if item is not None:
                            group.append(item)
                            size += len(item[2])
                        if group and (item is None or size >= BATCH):
                            j.check()  # Stop pressed? end here; saved batches stay saved
                            _embed_and_store(db, project, group, use_vec)
                            st["done"] += size
                            j.progress = f"{st['done']}/{st['total']} chunks"
                            group, size = [], 0
            except jobs.Stopped:
                with _cache_lock:
                    _cache.pop(project, None)
                st.update(state="stopped", finished=time.time(), files=len(files),
                          changed=len(pending), removed=len(removed),
                          error=f"stopped by user after {st['done']}/{st['total']} chunks (next run continues)")
                return st
        added = _add_missing_vectors(db, project) if mlx_up() else 0
        with _cache_lock:
            _cache.pop(project, None)
        st.update(state="ready", finished=time.time(), files=len(files),
                  changed=len(pending), removed=len(removed), vectors_added=added,
                  keyword_only=not mlx_up())
        return st
    except Exception as e:
        st.update(state="error", error=str(e), finished=time.time())
        return st
    finally:
        lock.release()


def _delete_file(con, project: str, f: str) -> None:
    ids = [r[0] for r in con.execute("SELECT id FROM code_chunks WHERE project=? AND file=?", (project, f))]
    con.executemany("DELETE FROM code_fts WHERE rowid=?", [(i,) for i in ids])
    con.execute("DELETE FROM code_chunks WHERE project=? AND file=?", (project, f))
    con.execute("DELETE FROM code_files WHERE project=? AND file=?", (project, f))


def _embed_and_store(db, project: str, group: list, use_vec: bool = True) -> None:
    flat = [(f, c) for f, _, chunks in group for c in chunks]
    vecs: list = [None] * len(flat)
    if use_vec and flat:
        try:
            vecs = list(embed([f"File: {f}\n\n{c[2][:6000]}" for f, c in flat]))
        except Exception:  # MLX went away mid-run: store for keyword search, vectors come later
            _mlx.update(at=time.time(), up=False)
    with closing(db()) as con, con:  # one transaction per batch = resumable
        for f, sha, _ in group:
            _delete_file(con, project, f)
        for (f, (s, e, text)), v in zip(flat, vecs):
            cur = con.execute("INSERT INTO code_chunks(project,file,start_line,end_line,text,vec) "
                              "VALUES (?,?,?,?,?,?)", (project, f, s, e, text, None if v is None else v.astype(np.float16).tobytes()))
            con.execute("INSERT INTO code_fts(rowid, project, file, words) VALUES (?,?,?,?)",
                        (cur.lastrowid, project, f, words(text, f)))
        for f, sha, _ in group:
            con.execute("INSERT OR REPLACE INTO code_files(project,file,sha) VALUES (?,?,?)", (project, f, sha))


def _add_missing_vectors(db, project: str) -> int:
    """MLX is up: embed chunks that were indexed without it (keyword-only). Resumable per batch."""
    with closing(db()) as con:
        todo = con.execute("SELECT id, file, text FROM code_chunks WHERE project=? AND vec IS NULL",
                           (project,)).fetchall()
    if not todo:
        return 0
    import jobs
    done = 0
    try:
        with jobs.job("code_index", f"Add meaning vectors to {project} ({len(todo)} chunks)", "auto") as j:
            for i in range(0, len(todo), BATCH):
                j.check()
                part = todo[i:i + BATCH]
                vecs = embed([f"File: {r['file']}\n\n{r['text'][:6000]}" for r in part])
                with closing(db()) as con, con:
                    con.executemany("UPDATE code_chunks SET vec=? WHERE id=?",
                                    [(v.astype(np.float16).tobytes(), r["id"]) for r, v in zip(part, vecs)])
                done += len(part)
                j.progress = f"{done}/{len(todo)} chunks"
    except Exception:
        pass  # stopped, or MLX went away: the rest is added at a later run
    return done


def _matrix(db, project: str):
    with _cache_lock:
        if project in _cache:
            return _cache[project]
    with closing(db()) as con:
        rows = con.execute("SELECT id, file, vec FROM code_chunks WHERE project=? AND vec IS NOT NULL",
                           (project,)).fetchall()
    if not rows:
        return None
    ids = np.array([r["id"] for r in rows])
    files = [r["file"] for r in rows]
    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float16) for r in rows]).astype(np.float32)
    with _cache_lock:
        _cache[project] = (ids, files, mat)
    return _cache[project]


# ---------------------------------------------------------------- search
def fts_query(q: str) -> str:
    toks = set(words(q, "").split())
    return " OR ".join(f'"{t}"' for t in toks if len(t) > 1)


def search(db, query: str, project: str | list[str], path_filter: str = "", limit: int = 6, k: int = 50):
    """-> (results, note). results: [{id, project, file, start, end, text, sim, rrf}]
    project: one repo, or a list of repos searched as one (same model, so scores compare)."""
    projects = [project] if isinstance(project, str) else list(project)
    note = ""
    ranked: dict[int, float] = {}
    sims: dict[int, float] = {}

    ms = [m for m in (_matrix(db, p) for p in projects) if m is not None] if mlx_up() else []
    if ms:
        try:
            qv = embed([QUERY_INSTRUCT + query])[0]
            ids = np.concatenate([m[0] for m in ms])
            files = [f for m in ms for f in m[1]]
            s = np.concatenate([m[2] @ qv for m in ms])
            if path_filter:
                s = np.where([f.startswith(path_filter) for f in files], s, -1.0)
            top = np.argsort(-s)[:k]
            for rank, i in enumerate(top):
                if s[i] > -1:
                    ranked[int(ids[i])] = ranked.get(int(ids[i]), 0) + 1 / (60 + rank)
                    sims[int(ids[i])] = float(s[i])
        except Exception as e:
            note = f"_Meaning search unavailable ({e.__class__.__name__}: MLX server down?) — keyword results only._"
    elif MLX_MODE == "off" or not mlx_up():
        note = "_Keyword search only (MLX GPU server off)._"
    else:
        note = "_No vectors yet (index not built) — keyword results only._"

    q = fts_query(query)
    if q:
        with closing(db()) as con:
            sql = (f"SELECT rowid FROM code_fts WHERE code_fts MATCH ? "
                   f"AND project IN ({','.join('?' * len(projects))})")
            args = [q, *projects]
            if path_filter:
                sql += " AND file LIKE ?"
                args.append(path_filter + "%")
            rows = con.execute(sql + " ORDER BY bm25(code_fts) LIMIT ?", args + [k]).fetchall()
        for rank, r in enumerate(rows):
            ranked[r[0]] = ranked.get(r[0], 0) + 1 / (60 + rank)

    # Tests repeat the words of the code they test, so they often out-rank it.
    # Push them down unless the question is about tests.
    if not re.search(r"\b(test|tests|spec|mock|coverage|vitest|jest)\b", query, re.I) and ranked:
        with closing(db()) as con:
            q_ids = list(ranked)
            rows = con.execute(f"SELECT id, file FROM code_chunks WHERE id IN ({','.join('?' * len(q_ids))})",
                               q_ids).fetchall()
        for r in rows:
            if TEST_FILE.search(r["file"]):
                ranked[r["id"]] *= TEST_PENALTY

    best = sorted(ranked, key=lambda i: -ranked[i])
    out, per_file = [], {}
    with closing(db()) as con:
        for cid in best:
            r = con.execute("SELECT * FROM code_chunks WHERE id=?", (cid,)).fetchone()
            key = (r["project"], r["file"]) if r else None
            if not r or per_file.get(key, 0) >= 2:  # at most 2 chunks per file
                continue
            per_file[key] = per_file.get(key, 0) + 1
            out.append({"id": cid, "project": r["project"], "file": r["file"],
                        "start": r["start_line"], "end": r["end_line"],
                        "text": r["text"], "sim": sims.get(cid), "rrf": ranked[cid]})
            if len(out) >= limit:
                break
    return out, note


def format_results(db, project: str | list[str], results: list, note: str, max_lines: int = 60) -> str:
    if not results:
        return (note + "\n" if note else "") + "No matches. Try other words, or grep for exact names."
    many = not isinstance(project, str) and len(project) > 1
    parts = [note] if note else []
    with closing(db()) as con:
        for r in results:
            p = r.get("project") or (project if isinstance(project, str) else project[0])
            sim = f"meaning {r['sim']:.2f}" if r["sim"] is not None else "keyword match"
            head = f"## {p + '/' if many else ''}{r['file']}:{r['start']}-{r['end']}  ({sim})"
            notes = [n for n in (fn(con, p, r["file"]) for fn in FILE_NOTES) if n]
            lines = r["text"].split("\n")
            body = "\n".join(lines[:max_lines]) + (f"\n… ({len(lines) - max_lines} more lines)" if len(lines) > max_lines else "")
            parts.append(head + ("\n" + "\n".join(notes) if notes else "") + f"\n```\n{body}\n```")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- file path search
def _subsequence(needle: str, hay: str) -> list[int] | None:
    """Positions of needle's chars appearing in order inside hay ("ptp" in "paymenttemplatespanel")."""
    pos, i = [], 0
    for ch in needle:
        i = hay.find(ch, i)
        if i < 0:
            return None
        pos.append(i)
        i += 1
    return pos


_WORD_START = re.compile(r"(?:^|[._\-\s])([A-Za-z0-9])|(?<=[a-z0-9])([A-Z])")


def _initials(path: str) -> list[tuple[str, list[int]]]:
    """Per path segment (deepest first): the initials of its words and their positions.
    "PaymentTemplatesPanel" -> "ptp"; "useDraftsQueries.ts" -> "udqt"."""
    out, offset = [], 0
    segs = []
    for seg in path.split("/"):
        segs.append((seg, offset))
        offset += len(seg) + 1
    for seg, off in reversed(segs):
        stem = seg.rsplit(".", 1)[0] if "." in seg else seg
        letters, pos = "", []
        for m in _WORD_START.finditer(stem):
            g = 1 if m.group(1) else 2
            letters += m.group(g).lower()
            pos.append(off + m.start(g))
        if letters:
            out.append((letters, pos))
    return out


def _score_term(term: str, path: str) -> tuple[float, list[tuple[int, int]]] | None:
    """Score one search word against a path, like an editor's "Go to file".
    Returns (score, highlight spans in the path) or None if it doesn't match."""
    low = path.lower()
    base_at = low.rfind("/") + 1
    base = low[base_at:]
    stem = base.rsplit(".", 1)[0]
    if stem == term or base == term:
        return 100.0, [(base_at, base_at + len(term))]
    if base.startswith(term):
        return 80.0, [(base_at, base_at + len(term))]
    i = base.find(term)
    if i >= 0:
        return 65.0, [(base_at + i, base_at + i + len(term))]
    # initials of a file/folder name: "ptp" -> PaymentTemplatesPanel
    if len(term) >= 2:
        for depth, (letters, pos) in enumerate(_initials(path)):
            if letters == term or (len(term) >= 3 and letters.startswith(term)):
                score = (72.0 if letters == term else 50.0) - depth * 3
                return score, [(p, p + 1) for p in pos[: len(term)]]
    # folder name match, e.g. "PaymentTemplatesPanel" for .../PaymentTemplatesPanel/index.tsx
    parts = low.split("/")
    for depth, seg in enumerate(reversed(parts[:-1])):
        j = seg.find(term)
        if j >= 0:
            start = low.rfind("/" + seg + "/") + 1 if ("/" + seg + "/") in low else low.find(seg)
            score = (70.0 if seg == term else 55.0) - depth * 3
            return score, [(start + j, start + j + len(term))]
    i = low.find(term)
    if i >= 0:
        return 45.0, [(i, i + len(term))]
    sub = _subsequence(term, base) if len(term) >= 2 else None
    if sub:
        gaps = sub[-1] - sub[0] - len(term) + 1
        return 30.0 - min(gaps, 25), [(base_at + p, base_at + p + 1) for p in sub]
    sub = _subsequence(term, low) if len(term) >= 3 else None
    if sub:
        return 12.0 - min(sub[-1] - sub[0], 10) / 2, [(p, p + 1) for p in sub]
    return None


def search_files(db, project: str, query: str, limit: int = 30) -> list[dict]:
    """Fuzzy file path search over the indexed repo. Every word must match.
    A camelCase word that matches nothing as a whole is retried as its parts:
    "useAuthStore" -> auth + store -> src/entities/auth/model/auth.store.ts."""
    raw_terms = [t for t in re.split(r"\s+", query.strip()) if t]
    if not raw_terms:
        return []
    stop = {"use", "get", "set", "is", "has", "on", "handle"}
    parts_of = {}
    for t in raw_terms:
        ps = [p.lower() for p in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", t)]
        ps = [p for p in ps if p not in stop and len(p) > 1]
        parts_of[t] = ps if len(ps) > 1 or (ps and ps[0] != t.lower()) else []
    with closing(db()) as con:
        files = [r[0] for r in con.execute("SELECT file FROM code_files WHERE project=?", (project,))]
        chunks = dict(con.execute("SELECT file, count(*) FROM code_chunks WHERE project=? GROUP BY file", (project,)).fetchall())
    out = []
    for f in files:
        total, spans = 0.0, []
        for t in raw_terms:
            r = _score_term(t.lower(), f)
            if r is None and parts_of[t]:  # camelCase fallback: every part must match
                sub = [_score_term(p, f) for p in parts_of[t]]
                if all(sub):
                    r = (sum(s[0] for s in sub) / len(sub) * 0.6, [sp for s in sub for sp in s[1]])
            if r is None:
                break
            total += r[0]
            spans += r[1]
        else:
            # shorter paths and non-test files first on ties
            total -= len(f) / 200 + (8 if TEST_FILE.search(f) else 0)
            out.append({"file": f, "score": round(total, 2), "chunks": chunks.get(f, 0),
                        "spans": sorted(set(spans))})
    return sorted(out, key=lambda r: -r["score"])[:limit]


def read_file(project: str, file: str, db=None) -> dict:
    """Content of an INDEXED file only (never an arbitrary path)."""
    if db is not None:
        with closing(db()) as con:
            ok = con.execute("SELECT 1 FROM code_files WHERE project=? AND file=?", (project, file)).fetchone()
        if not ok:
            return {"error": "not an indexed file"}
    root = os.path.realpath(os.path.join(REPOS_DIR, project))
    full = os.path.realpath(os.path.join(root, file))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return {"error": "file not found"}
    with open(full, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return {"file": file, "text": text, "lines": text.count("\n") + 1}


# ---------------------------------------------------------------- MCP tools + background
def default_project(p: str) -> str:
    return p or (CODE_PROJECTS[0] if CODE_PROJECTS else "")


def group_of(p: str) -> list[str]:
    """The project plus the repos linked to it in PROJECT_GROUPS (itself first)."""
    p = default_project(p)
    for g in PROJECT_GROUPS:
        if p in g:
            return [p] + [x for x in g if x != p]
    return [p]


def narrow(projects: list[str], path_filter: str) -> tuple[list[str], str]:
    """path_filter may start with a repo name to search only that repo:
    "Highway.BBP.API" or "Highway.BBP.API/src/Domain"."""
    head, _, rest = (path_filter or "").partition("/")
    return ([head], rest) if head in projects else (projects, path_filter)


def resolve_file(db, projects: list[str], file: str) -> tuple[str, str] | None:
    """(project, file) for a path given plain or as "<repo>/<path>"; first repo that has it."""
    head, _, rest = file.partition("/")
    if head in projects and rest:
        projects, file = [head], rest
    with closing(db()) as con:
        for p in projects:
            if con.execute("SELECT 1 FROM code_files WHERE project=? AND file=?", (p, file)).fetchone():
                return p, file
    return None


def register(mcp, db) -> None:
    with closing(db()) as con, con:
        con.executescript(SCHEMA)

    @mcp.tool()
    def find_files(query: str, project: str = "", limit: int = 20) -> str:
        """Find files by NAME or PATH (fuzzy, like an editor's "Go to file").

        query: part of a file/folder name or path, e.g. "PaymentTemplatesPanel",
               "drafts hook" (all words must match), "ptp" (letters in order).
        Returns repo-relative paths, best first, with the Jira tickets behind each file.
        Use code_search instead when you know WHAT the code does but not its name.
        Linked repos (PROJECT_GROUPS, e.g. web + API) are searched together; paths then
        start with the repo name.
        """
        ps = group_of(project)
        n = max(1, min(int(limit), 50))
        rows = sorted(({**r, "project": p} for p in ps for r in search_files(db, p, query, n)),
                      key=lambda r: -r["score"])[:n]
        if not rows:
            return f"No file path matches '{query}' in {', '.join(ps)}."
        lines = []
        with closing(db()) as con:
            for r in rows:
                notes = [x for x in (fn(con, r["project"], r["file"]) for fn in FILE_NOTES) if x]
                path = f"{r['project']}/{r['file']}" if len(ps) > 1 else r["file"]
                lines.append(f"- `{path}`" + (f"\n  {notes[0]}" if notes else ""))
        return f"{len(rows)} file(s) in {', '.join(ps)}:\n" + "\n".join(lines)

    @mcp.tool()
    def code_search(query: str, project: str = "", path_filter: str = "", limit: int = 6) -> str:
        """Find code by MEANING and by NAME (hybrid search) in a repo.

        query: describe what you look for ("where are auth headers added to requests")
               or name it ("useTemplatesQuery"). Both work.
        project: repo folder, default: the first one in CODE_PROJECTS. Repos linked to it
                 (PROJECT_GROUPS, e.g. frontend + backend) are searched together, so one
                 question finds both sides; each result then starts with its repo name.
        path_filter: e.g. "src/entities"; start it with a repo name to search only that repo
                 ("Highway.BBP.API" or "Highway.BBP.API/src").
        Each result shows file:lines, the code, and (when known) the Jira tickets/commits
        that changed that file -- use `explain_code` for the full "why".
        """
        ps, pf = narrow(group_of(project), path_filter)
        results, note = search(db, query, ps, pf, max(1, min(int(limit), 20)))
        return format_results(db, ps, results, note)

    @mcp.tool()
    def code_index(project: str = "", force: bool = False) -> str:
        """(Re)build the code index for a repo in the background. Normally automatic
        (every 10 min, only changed files). force=True re-embeds everything."""
        p = default_project(project)
        if not os.path.isdir(os.path.join(REPOS_DIR, p)):
            return f"Unknown project {p} (not found under the mounted projects folder)."
        st = _status.get(p)
        if st and st.get("state") == "indexing":
            return f"Already indexing {p}: {st['done']}/{st['total']} chunks."
        threading.Thread(target=index_project, args=(db, p, force), daemon=True).start()
        return f"Indexing {p} started in the background. Check with code_index_status."

    @mcp.tool()
    def code_index_status(project: str = "") -> str:
        """Show code index state: files, chunks, last run, MLX server reachability."""
        p = default_project(project)
        with closing(db()) as con:
            files = con.execute("SELECT count(*) FROM code_files WHERE project=?", (p,)).fetchone()[0]
            chunks = con.execute("SELECT count(*) FROM code_chunks WHERE project=?", (p,)).fetchone()[0]
        st = _status.get(p, {})
        last = ""
        if st:
            last = f"\nLast run: {st.get('state')}"
            if st.get("state") == "indexing":
                last += f" {st['done']}/{st['total']} chunks"
            if st.get("finished"):
                last += f", finished {int(time.time() - st['finished'])}s ago"
                if st.get("changed") is not None:
                    last += f" ({st['changed']} changed files, {st.get('removed', 0)} removed)"
            if st.get("error"):
                last += f"\nError: {st['error']}"
        return (f"{p}: {files} files, {chunks} chunks indexed. MLX server: "
                f"{'up' if mlx_up() else 'off (keyword search only)'} ({EMBED_URL}).{last}")


def start_scheduler(db) -> None:
    """Keep indexes fresh: every CODE_REINDEX_SECONDS re-embed only changed files (HTTP server only)."""
    if REINDEX_EVERY_S <= 0 or not CODE_PROJECTS:
        print("code auto-index: off", flush=True)
        return

    def loop():
        time.sleep(30)
        was_up = None
        while True:
            up = mlx_up()
            if up != was_up:
                print("code auto-index: " + ("MLX up, meaning search on" if up else
                      "keyword search only (MLX " + ("turned off" if MLX_MODE == "off" else "not running") + ")"), flush=True)
                was_up = up
            for p in CODE_PROJECTS:
                if os.path.isdir(os.path.join(REPOS_DIR, p)):
                    st = index_project(db, p)
                    if st.get("changed") or st.get("vectors_added"):
                        print(f"code auto-index {p}: {st.get('changed', 0)} changed files, "
                              f"{st.get('done', 0)} chunks indexed, {st.get('vectors_added', 0)} vectors added", flush=True)
                    elif st.get("state") == "error":
                        print(f"code auto-index {p} failed: {st.get('error')}", flush=True)
            time.sleep(REINDEX_EVERY_S)

    threading.Thread(target=loop, name="code-auto-index", daemon=True).start()
    print(f"code auto-index: {', '.join(CODE_PROJECTS)} every {REINDEX_EVERY_S}s", flush=True)
