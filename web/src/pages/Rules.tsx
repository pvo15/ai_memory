import { FileText, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { useOverview, useRuleFile, useRuleSearch, useRules } from '../api/hooks';
import type { RuleFileRow } from '../api/types';
import { Markdown } from '../components/Markdown';
import { Card, Empty, Tag, useDebounced } from '../components/ui';
import { ago, fmtNum } from '../lib/format';

/** Overview's "Rules files" link sets this so the tab opens on that project. */
export const RULES_PROJECT_KEY = 'aimem.rules.project';

function readKey(): string {
    try {
        return sessionStorage.getItem(RULES_PROJECT_KEY) ?? '';
    } catch {
        return '';
    }
}

/** Leading `---` front matter (e.g. `paths:` a rule applies to) is shown as tags, not as text. */
function splitFrontMatter(text: string): { meta: string[]; body: string } {
    const m = text.match(/^---\n([\s\S]*?)\n---\n?/);
    if (!m) return { meta: [], body: text };
    const meta = m[1]
        .split('\n')
        .map((l) => l.replace(/^\s*-\s*/, '').replace(/^["']|["']$/g, '').trim())
        .filter((l) => l && !l.endsWith(':'));
    return { meta, body: text.slice(m[0].length) };
}

export function RulesPage() {
    const { data: o } = useOverview(false);
    const repos = o?.repos ?? [];
    const [project, setProject] = useState(readKey);
    const [text, setText] = useState('');
    const [file, setFile] = useState<string | null>(null);
    const [focus, setFocus] = useState<string | null>(null);
    const q = useDebounced(text);
    const current = project && repos.some((r) => r.name === project) ? project : (repos[0]?.name ?? '');

    const { data: list, isFetching } = useRules(current);
    const { data: hits } = useRuleSearch(current, q);
    const { data: view } = useRuleFile(current, file);
    const viewer = useRef<HTMLDivElement>(null);

    const groups = useMemo(() => {
        const g: Record<string, RuleFileRow[]> = {};
        for (const f of list?.files ?? []) (g[f.folder] ??= []).push(f);
        return Object.entries(g);
    }, [list]);

    // A search hit opens its file and scrolls to that section.
    useEffect(() => {
        if (!view || !focus || !viewer.current) return;
        const want = focus.split(' > ').pop()?.trim();
        const h = [...viewer.current.querySelectorAll('h1,h2,h3,h4')].find((e) => e.textContent?.trim() === want);
        h?.scrollIntoView({ block: 'start' });
    }, [view, focus]);

    const pickProject = (p: string) => {
        setProject(p);
        setFile(null);
        try {
            sessionStorage.setItem(RULES_PROJECT_KEY, p);
        } catch {
            /* private window: just not remembered */
        }
    };
    const open = (f: string, heading: string | null = null) => {
        setFile(f);
        setFocus(heading);
    };
    const fm = view && !view.error ? splitFrontMatter(view.text) : null;

    return (
        <div className="page">
            <Card>
                <div className="row">
                    <select
                        className="input"
                        value={current}
                        onChange={(e) => pickProject(e.target.value)}
                        aria-label="Project"
                        data-testid="rules-project"
                    >
                        {repos.map((r) => (
                            <option key={r.name} value={r.name}>
                                {r.name} ({fmtNum(r.rules_files)} files)
                            </option>
                        ))}
                    </select>
                    <Search size={16} className="muted" aria-hidden />
                    <input
                        className="input input-grow"
                        type="search"
                        placeholder="Search rules — validation, attachments, error handling…"
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        aria-label="Search rules"
                        data-testid="rules-search"
                    />
                </div>
                <div className="muted small" style={{ marginTop: 8 }}>
                    The files every AI tool gets through <code>project_rules</code>: {list?.globs.join(', ')}
                </div>
            </Card>
            {!repos.length && <Empty>No connected folders. Add them to CODE_PROJECTS in .env.</Empty>}
            {list?.error && <Empty>{list.error}</Empty>}
            {!!repos.length && !list?.error && (
                <div className="split">
                    <Card
                        title={
                            q
                                ? `${hits?.results.length ?? 0} matching sections`
                                : `${fmtNum(list?.files.length ?? 0)} rules files${isFetching ? ' …' : ''}`
                        }
                    >
                        {q ? (
                            <table data-testid="rules-hits">
                                <tbody>
                                    {hits?.results.map((h, i) => (
                                        <tr
                                            key={i}
                                            className={`clickable ${h.file === file ? 'selected' : ''}`}
                                            onClick={() => open(h.file, h.heading)}
                                        >
                                            <td>
                                                <b>{h.heading}</b>
                                                <div className="muted small mono">{h.file}</div>
                                                <div className="small">{h.snip}</div>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        ) : (
                            <table data-testid="rules-files">
                                <tbody>
                                    {groups.map(([folder, files]) => [
                                        <tr key={`h-${folder}`}>
                                            <th colSpan={2} className="mono">{folder}</th>
                                        </tr>,
                                        ...files.map((f) => (
                                            <tr
                                                key={f.file}
                                                className={`clickable ${f.file === file ? 'selected' : ''}`}
                                                onClick={() => open(f.file)}
                                            >
                                                <td>
                                                    <FileText size={13} aria-hidden style={{ verticalAlign: -2, marginRight: 6 }} />
                                                    {f.file.split('/').pop()}
                                                </td>
                                                <td className="muted small" style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                                                    {f.sections} sections · {ago(f.mtime)}
                                                </td>
                                            </tr>
                                        )),
                                    ])}
                                </tbody>
                            </table>
                        )}
                        {q && hits && !hits.results.length && <Empty>No section matches “{q}”.</Empty>}
                    </Card>
                    <Card title={file ?? 'Rules file'} className="sticky">
                        <div ref={viewer} data-testid="rules-viewer">
                            {!file && <Empty>Pick a file to read it. These are the rules the AI follows in {current}.</Empty>}
                            {view?.error && <Empty>{view.error}</Empty>}
                            {fm && (
                                <>
                                    {!!fm.meta.length && (
                                        <div className="row" style={{ marginBottom: 8 }}>
                                            <span className="muted small">Applies to</span>
                                            {fm.meta.map((m) => <Tag key={m}>{m}</Tag>)}
                                        </div>
                                    )}
                                    <Markdown source={fm.body} />
                                </>
                            )}
                        </div>
                    </Card>
                </div>
            )}
        </div>
    );
}
