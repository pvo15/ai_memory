const now = () => Date.now() / 1000;

export const fmtNum = (n: number | null | undefined) => Number(n ?? 0).toLocaleString();

export const duration = (s: number) => {
    if (s < 60) return `${Math.floor(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
};

export const ago = (t: number | null | undefined) =>
    t ? (now() - t < 60 ? 'just now' : `${duration(now() - t).split(' ')[0]} ago`) : 'never';

export const until = (t: number | null | undefined) => {
    if (!t) return '—';
    const s = t - now();
    return s <= 0 ? 'due now' : `in ${duration(s)}`;
};

export const dateTime = (t: number | null | undefined) =>
    t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';

export const timeOnly = (t: number) => new Date(t * 1000).toLocaleTimeString();

export const bytes = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`);

/** Chunks are stored with their first line trimmed; re-align the rest to their common indent. */
export function dedent(text: string): string {
    const lines = text.split('\n');
    const indents = lines
        .slice(1)
        .filter((l) => l.trim())
        .map((l) => l.match(/^ */)![0].length);
    const min = indents.length ? Math.min(...indents) : 0;
    return [lines[0], ...lines.slice(1).map((l) => l.slice(Math.min(min, l.match(/^ */)![0].length)))].join('\n');
}
