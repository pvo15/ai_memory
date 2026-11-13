// "Where does this code come from, and why?" — shown in a popup window.
// Opens with a code snippet (selected lines, a whole result, or pasted code),
// shows location + git blame + Jira story, and streams the local AI explanation.
import { FileSearch, GitCommitHorizontal, Lightbulb, Loader2, Sparkles, Square, Ticket, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { apiStream } from '../api/client';
import { useExplain } from '../api/hooks';
import type { Explanation } from '../api/types';
import { ago } from '../lib/format';
import { Markdown } from './Markdown';
import { Card, Empty, Tag } from './ui';

function AiExplanation({ code, ready, autoStart }: { code: string; ready: boolean; autoStart: boolean }) {
    const [text, setText] = useState('');
    const [state, setState] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
    const abort = useRef<AbortController | null>(null);

    const run = async () => {
        abort.current?.abort();
        abort.current = new AbortController();
        setText('');
        setState('running');
        try {
            await apiStream('/api/explain/ai', { code }, (t) => setText((p) => p + t), abort.current.signal);
            setState('done');
        } catch (e) {
            if ((e as Error).name !== 'AbortError') {
                setText((p) => `${p}\n\n_Error: ${(e as Error).message}_`);
                setState('error');
            }
        }
    };

    // Auto-start exactly once per piece of code (never again on re-renders);
    // closing the popup aborts the request, and the server stops generating.
    const startedFor = useRef<string | null>(null);
    useEffect(() => {
        if (ready && autoStart && startedFor.current !== code) {
            startedFor.current = code;
            void run();
        }
        return () => {
            abort.current?.abort();
            startedFor.current = null;
        };
    }, [ready, autoStart, code]); // eslint-disable-line react-hooks/exhaustive-deps

    return (
        <Card
            title="Explanation · local AI on your GPU"
            icon={<Sparkles size={14} />}
            actions={
                state === 'running' ? (
                    <button className="btn" onClick={() => { abort.current?.abort(); setState('done'); }}>
                        <Square size={13} /> Stop
                    </button>
                ) : (
                    <button className="btn btn-primary" disabled={!ready} onClick={run}>
                        <Sparkles size={14} /> {state === 'idle' ? 'Explain' : 'Explain again'}
                    </button>
                )
            }
        >
            {state === 'idle' && (
                <Empty>“What it does / why it exists / rule it implements”, written offline by Qwen2.5-Coder on your Mac.</Empty>
            )}
            {state === 'running' && !text && (
                <Empty><Loader2 size={16} className="spin" /> Thinking… (first run loads the model, ~5 s)</Empty>
            )}
            {text && <Markdown source={text} />}
        </Card>
    );
}

function Facts({ a }: { a: Explanation }) {
    if (!a.location) {
        return <Card><Empty>Couldn't find this code in the indexed repo. Is it committed, and in an indexed project?</Empty></Card>;
    }
    const loc = a.location;
    return (
        <>
            <Card title="Where it lives" icon={<FileSearch size={14} />}>
                <div className="row">
                    <span className="result-file">
                        {loc.file}
                        <span className="muted">:{loc.start}-{loc.end}</span>
                    </span>
                    <Tag>{loc.method === 'exact' ? `exact · ${loc.matched}/${loc.total} lines` : `similar · ${loc.score}`}</Tag>
                    <span className="muted small">{a.ms} ms</span>
                </div>
                {loc.method === 'similar' && <p className="muted small">No exact match — closest code shown; it may have changed.</p>}
                {a.code && <pre className="code">{a.code}</pre>}
            </Card>
            <Card title="Commits that wrote these lines · git blame" icon={<GitCommitHorizontal size={14} />}>
                {a.commits.length ? (
                    <table>
                        <tbody>
                            {a.commits.map((c) => (
                                <tr key={c.hash}>
                                    <td style={{ width: 70 }}><Tag>{c.lines.length} line{c.lines.length > 1 ? 's' : ''}</Tag></td>
                                    <td>
                                        {c.subject}
                                        <div className="muted small">
                                            {c.uncommitted ? 'local, uncommitted' : `${c.author} · ${ago(c.date)} · ${c.hash.slice(0, 8)}`}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <Empty>No history for these lines.</Empty>
                )}
            </Card>
            {a.stories.map((s) => (
                <Card key={s.key} title={`Story ${s.key} · ${s.status} · wrote ${s.lines} of these lines`} icon={<Ticket size={14} />}>
                    <details open={a.stories.length === 1}>
                        <summary><b>{s.summary}</b></summary>
                        <Markdown source={s.text} />
                    </details>
                </Card>
            ))}
            {!a.stories.length && (
                <Card title="Story" icon={<Ticket size={14} />}>
                    <Empty>The commits that wrote these lines don't mention a Jira key — the reason isn't recorded in Jira.</Empty>
                </Card>
            )}
            {!!a.decisions.length && (
                <Card title="Decisions from the task journal" icon={<Lightbulb size={14} />}>
                    <ul>{a.decisions.map((d, i) => <li key={i}><b>{d.key}</b>: {d.content}</li>)}</ul>
                </Card>
            )}
        </>
    );
}

/** Popup window. `code` = null -> closed; '' -> opens with an empty box to paste into. */
export function ExplainModal({ code, onClose }: { code: string | null; onClose: () => void }) {
    const ref = useRef<HTMLDialogElement>(null);
    const [draft, setDraft] = useState('');
    const [submitted, setSubmitted] = useState('');
    const explain = useExplain();

    useEffect(() => {
        const d = ref.current;
        if (!d) return;
        if (code !== null) {
            if (!d.open) d.showModal();
            setDraft(code);
            setSubmitted(code);
            explain.reset();
            if (code.trim()) explain.mutate(code);
        } else if (d.open) {
            d.close();
        }
    }, [code]); // eslint-disable-line react-hooks/exhaustive-deps

    const submit = () => {
        if (!draft.trim()) return;
        setSubmitted(draft);
        explain.mutate(draft);
    };

    return (
        <dialog
            ref={ref}
            className="modal-wide"
            onClose={onClose}
            onClick={(e) => { if (e.target === ref.current) onClose(); }}  // click on the backdrop
            aria-labelledby="explain-title"
        >
            <div className="row modal-head">
                <h3 id="explain-title" className="row" style={{ margin: 0 }}>
                    <Lightbulb size={18} aria-hidden /> Where is this code from, and why?
                </h3>
                <span className="grow" />
                <button className="btn btn-icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
            </div>
            <div className="modal-body">
                <textarea
                    className="input mono"
                    style={{ width: '100%', minHeight: submitted ? 70 : 150, resize: 'vertical' }}
                    placeholder="Paste a few lines of code…"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(); }}
                    aria-label="Code to explain"
                    spellCheck={false}
                />
                <div className="row" style={{ margin: '8px 0 14px' }}>
                    <button className="btn btn-primary" disabled={!draft.trim() || explain.isPending || draft === submitted && !!explain.data} onClick={submit}>
                        {explain.isPending ? <Loader2 size={14} className="spin" /> : <FileSearch size={14} />} Find story
                    </button>
                    <span className="muted small">⌘ + Enter · whitespace doesn't matter</span>
                </div>
                {explain.isPending && <Empty><Loader2 size={16} className="spin" /> Finding the code and its history…</Empty>}
                {explain.error && <Empty>Error: {explain.error.message}</Empty>}
                {explain.data && (
                    <div className="split">
                        <div style={{ display: 'grid', gap: 14 }}><Facts a={explain.data} /></div>
                        <div className="sticky-modal">
                            <AiExplanation code={submitted} ready={!!explain.data.location} autoStart />
                        </div>
                    </div>
                )}
            </div>
        </dialog>
    );
}
