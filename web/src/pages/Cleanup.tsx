import { CircleCheck, Copy, EyeOff, Hourglass, Pause, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { useCleanup, useDeleteMemory, useSetTaskStatus } from '../api/hooks';
import type { CleanupMemory } from '../api/types';
import { Card, Empty, Tag, useToast } from '../components/ui';
import { ago } from '../lib/format';

/** Delete button for one memory; asks first. Teammates' memories and file copies can't be deleted here. */
function DeleteButton({ m }: { m: CleanupMemory }) {
    const del = useDeleteMemory();
    const toast = useToast();
    if (m.author) return <Tag>{m.author} · read-only</Tag>;
    if (m.from_file)
        return (
            <span className="tag" title="Copied from a Claude Code memory file: delete the file instead.">
                Claude file
            </span>
        );
    return (
        <button
            className="btn small btn-danger"
            disabled={del.isPending}
            data-testid={`cleanup-delete-${m.id}`}
            onClick={() => {
                const shared = !m.private ? ' It is shared, so it is also removed from teammates at the next sync.' : '';
                if (!confirm(`Delete memory [${m.id}]? This can't be undone.${shared}\n\n${m.content.slice(0, 300)}`)) return;
                del.mutate(m.id, { onSuccess: (r) => toast(r.message), onError: (e) => toast(`Failed: ${e.message}`) });
            }}
        >
            <Trash2 size={13} aria-hidden /> Delete
        </button>
    );
}

const CLIP = 280;

function MemoryCell({ m }: { m: CleanupMemory }) {
    const [all, setAll] = useState(false);
    const long = m.content.length > CLIP;
    return (
        <>
            <div className="muted small">
                [{m.id}] {m.project || 'global'} · {m.source || 'unknown'} · saved {ago(m.created_at)}
            </div>
            <div style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-line' }}>
                {long && !all ? `${m.content.slice(0, CLIP).trimEnd()}…` : m.content}
                {long && (
                    <button className="btn small" style={{ marginLeft: 6, padding: '0 6px' }} onClick={() => setAll(!all)}>
                        {all ? 'show less' : 'show all'}
                    </button>
                )}
            </div>
        </>
    );
}

export function CleanupPage() {
    const { data: c, isLoading, error } = useCleanup();
    const setStatus = useSetTaskStatus();
    const toast = useToast();
    if (isLoading) return <div className="page"><Empty>Loading…</Empty></div>;
    if (error || !c) return <div className="page"><Empty>Couldn&apos;t load the clean-up view: {String(error)}</Empty></div>;

    const status = (key: string, s: 'paused' | 'done') =>
        setStatus.mutate({ key, status: s }, { onSuccess: (r) => toast(r.message), onError: (e) => toast(`Failed: ${e.message}`) });

    return (
        <div className="page" data-testid="cleanup-page">
            <div className="muted small">
                Things that probably need a look. Nothing changes until you click; deleting asks first.
            </div>

            <Card title={`Stale tasks (${c.stale_tasks.length})`} icon={<Hourglass size={14} />}>
                <div className="muted small" style={{ marginBottom: 8 }}>
                    Still &quot;active&quot;, but nothing was logged for {c.stale_task_days}+ days. Active tasks are offered at
                    session start, so finish or pause the ones you are not working on.
                </div>
                {c.stale_tasks.length === 0 ? (
                    <Empty>No stale tasks</Empty>
                ) : (
                    <table data-testid="cleanup-stale-tasks">
                        <thead><tr><th>Task</th><th>Last entry</th><th>Updated</th><th /></tr></thead>
                        <tbody>
                            {c.stale_tasks.map((t) => (
                                <tr key={t.key}>
                                    <td>
                                        <b>{t.key}</b> {t.title}
                                        <div className="muted small">{[t.project, t.branch].filter(Boolean).join(' · ')}</div>
                                    </td>
                                    <td className="small" style={{ maxWidth: 420, overflowWrap: 'anywhere' }}>
                                        {t.last ? <>{t.last.kind}: {t.last.content.slice(0, 220)}</> : <span className="muted">—</span>}
                                    </td>
                                    <td>{ago(t.updated_at)}</td>
                                    <td>
                                        <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                                            <button className="btn small" disabled={setStatus.isPending} onClick={() => status(t.key, 'done')}>
                                                <CircleCheck size={13} aria-hidden /> Done
                                            </button>
                                            <button className="btn small" disabled={setStatus.isPending} onClick={() => status(t.key, 'paused')}>
                                                <Pause size={13} aria-hidden /> Pause
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </Card>

            <Card title={`Possible duplicates (${c.duplicates.length})`} icon={<Copy size={14} />}>
                <div className="muted small" style={{ marginBottom: 8 }}>
                    Most words of one memory are also in the other: often the same fact saved twice in different
                    words. Keep the better one and delete the other.
                </div>
                {c.duplicates.length === 0 ? (
                    <Empty>No near-duplicates</Empty>
                ) : (
                    <table data-testid="cleanup-duplicates">
                        <thead><tr><th>Same words</th><th>Memory A</th><th>Memory B</th></tr></thead>
                        <tbody>
                            {c.duplicates.map((d) => (
                                <tr key={`${d.a.id}-${d.b.id}`}>
                                    <td>{Math.round(d.score * 100)}%</td>
                                    {[d.a, d.b].map((m) => (
                                        <td key={m.id} style={{ verticalAlign: 'top' }}>
                                            <MemoryCell m={m} />
                                            <div style={{ marginTop: 6 }}><DeleteButton m={m} /></div>
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </Card>

            <Card title={`Never recalled (${c.never_recalled_total})`} icon={<EyeOff size={14} />}>
                <div className="muted small" style={{ marginBottom: 8 }}>
                    My memories older than {c.never_recalled_days} days that <code>recall</code> has never returned since{' '}
                    {new Date(c.tracking_since * 1000).toLocaleDateString()}. They may still be shown at session start, so
                    check before deleting.
                    {!c.tracking_ready && (
                        <b> Recalls have only been counted since {ago(c.tracking_since)}: this list is not meaningful yet.</b>
                    )}
                </div>
                {c.never_recalled.length === 0 ? (
                    <Empty>Nothing here</Empty>
                ) : (
                    <table data-testid="cleanup-never-recalled">
                        <thead><tr><th>Memory</th><th /></tr></thead>
                        <tbody>
                            {c.never_recalled.map((m) => (
                                <tr key={m.id}>
                                    <td><MemoryCell m={m} /></td>
                                    <td><DeleteButton m={m} /></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
                {c.never_recalled_total > c.never_recalled.length && (
                    <div className="muted small">Showing the oldest {c.never_recalled.length}.</div>
                )}
            </Card>
        </div>
    );
}
