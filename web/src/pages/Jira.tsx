import { Search } from 'lucide-react';
import { useState } from 'react';

import { useJira, useJiraStory, useOverview } from '../api/hooks';
import { Markdown } from '../components/Markdown';
import { Card, Empty, Tag, useDebounced } from '../components/ui';
import { ago } from '../lib/format';

export function JiraPage() {
    const [text, setText] = useState('');
    const [status, setStatus] = useState('');
    const [key, setKey] = useState<string | null>(null);
    const q = useDebounced(text);
    const { data: o } = useOverview(false);
    const { data: rows = [], isFetching } = useJira(q, q ? '' : status);
    const { data: story } = useJiraStory(key);

    return (
        <div className="page">
            <Card>
                <div className="row">
                    <Search size={16} className="muted" aria-hidden />
                    <input
                        className="input input-grow"
                        type="search"
                        placeholder="Search stories — IBAN, templates, FX rate…"
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        aria-label="Search Jira stories"
                    />
                    <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} disabled={!!q} aria-label="Status">
                        <option value="">All statuses</option>
                        {o?.charts.jira_status.map((s) => (
                            <option key={s.label} value={s.label}>{s.label} ({s.n})</option>
                        ))}
                    </select>
                </div>
            </Card>
            <div className="split">
                <Card title={`${rows.length} issues${isFetching ? ' …' : ''}`}>
                    <table>
                        <thead><tr><th>Key</th><th>Summary</th><th>Status</th></tr></thead>
                        <tbody>
                            {rows.map((r) => (
                                <tr key={r.key} className={`clickable ${r.key === key ? 'selected' : ''}`} onClick={() => setKey(r.key)}>
                                    <td className="mono">{r.key}</td>
                                    <td>{r.summary}<div className="muted small">{r.type}</div></td>
                                    <td><Tag>{r.status}</Tag></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {!rows.length && <Empty>No matches</Empty>}
                </Card>
                <Card title={key ? `${key} · ${story?.commits.length ?? 0} commits` : 'Story'} className="sticky">
                    {!key && <Empty>Select an issue to read it, with the commits that implemented it.</Empty>}
                    {story?.story && <Markdown source={story.story.text} />}
                    {story && !story.story && <Empty>Not cached.</Empty>}
                    {!!story?.commits.length && (
                        <>
                            <h2 className="card-title" style={{ marginTop: 16 }}>Commits</h2>
                            <table>
                                <tbody>
                                    {story.commits.map((c) => (
                                        <tr key={c.hash}>
                                            <td className="mono muted">{c.hash.slice(0, 8)}</td>
                                            <td>{c.subject}<div className="muted small">{c.author} · {ago(c.date)}</div></td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </>
                    )}
                </Card>
            </div>
        </div>
    );
}
