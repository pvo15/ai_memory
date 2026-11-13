// "Go to file": fuzzy path search + a file viewer. Lines selected in the viewer
// can be explained like search results (the parent page handles the selection).
import { FileCode2, GitCommitHorizontal, Lightbulb, Search } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { useFile, useFindFiles } from '../api/hooks';
import type { FileHit } from '../api/types';
import { ago } from '../lib/format';
import { Card, Empty, Tag, useDebounced } from './ui';

/** Render a path with the matched characters highlighted. */
function Highlighted({ path, spans }: { path: string; spans: [number, number][] }) {
    const out: ReactNode[] = [];
    let at = 0;
    spans.forEach(([s, e], i) => {
        if (s < at) return;
        if (s > at) out.push(path.slice(at, s));
        out.push(<mark key={i} className="hl">{path.slice(s, e)}</mark>);
        at = e;
    });
    out.push(path.slice(at));
    return (
        <span className="mono path-cell" title={path}>
            {out}
        </span>
    );
}

const cleanWhy = (w: string) => w.replace(/ — `explain_code` for details/, '').replace(/^_|_$/g, '');

function FileViewer({ path, onExplain }: { path: string; onExplain: (code: string) => void }) {
    const { data, isFetching } = useFile(path);
    if (isFetching && !data) return <Card><Empty>Loading…</Empty></Card>;
    if (!data) return null;
    if (data.error) return <Card><Empty>{data.error}</Empty></Card>;
    const lines = data.text.split('\n');
    return (
        <Card
            title={<span className="mono path-title">{data.file}</span>}
            icon={<FileCode2 size={14} />}
            actions={
                <button className="btn small" onClick={() => onExplain(data.text)} title="Which stories shaped this file?">
                    <Lightbulb size={13} /> Explain file
                </button>
            }
        >
            {data.why && <div className="why" style={{ marginBottom: 8 }}>{cleanWhy(data.why)}</div>}
            <div className="file-view">
                <pre className="code gutter" aria-hidden>{lines.map((_, i) => i + 1).join('\n')}</pre>
                <pre className="code selectable" data-explainable>{data.text}</pre>
            </div>
            <p className="muted small">{data.lines} lines · select lines to see which story and commits wrote them</p>
            {!!data.commits.length && (
                <>
                    <h2 className="card-title" style={{ marginTop: 14 }}><GitCommitHorizontal size={14} /> Recent commits</h2>
                    <table>
                        <tbody>
                            {data.commits.map((c) => (
                                <tr key={c.hash}>
                                    <td className="mono muted" style={{ width: 80 }}>{c.hash.slice(0, 8)}</td>
                                    <td>{c.subject}<div className="muted small">{c.author} · {ago(c.date)}</div></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </>
            )}
        </Card>
    );
}

export function FileSearch({ onExplain, onSearchCode }: { onExplain: (code: string) => void; onSearchCode: (q: string) => void }) {
    const [text, setText] = useState('');
    const [open, setOpen] = useState<string | null>(null);
    const q = useDebounced(text, 200);
    const { data, isFetching } = useFindFiles(q);

    return (
        <>
            <Card>
                <div className="row">
                    <Search size={16} className="muted" aria-hidden />
                    <input
                        className="input input-grow mono"
                        type="search"
                        autoFocus
                        placeholder='File name or path — "PaymentTemplatesPanel", "drafts hook", "ptp"'
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter' && data?.results[0]) setOpen(data.results[0].file); }}
                        aria-label="File path search"
                    />
                </div>
                <p className="muted small" style={{ margin: '8px 0 0' }}>
                    Like “Go to file”: name, folder or path parts; several words must all match; letters in order work
                    too. Enter opens the first result.
                </p>
            </Card>
            <div className="split">
                <Card title={data ? `${data.results.length} of ${data.total} files · ${data.ms} ms${isFetching ? ' …' : ''}` : 'Files'}>
                    {!q.trim() && <Empty>Type part of a file name or path.</Empty>}
                    {q.trim() && data && !data.results.length && (
                        <Empty>
                            No file path matches “{q}”.{' '}
                            <button className="btn small" onClick={() => onSearchCode(q)}>
                                <Search size={13} /> Search the code for it
                            </button>
                        </Empty>
                    )}
                    {!!data?.results.length && (
                        <table>
                            <tbody>
                                {data.results.map((r: FileHit) => (
                                    <tr key={r.file} className={`clickable ${r.file === open ? 'selected' : ''}`} onClick={() => setOpen(r.file)}>
                                        <td>
                                            <Highlighted path={r.file} spans={r.spans} />
                                            {r.why && <div className="why">{cleanWhy(r.why)}</div>}
                                        </td>
                                        <td style={{ width: 70 }}><Tag>{r.chunks} chunk{r.chunks === 1 ? '' : 's'}</Tag></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </Card>
                <div className="sticky">
                    {open ? <FileViewer path={open} onExplain={onExplain} /> : <Card><Empty>Click a file to open it.</Empty></Card>}
                </div>
            </div>
        </>
    );
}
