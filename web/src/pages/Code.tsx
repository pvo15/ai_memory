import { ClipboardPaste, FileCode2, Lightbulb, Search, TextSearch } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { useCodeSearch, useOverview } from '../api/hooks';
import type { CodeResult } from '../api/types';
import { ExplainModal } from '../components/Explain';
import { FileSearch } from '../components/FileSearch';
import { Card, Empty, Tag } from '../components/ui';
import { dedent } from '../lib/format';

const PREVIEW_LINES = 18;

/** The selected text inside a <pre>, widened to whole lines (a half-selected first or
 *  last line would otherwise never match the repo exactly). */
function selectedLines(pre: HTMLElement, range: Range): string {
    const before = document.createRange();
    before.selectNodeContents(pre);
    before.setEnd(range.startContainer, range.startOffset);
    const full = pre.textContent ?? '';
    let start = before.toString().length;
    let end = start + range.toString().length;
    start = full.lastIndexOf('\n', start - 1) + 1;
    const nl = full.indexOf('\n', Math.max(end - 1, start));
    end = nl === -1 ? full.length : nl;
    return full.slice(start, end).replace(/\n… \d+ more lines$/, '');
}

interface Pop { x: number; y: number; code: string; lines: number }

function Result({ r, onExplain }: { r: CodeResult; onExplain: (code: string) => void }) {
    const [open, setOpen] = useState(false);
    const lines = dedent(r.text).split('\n');
    const shown = open ? lines : lines.slice(0, PREVIEW_LINES);
    const why = r.why.replace(/ — `explain_code` for details/, '').replace(/^_|_$/g, '');
    return (
        <div className="result">
            <div className="row">
                <span className="result-file">
                    {r.file}
                    <span className="muted">:{r.start}-{r.end}</span>
                </span>
                <Tag>{r.sim != null ? `meaning ${r.sim.toFixed(2)}` : 'name match'}</Tag>
                <span className="grow" />
                <button className="btn small" onClick={() => onExplain(r.text)} title="Which story is this code from, and why?">
                    <Lightbulb size={13} /> Explain this
                </button>
            </div>
            {why && <div className="why">{why}</div>}
            <pre className="code selectable" data-explainable>{shown.join('\n')}</pre>
            {lines.length > PREVIEW_LINES && (
                <button className="btn small" style={{ marginTop: 6 }} onClick={() => setOpen(!open)}>
                    {open ? 'Show less' : `Show ${lines.length - PREVIEW_LINES} more lines`}
                </button>
            )}
        </div>
    );
}

