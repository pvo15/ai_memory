// Health panel + running jobs with Stop buttons (Overview), and a compact header pill.
import { Activity, CircleStop, HeartPulse, Loader2, Octagon } from 'lucide-react';

import { useHealth, useStopJob, type Poll } from '../api/hooks';
import type { Health, RunningJob } from '../api/types';
import { ago, duration } from '../lib/format';
import { Card, Empty, StatusPill, Tag, useToast } from './ui';

const KIND_LABEL: Record<string, string> = {
    code_index: 'Code index', jira_sync: 'Jira sync', backup: 'Backup', explain: 'AI explanation',
};

function StopButton({ job }: { job: RunningJob }) {
    const stop = useStopJob();
    const toast = useToast();
    const stopping = job.state === 'stopping' || stop.isPending;
    return (
        <button
            className="btn small btn-danger"
            disabled={stopping}
            onClick={() => stop.mutate(job.id, { onSuccess: (r) => toast(r.message) })}
            title="Stops safely at the next step; work already saved is kept"
        >
            {stopping ? <Loader2 size={13} className="spin" /> : <CircleStop size={13} />} {stopping ? 'Stopping…' : 'Stop'}
        </button>
    );
}

export function JobsCard({ health }: { health?: Health }) {
    const stopAll = useStopJob();
    const toast = useToast();
    const running = health?.jobs.running ?? [];
    const recent = health?.jobs.recent ?? [];
    return (
        <Card
            title={`Running jobs${running.length ? ` · ${running.length}` : ''}`}
            icon={<Activity size={14} />}
            actions={running.length > 1 ? (
                <button className="btn small btn-danger" onClick={() => stopAll.mutate('all', { onSuccess: (r) => toast(r.message) })}>
                    <Octagon size={13} /> Stop all
                </button>
            ) : undefined}
        >
            {running.length ? (
                <table>
                    <tbody>
                        {running.map((j) => (
                            <tr key={j.id}>
                                <td>
                                    <b>{j.label}</b>
                                    <div className="muted small">
                                        <Loader2 size={11} className="spin" /> {j.progress || 'starting…'} · {duration(Date.now() / 1000 - j.started)} · {j.source}
                                    </div>
                                </td>
                                <td style={{ width: 110, textAlign: 'right' }}><StopButton job={j} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : (
                <Empty>Nothing running. Jobs (re-index, Jira sync, backup, AI explanation) appear here with a Stop button.</Empty>
            )}
            {!!recent.length && (
                <details style={{ marginTop: 10 }}>
                    <summary className="muted">Recent jobs ({recent.length})</summary>
                    <table>
                        <tbody>
                            {recent.map((j) => (
                                <tr key={j.id}>
                                    <td>{j.label}<div className="muted small">{KIND_LABEL[j.kind] ?? j.kind} · {j.source} · {ago(j.finished)}{j.progress ? ` · ${j.progress}` : ''}</div></td>
                                    <td style={{ width: 90 }}>
                                        <Tag>{j.state === 'done' ? '✓ done' : j.state === 'stopped' ? '■ stopped' : j.state === 'error' ? '✕ error' : j.state}</Tag>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </details>
            )}
        </Card>
    );
}

export function HealthCard({ health }: { health?: Health }) {
    if (!health) return <Card title="Health" icon={<HeartPulse size={14} />}><Empty>Checking…</Empty></Card>;
    return (
        <Card title="Health" icon={<HeartPulse size={14} />} actions={<span className="muted small">checked {ago(health.at)}</span>}>
            <table>
                <tbody>
                    {health.checks.map((c) => (
                        <tr key={c.name}>
                            <td style={{ width: 190 }}><StatusPill health={c.status}>{c.name}</StatusPill></td>
                            <td className="small">{c.detail}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </Card>
    );
}

/** Header: overall health + running-job count; click goes to Overview. */
export function HeaderMonitor({ poll }: { poll: Poll }) {
    const { data } = useHealth(poll);
    if (!data) return null;
    const running = data.jobs.running.length;
    const problems = data.checks.filter((c) => c.status === 'critical' || c.status === 'warning');
    const label = data.overall === 'good' ? 'all healthy' : `${problems.length} issue${problems.length > 1 ? 's' : ''}`;
    return (
        <button className="pill pill-btn" onClick={() => (location.hash = 'overview')} title={problems.map((p) => `${p.name}: ${p.detail}`).join('\n') || 'All checks pass'}>
            <StatusPill health={data.overall}>{label}</StatusPill>
            {running > 0 && <span className="small"><Loader2 size={12} className="spin" /> {running} running</span>}
        </button>
    );
}

export function useMonitor(poll: Poll) {
    return useHealth(poll).data;
}
