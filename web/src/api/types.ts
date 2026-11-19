// Shapes returned by the Python API in ../dashboard.py

export interface Bar {
    label: string;
    n: number;
    summary?: string | null;
}

export interface CodeIndexStatus {
    state?: 'indexing' | 'ready' | 'error';
    done?: number;
    total?: number;
    finished?: number;
    changed?: number;
    error?: string;
}

/** A repo folder ai-memory reads (CODE_PROJECTS), read-only. */
export interface ConnectedRepo {
    name: string;
    /** Folder on the Mac. */
    path: string;
    /** false: in CODE_PROJECTS but no such folder. */
    found: boolean;
    branch: string;
    files: number;
    chunks: number;
    /** chunks with a meaning vector (MLX) */
    vectors: number;
    commits: number;
    last_commit: number | null;
    rules_files: number;
    /** repos searched together with this one (PROJECT_GROUPS) */
    linked: string[];
    index: CodeIndexStatus;
}

export interface Overview {
    counts: {
        memories: number;
        tasks_active: number;
        tasks_total: number;
        jira: number;
        code_files: number;
        code_chunks: number;
        commits: number;
        commits_keyed: number;
    };
    server: { uptime_s: number; db_bytes: number };
    /** state: 'up' | 'down' (worked before, not answering) | 'off' (MLX=off or not installed: keyword search). */
    mlx: { up: boolean; state: 'up' | 'down' | 'off'; mode: 'auto' | 'off'; url: string };
    jira: { configured: boolean; last_sync: number | null; last_result: string; next_sync: number | null };
    backup: { next: number; tz: string; count: number };
    code: { projects: string[]; status: Record<string, CodeIndexStatus> };
    repos: ConnectedRepo[];
    charts: {
        jira_status: Bar[];
        jira_type: Bar[];
        commits_week: Bar[];
        code_folders: Bar[];
        top_tickets: Bar[];
    };
}

export interface JiraRow {
    key: string;
    summary: string;
    status: string;
    type: string | null;
    updated: string | null;
    fetched_at: number;
}

export interface JiraDetail {
    story: { key: string; summary: string; status: string; text: string; fetched_at: number } | null;
    commits: { hash: string; date: number; author: string; subject: string }[];
}

export interface Memory {
    id: number;
    content: string;
    tags: string;
    project: string;
    source: string;
    updated_at: number;
    /** 1 = stays on this Mac; 0 = shared with the team at the next sync. */
    private: number;
    /** '' = written here; otherwise the teammate it was synced from (read-only). */
    author: string;
    /** Looks like it contains a credential: never exported by team sync. */
    looks_secret?: boolean;
}

export interface SyncStatus {
    configured: boolean;
    problem: string;
    author: string;
    repo: string;
    branch: string;
    interval_min: number;
    grace_min: number;
    last_at: number | null;
    last_result: string;
    error: string;
    error_at: number | null;
    shared: number;
    private: number;
    team: number;
    teammates: number;
}

export interface CodeResult {
    id: number;
    file: string;
    start: number;
    end: number;
    text: string;
    sim: number | null;
    rrf: number;
    why: string;
}

export interface CodeSearch {
    results: CodeResult[];
    note: string;
    ms: number;
}

export interface BackupFile {
    file: string;
    bytes: number;
    mtime: number;
}

export type LogLevel = 'info' | 'warn' | 'error';

export interface LogLine {
    t: number | null;
    src: string;
    line: string;
    level: LogLevel;
}

export interface Job {
    state: 'running' | 'done' | 'error';
    started?: number;
    finished?: number;
    result?: string;
}

export type ActionName = 'jira_sync' | 'code_index' | 'backup_now' | 'memory_sync';

export interface BlameCommit {
    hash: string;
    lines: number[];
    author: string;
    date: number;
    subject: string;
    keys: string[];
    uncommitted: boolean;
}

export interface Explanation {
    project: string;
    location: {
        file: string;
        start: number;
        end: number;
        matched: number;
        total: number;
        method: 'exact' | 'similar';
        score: number;
    } | null;
    code?: string;
    commits: BlameCommit[];
    stories: { key: string; summary: string; status: string; text: string; lines: number }[];
    decisions: { key: string; content: string; source: string; created_at: number }[];
    ms: number;
}

export interface FileHit {
    file: string;
    score: number;
    chunks: number;
    spans: [number, number][];
    why: string;
}

export interface FileView {
    file: string;
    text: string;
    lines: number;
    why: string;
    commits: { hash: string; date: number; author: string; subject: string }[];
    error?: string;
}

export type HealthStatus = 'good' | 'warning' | 'critical' | 'off';

export interface RunningJob {
    id: number;
    kind: 'code_index' | 'jira_sync' | 'backup' | 'explain' | string;
    label: string;
    source: string;
    state: 'running' | 'stopping' | 'done' | 'stopped' | 'error';
    started: number;
    finished?: number;
    progress: string;
    error?: string;
}

export interface Health {
    overall: HealthStatus;
    checks: { name: string; status: HealthStatus; detail: string }[];
    jobs: { running: RunningJob[]; recent: RunningJob[] };
    at: number;
}
