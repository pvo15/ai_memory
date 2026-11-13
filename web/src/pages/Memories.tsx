import { Loader2, Lock, RefreshCw, Search, ShieldAlert, Users } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { useAction, useJobs, useMemories, useSetMemoryPrivate, useSyncStatus } from '../api/hooks';
import type { Memory, SyncStatus } from '../api/types';
import { Card, Empty, Tag, useDebounced, useToast } from '../components/ui';
import { ago } from '../lib/format';

type Filter = 'all' | 'shared' | 'private' | 'team';
const FILTERS: Record<Filter, { label: string; match: (m: Memory) => boolean }> = {
    all: { label: 'All', match: () => true },
    shared: { label: 'Mine · shared', match: (m) => !m.author && !m.private },
    private: { label: 'Mine · private', match: (m) => !m.author && !!m.private },
    team: { label: 'From team', match: (m) => !!m.author },
};

function SyncCard({ sync }: { sync?: SyncStatus }) {
    const { data: jobs = {} } = useJobs(3000);
    const action = useAction();
    const toast = useToast();
    if (!sync) return null;
    const running = jobs.memory_sync?.state === 'running' || action.isPending;
    const failed = !!sync.error && (sync.error_at ?? 0) >= (sync.last_at ?? 0);
    return (
        <Card title="Team sync" icon={<Users size={14} />}>
            <div className="row" style={{ alignItems: 'flex-start' }}>
                <div className="muted small" style={{ flex: 1, minWidth: 240 }}>
                    {sync.configured ? (
                        <>
                            As <b>{sync.author}</b> → {sync.repo} ({sync.branch}) · last sync {ago(sync.last_at)}
                            {sync.last_result && <div>{sync.last_result}</div>}
                            {failed && <div style={{ color: 'var(--critical)' }}>Last sync failed: {sync.error}</div>}
                        </>
                    ) : (
                        <>Team sync is {sync.problem}. Set it up in .env (see SETUP.md).</>
                    )}
                    <div style={{ marginTop: 6 }}>
                        {sync.shared} shared · {sync.private} private · {sync.team} from {sync.teammates} teammate(s). New
                        and edited memories wait {sync.grace_min} min before they are shared, so you can still make them
                        private.
                    </div>
                </div>
                {sync.configured && (
                    <button
                        className="btn"
                        disabled={running}
                        onClick={() => action.mutate('memory_sync', { onSuccess: (r) => toast(r.message) })}
                    >
                        {running ? <Loader2 size={14} className="spin" aria-hidden /> : <RefreshCw size={14} aria-hidden />}
                        Sync now
                    </button>
                )}
            </div>
        </Card>
    );
}

function SharingCell({ m }: { m: Memory }) {
    const setPrivate = useSetMemoryPrivate();
    const toast = useToast();
    if (m.author) return <Tag>{m.author}</Tag>;
    if (m.looks_secret && !m.private)
        return (
            <span className="tag" title="Looks like it contains a secret, so sync never shares it. Better: forget it.">
                <ShieldAlert size={12} aria-hidden /> secret · not shared
            </span>
        );
    return (
        <button
            className="btn small"
            disabled={setPrivate.isPending}
            title={m.private ? 'Private: stays on this Mac. Click to share with the team.' : 'Shared with the team. Click to make private.'}
            onClick={() =>
                setPrivate.mutate(
                    { id: m.id, private: !m.private },
                    { onSuccess: (r) => toast(r.message), onError: (e) => toast(`Failed: ${e.message}`) },
                )
            }
        >
            {m.private ? <><Lock size={13} aria-hidden /> Private</> : <><Users size={13} aria-hidden /> Shared</>}
        </button>
    );
}

export function MemoriesPage() {
    const [text, setText] = useState('');
    const [filter, setFilter] = useState<Filter>('all');
    const { data: all = [] } = useMemories(useDebounced(text));
    const { data: sync } = useSyncStatus(5000);
    // A sync finished (button or schedule): reload the list, teammates' memories may have changed.
    const qc = useQueryClient();
    const lastSync = sync?.last_at;
    useEffect(() => {
        if (lastSync) void qc.invalidateQueries({ queryKey: ['memories'] });
    }, [lastSync, qc]);
    const rows = all.filter(FILTERS[filter].match);
    return (
        <div className="page">
            <SyncCard sync={sync} />
            <Card title={`Memories (${rows.length})`}>
                <div className="row" style={{ marginBottom: 10 }}>
                    <Search size={16} className="muted" aria-hidden />
                    <input
                        className="input input-grow"
                        type="search"
                        placeholder="Search memories"
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        aria-label="Search memories"
                    />
                    <div className="segmented" role="tablist" aria-label="Filter memories">
                        {(Object.keys(FILTERS) as Filter[]).map((f) => (
                            <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}>
                                {FILTERS[f].label}
                            </button>
                        ))}
                    </div>
                </div>
                <table>
                    <thead><tr><th>#</th><th>Memory</th><th>Project</th><th>From</th><th>Sharing</th><th>Updated</th></tr></thead>
                    <tbody>
                        {rows.map((m) => (
                            <tr key={m.id}>
                                <td className="muted">{m.id}</td>
                                <td>
                                    {m.content}
                                    {m.tags && (
                                        <div className="row" style={{ gap: 4, marginTop: 4 }}>
                                            {m.tags.split(',').map((t) => <Tag key={t}>{t}</Tag>)}
                                        </div>
                                    )}
                                </td>
                                <td>{m.project || <span className="muted">global</span>}</td>
                                <td>{m.source}</td>
                                <td><SharingCell m={m} /></td>
                                <td>{ago(m.updated_at)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {!rows.length && <Empty>No memories</Empty>}
            </Card>
        </div>
    );
}
