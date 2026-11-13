import { CircleX, TriangleAlert } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { useLogs, type Poll } from '../api/hooks';
import { Card, Empty, useDebounced } from '../components/ui';
import { timeOnly } from '../lib/format';

export function LogsPage({ poll }: { poll: Poll }) {
    const [source, setSource] = useState('server');
    const [level, setLevel] = useState('all');
    const [text, setText] = useState('');
    const [follow, setFollow] = useState(true);
    const { data: lines = [] } = useLogs(source, level, useDebounced(text, 300), poll ? 3000 : false);
    const box = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
    }, [lines, follow]);

    return (
        <div className="page">
            <Card>
                <div className="row" style={{ marginBottom: 10 }}>
                    <select className="input" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Log source">
                        <option value="server">ai-memory server</option>
                        <option value="mlx">MLX server (GPU)</option>
                    </select>
                    <select className="input" value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
                        <option value="all">all levels</option>
                        <option value="error">errors</option>
                        <option value="warn">warnings</option>
                        <option value="info">info</option>
                    </select>
                    <input
                        className="input input-grow"
                        type="search"
                        placeholder="filter text — auto-sync, backup, POST /mcp…"
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        aria-label="Filter logs"
                    />
                    <label className="pill">
                        <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow
                    </label>
                    <span className="muted small">{lines.length} lines</span>
                </div>
                <div className="logbox mono" ref={box}>
                    {lines.map((l, i) => (
                        <div key={i} className={`logline lv-${l.level}`}>
                            {l.t && <span className="ts">{timeOnly(l.t)}</span>}
                            {l.level === 'error' && <CircleX size={12} aria-label="error" />}
                            {l.level === 'warn' && <TriangleAlert size={12} aria-label="warning" />}{' '}
                            {l.line}
                        </div>
                    ))}
                    {!lines.length && <Empty>No log lines</Empty>}
                </div>
            </Card>
        </div>
    );
}
