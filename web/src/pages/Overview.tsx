import { Archive, Brain, Database, FolderGit2, GitCommitHorizontal, ListChecks, Loader2, RefreshCw, Ticket } from 'lucide-react';

import { useAction, useHealth, useJobs, useOverview, useSyncStatus, type Poll } from '../api/hooks';
import type { ActionName, Job } from '../api/types';
import { BarList, WeekColumns } from '../components/charts';
import { HealthCard, JobsCard } from '../components/Monitor';
import { QuickCommandsCard } from './Cheatsheet';
import { Card, StatTile, useToast } from '../components/ui';
import { ago, bytes, dateTime, fmtNum, until } from '../lib/format';

function JobButton({ name, label, job }: { name: ActionName; label: string; job?: Job }) {
    const action = useAction();
    const toast = useToast();
    const running = job?.state === 'running' || action.isPending;
    return (
        <div>
            <button
                className="btn"
                disabled={running}
                onClick={() => action.mutate(name, { onSuccess: (r) => toast(r.message) })}
            >
                {running ? <Loader2 size={14} className="spin" aria-hidden /> : <RefreshCw size={14} aria-hidden />}
                {label}
            </button>
            {job && job.state !== 'running' && (
                <div className="muted small" style={{ marginTop: 4, maxWidth: 220 }}>
                    {job.state === 'error' ? '✕ ' : '✓ '}
                    {String(job.result ?? '').slice(0, 90)} · {ago(job.finished)}
                </div>
            )}
        </div>
    );
}

export function OverviewPage({ poll }: { poll: Poll }) {
    const { data: o } = useOverview(poll);
    const { data: jobs = {} } = useJobs(poll);
    const { data: health } = useHealth(poll);
    const { data: sync } = useSyncStatus(poll);
    if (!o) return null;
    const c = o.counts;
    const codeStatus =
        Object.entries(o.code.status)
            .map(([p, s]) =>
                s.state === 'indexing'
                    ? `${p}: indexing ${fmtNum(s.done)}/${fmtNum(s.total)}`
                    : s.finished
                      ? `${p}: ${s.state}, ${ago(s.finished)}${s.error ? ` — ${s.error}` : ''}`
                      : `${p}: waiting for first run`,
            )
            .join(' · ') || '—';

    return (
        <div className="page">
            <div className="grid-2">
                <HealthCard health={health} />
                <div style={{ display: 'grid', gap: 14, alignContent: 'start' }}>
                    <JobsCard health={health} />
                    <QuickCommandsCard />
                </div>
            </div>
            <div className="grid-tiles">
                <StatTile value={fmtNum(c.memories)} label="memories" sub="facts & preferences" icon={<Brain size={14} />} />
                <StatTile value={fmtNum(c.tasks_active)} label="active tasks" sub={`${fmtNum(c.tasks_total)} total`} icon={<ListChecks size={14} />} />
                <StatTile value={fmtNum(c.jira)} label="Jira issues" sub="cached, searchable" icon={<Ticket size={14} />} />
                <StatTile value={fmtNum(c.code_chunks)} label="code chunks" sub={`${fmtNum(c.code_files)} files`} icon={<FolderGit2 size={14} />} />
                <StatTile value={fmtNum(c.commits)} label="commits" sub={`${fmtNum(c.commits_keyed)} with a ticket key`} icon={<GitCommitHorizontal size={14} />} />
                <StatTile value={bytes(o.server.db_bytes)} label="database" sub={`${fmtNum(o.backup.count)} backups kept`} icon={<Database size={14} />} />
            </div>

            <div className="grid-2">
                <Card title="Schedules & jobs" icon={<Archive size={14} />}>
                    <table>
                        <tbody>
                            <tr>
                                <td><b>Jira sync</b></td>
                                <td>
                                    last {ago(o.jira.last_sync)} <span className="muted">{o.jira.last_result}</span>
                                    <br />
                                    next {until(o.jira.next_sync)}
                                </td>
                                <td><JobButton name="jira_sync" label="Sync now" job={jobs.jira_sync} /></td>
                            </tr>
                            <tr>
                                <td><b>Code index</b></td>
                                <td>
                                    {codeStatus}
                                    <br />
                                    <span className="muted">auto every 10 min · MLX {o.mlx.up ? 'up' : 'DOWN'}</span>
                                </td>
                                <td><JobButton name="code_index" label="Re-index" job={jobs.code_index} /></td>
                            </tr>
                            <tr>
                                <td><b>Team sync</b></td>
                                <td>
                                    {sync?.configured ? (
                                        <>
                                            last {ago(sync.last_at)} <span className="muted">{sync.last_result}</span>
                                            <br />
                                            <span className="muted">
                                                {sync.interval_min > 0 ? `auto every ${sync.interval_min} min` : 'manual only'} · as {sync.author}
                                            </span>
                                        </>
                                    ) : (
                                        <span className="muted">{sync?.problem ?? '…'}</span>
                                    )}
                                </td>
                                <td>{sync?.configured && <JobButton name="memory_sync" label="Sync now" job={jobs.memory_sync} />}</td>
                            </tr>
                            <tr>
                                <td><b>Backup</b></td>
                                <td>
                                    next {dateTime(o.backup.next)}{' '}
                                    <span className="muted">({until(o.backup.next)}, {o.backup.tz})</span>
                                </td>
                                <td><JobButton name="backup_now" label="Backup now" job={jobs.backup_now} /></td>
                            </tr>
                        </tbody>
                    </table>
                </Card>
                <Card title="Commits per week · last 13 weeks">
                    <WeekColumns rows={o.charts.commits_week} unit="commits" />
                </Card>
                <Card title="Jira issues by status">
                    <BarList rows={o.charts.jira_status} unit="issues" />
                </Card>
                <Card title="Jira issues by type">
                    <BarList rows={o.charts.jira_type} unit="issues" />
                </Card>
                <Card title="Most-worked tickets · commits, 13 weeks">
                    <BarList rows={o.charts.top_tickets} unit="commits" showSummary />
                </Card>
                <Card title="Code chunks by folder">
                    <BarList rows={o.charts.code_folders} unit="chunks" />
                </Card>
            </div>
        </div>
    );
}
