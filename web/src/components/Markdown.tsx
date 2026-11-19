// A tiny, SAFE markdown renderer for briefings and Jira stories: it builds React
// elements (never innerHTML), so text from Jira or an AI can't inject markup.
// Supports: #-headings, - lists, | tables |, ``` fences, `code`, **bold**, _(muted notes)_.
import type { ReactNode } from 'react';

function inline(text: string): ReactNode[] {
    const out: ReactNode[] = [];
    const re = /`([^`]+)`|\*\*([^*]+)\*\*|_\(([^)]*)\)_/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
        if (m.index > last) out.push(text.slice(last, m.index));
        if (m[1] !== undefined) out.push(<code key={m.index}>{m[1]}</code>);
        else if (m[2] !== undefined) out.push(<b key={m.index}>{m[2]}</b>);
        else out.push(<span key={m.index} className="muted">({m[3]})</span>);
        last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
}

export function Markdown({ source }: { source: string }) {
    const blocks: ReactNode[] = [];
    let list: ReactNode[] = [];
    let fence: string[] | null = null;
    let rows: string[][] = [];
    const flushList = () => {
        if (list.length) blocks.push(<ul key={`ul${blocks.length}`}>{list}</ul>);
        list = [];
    };
    const flushTable = () => {
        if (rows.length) {
            const [head, ...body] = rows;
            blocks.push(
                <div key={`t${blocks.length}`} style={{ overflowX: 'auto' }}>
                    <table>
                        <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
                        <tbody>
                            {body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}
                        </tbody>
                    </table>
                </div>,
            );
        }
        rows = [];
    };
    source.split('\n').forEach((line, i) => {
        if (line.startsWith('```')) {
            if (fence) {
                blocks.push(<pre key={i} className="code">{fence.join('\n')}</pre>);
                fence = null;
            } else {
                flushList();
                flushTable();
                fence = [];
            }
            return;
        }
        if (fence) {
            fence.push(line);
            return;
        }
        if (/^\s*\|.*\|\s*$/.test(line)) {
            flushList();
            if (!/^\s*\|[\s:|-]+\|\s*$/.test(line)) rows.push(line.trim().slice(1, -1).split('|').map((c) => c.trim()));
            return;
        }
        flushTable();
        const li = line.match(/^\s*[-*]\s+(.*)/);
        if (li) {
            list.push(<li key={i}>{inline(li[1])}</li>);
            return;
        }
        flushList();
        const h = line.match(/^(#{1,6})\s+(.*)/);
        if (h) {
            const level = Math.min(h[1].length, 4);
            const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4';
            blocks.push(<Tag key={i}>{inline(h[2])}</Tag>);
        } else if (line.trim()) {
            blocks.push(<p key={i}>{inline(line)}</p>);
        }
    });
    flushList();
    flushTable();
    if (fence) blocks.push(<pre key="open-fence" className="code">{(fence as string[]).join('\n')}</pre>);
    return <div className="md">{blocks}</div>;
}