export function CodePage() {
    const meaning = useOverview(false).data?.mlx.state === 'up'; // without MLX: names/keywords only
    const [text, setText] = useState('');
    const [path, setPath] = useState('');
    const [submitted, setSubmitted] = useState({ q: '', path: '' });
    const [explainCode, setExplainCode] = useState<string | null>(null);
    const [pop, setPop] = useState<Pop | null>(null);
    const results = useRef<HTMLDivElement>(null);
    const [mode, setMode] = useState<'code' | 'files'>(() => {
        try {
            return sessionStorage.getItem('aimem_code_mode') === 'files' ? 'files' : 'code';
        } catch {
            return 'code';
        }
    });
    const switchMode = (m: 'code' | 'files') => {
        setMode(m);
        setPop(null);
        try {
            sessionStorage.setItem('aimem_code_mode', m);
        } catch {
            /* ignore */
        }
    };
    const { data, isFetching, error } = useCodeSearch(submitted.q, submitted.path);

    // Show the "Explain lines" popup when code inside a result is selected.
    const onSelect = useCallback(() => {
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed || !sel.rangeCount) return setPop(null);
        const range = sel.getRangeAt(0);
        const pre = (range.commonAncestorContainer instanceof Element
            ? range.commonAncestorContainer
            : range.commonAncestorContainer.parentElement)?.closest<HTMLElement>('pre[data-explainable]');
        if (!pre || !results.current?.contains(pre)) return setPop(null);
        const code = selectedLines(pre, range);
        if (!code.trim()) return setPop(null);
        const rect = range.getBoundingClientRect();
        setPop({
            x: Math.min(rect.right + 8, window.innerWidth - 190),
            y: Math.max(rect.top - 44, 8),
            code,
            lines: code.split('\n').length,
        });
    }, []);

    useEffect(() => {
        const hide = () => setPop(null);
        addEventListener('scroll', hide, true);
        return () => removeEventListener('scroll', hide, true);
    }, []);

    const openExplain = (code: string) => {
        setPop(null);
        window.getSelection()?.removeAllRanges();
        setExplainCode(code);
    };

    return (
        <div className="page" ref={results} onMouseUp={onSelect} onKeyUp={onSelect}>
            <div className="segmented" role="tablist" aria-label="Search mode">
                <button role="tab" aria-selected={mode === 'code'} onClick={() => switchMode('code')}>
                    <TextSearch size={14} /> Code <span className="muted small">{meaning ? 'by meaning or name' : 'by name / keywords'}</span>
                </button>
                <button role="tab" aria-selected={mode === 'files'} onClick={() => switchMode('files')}>
                    <FileCode2 size={14} /> Files <span className="muted small">by file path</span>
                </button>
            </div>
            {mode === 'files' && (
                <FileSearch
                    onExplain={openExplain}
                    onSearchCode={(q) => { switchMode('code'); setText(q); setSubmitted({ q, path: '' }); }}
                />
            )}
            {mode === 'code' && <>
            <Card>
                <form className="row" onSubmit={(e) => { e.preventDefault(); setSubmitted({ q: text, path }); }}>
                    <Search size={16} className="muted" aria-hidden />
                    <input
                        className="input input-grow"
                        type="search"
                        placeholder={meaning ? 'By meaning or name — "where are auth headers added", "useAuthStore"' : 'By name or keywords — "useAuthStore", "auth headers"'}
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        aria-label="Code search"
                    />
                    <input
                        className="input"
                        style={{ width: 200 }}
                        placeholder="path filter, e.g. src/entities"
                        value={path}
                        onChange={(e) => setPath(e.target.value)}
                        aria-label="Path filter"
                    />
                    <button className="btn btn-primary" type="submit" disabled={!text.trim()}>Search</button>
                    <button className="btn" type="button" onClick={() => openExplain('')} title="Paste any code and find its story">
                        <ClipboardPaste size={14} /> Paste code
                    </button>
                </form>
                <p className="muted small" style={{ margin: '8px 0 0' }}>
                    Tip: <b>select lines</b> in any result to see which story and commits wrote them, and why.
                </p>
            </Card>
            <Card title={data ? `${data.results.length} results · ${data.ms} ms` : 'Results'}>
                <div>
                    {!submitted.q && <Empty>Search, then select lines in a result to explain them.</Empty>}
                    {submitted.q && isFetching && <Empty>Searching…</Empty>}
                    {error && <Empty>{String(error)}</Empty>}
                    {data?.note && <p className="muted">{data.note.replace(/_/g, '')}</p>}
                    {data && !isFetching && data.results.map((r) => <Result key={r.id} r={r} onExplain={openExplain} />)}
                    {data && !isFetching && !data.results.length && <Empty>No matches</Empty>}
                </div>
            </Card>
            </>}

            {pop && (
                <button
                    className="btn btn-primary sel-pop"
                    style={{ left: pop.x, top: pop.y }}
                    onMouseDown={(e) => e.preventDefault()}  // keep the selection while clicking
                    onClick={() => openExplain(pop.code)}
                >
                    <Lightbulb size={14} /> Explain {pop.lines} line{pop.lines > 1 ? 's' : ''}
                </button>
            )}
            <ExplainModal code={explainCode} onClose={() => setExplainCode(null)} />
        </div>
    );
}
