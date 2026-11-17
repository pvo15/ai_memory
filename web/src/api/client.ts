// Every data call goes through here. Normally the browser is logged in by the
// session cookie the server sets when /ui opens (HttpOnly, sent automatically).
// A pasted token is only a fallback; a 401 becomes a typed error that opens it.

const TOKEN_KEY = 'aimem_token';

export class UnauthorizedError extends Error {
    constructor() {
        super('unauthorized');
    }
}

export const tokenStore = {
    get(): string {
        try {
            return localStorage.getItem(TOKEN_KEY) ?? '';
        } catch {
            return '';
        }
    },
    set(token: string) {
        try {
            localStorage.setItem(TOKEN_KEY, token);
        } catch {
            /* private mode: keep it for this tab only */
        }
    },
};

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = tokenStore.get();
    const res = await fetch(path, {
        ...init,
        credentials: 'same-origin',
        headers: { ...init.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
    if (res.status === 401) throw new UnauthorizedError();
    const body = (await res.json()) as T & { error?: string };
    if (!res.ok) throw new Error(body?.error ?? `HTTP ${res.status}`);
    return body;
}

/** POST JSON and receive a text stream; calls onChunk with each piece as it arrives. */
export async function apiStream(path: string, body: unknown, onChunk: (text: string) => void, signal?: AbortSignal) {
    const token = tokenStore.get();
    const res = await fetch(path, {
        method: 'POST',
        credentials: 'same-origin',
        signal,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(body),
    });
    if (res.status === 401) throw new UnauthorizedError();
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        onChunk(dec.decode(value, { stream: true }));
    }
}

export const qs = (params: Record<string, string | number | undefined>) =>
    new URLSearchParams(
        Object.entries(params)
            .filter(([, v]) => v !== undefined && v !== '')
            .map(([k, v]) => [k, String(v)]),
    ).toString();

// ---------------------------------------------------------------- login helpers
/** Is the ai-memory server answering at all? */
export async function serverUp(): Promise<boolean> {
    try {
        return (await fetch('/health', { cache: 'no-store' })).ok;
    } catch {
        return false;
    }
}

/** Does the browser have a working login (cookie or saved token)? */
async function loggedIn(): Promise<boolean> {
    try {
        await apiFetch('/api/overview');
        return true;
    } catch {
        return false;
    }
}

/** Get a fresh login cookie without reloading: opening /ui is what sets it. */
export async function silentLogin(): Promise<boolean> {
    try {
        await fetch('/ui', { cache: 'no-store', credentials: 'same-origin' });
    } catch {
        return false;
    }
    return loggedIn();
}

/** Check a pasted token before saving it: 'ok', 'wrong' or 'offline'. */
export async function checkToken(token: string): Promise<'ok' | 'wrong' | 'offline'> {
    try {
        const res = await fetch('/api/overview', { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
        return res.ok ? 'ok' : res.status === 401 ? 'wrong' : 'offline';
    } catch {
        return 'offline';
    }
}
