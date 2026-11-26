import {
    Activity,
    Archive,
    BookOpen,
    Brain,
    Code2,
    FileText,
    KeyRound,
    LayoutDashboard,
    MemoryStick,
    Moon,
    RefreshCw,
    ScrollText,
    Sparkles,
    Ticket,
} from 'lucide-react';
import { useEffect, useState, type ComponentType } from 'react';

/** True when the server has a newer build than the one this page is running. */
function useNewVersion(): boolean {
    const [newer, setNewer] = useState(false);
    useEffect(() => {
        const mine = (document.querySelector('script[type="module"][src]') as HTMLScriptElement | null)?.getAttribute('src');
        if (!mine) return;
        const check = async () => {
            try {
                const html = await (await fetch('/ui', { cache: 'no-store' })).text();
                const live = html.match(/\/ui\/assets\/index-[^"]+\.js/)?.[0];
                if (live && !mine.endsWith(live)) setNewer(true);
            } catch {
                /* offline / restarting: try again later */
            }
        };
        const t = setInterval(check, 60_000);
        addEventListener('focus', check);
        return () => { clearInterval(t); removeEventListener('focus', check); };
    }, []);
    return newer;
}
import { useIsFetching, useQueryClient } from '@tanstack/react-query';

import { useOverview, type Poll } from './api/hooks';
import { HeaderMonitor } from './components/Monitor';
import { StatusPill } from './components/ui';
import { ago, duration } from './lib/format';
import { BackupsPage } from './pages/Backups';
import { CheatsheetPage } from './pages/Cheatsheet';
import { CleanupPage } from './pages/Cleanup';
import { CodePage } from './pages/Code';
import { JiraPage } from './pages/Jira';
import { LogsPage } from './pages/Logs';
import { MemoriesPage } from './pages/Memories';
import { OverviewPage } from './pages/Overview';
import { RulesPage } from './pages/Rules';

const TABS = {
    overview: { label: 'Overview', icon: LayoutDashboard },
    jira: { label: 'Jira', icon: Ticket },
    code: { label: 'Code', icon: Code2 },
    rules: { label: 'Rules', icon: FileText },
    memories: { label: 'Memories', icon: Brain },
    cleanup: { label: 'Clean-up', icon: Sparkles },
    backups: { label: 'Backups', icon: Archive },
    logs: { label: 'Logs', icon: ScrollText },
    cheatsheet: { label: 'Cheat sheet', icon: BookOpen },
} satisfies Record<string, { label: string; icon: ComponentType<{ size?: number }> }>;
type TabKey = keyof typeof TABS;

const tabFromHash = (): TabKey => {
    const h = location.hash.slice(1);
    return h in TABS ? (h as TabKey) : 'overview';
};

function useHashTab() {
    const [tab, setTab] = useState<TabKey>(tabFromHash);
    useEffect(() => {
        const on = () => setTab(tabFromHash());
        addEventListener('hashchange', on);
        return () => removeEventListener('hashchange', on);
    }, []);
    return [tab, (t: TabKey) => (location.hash = t)] as const;
}

function toggleTheme() {
    const dark = getComputedStyle(document.documentElement).colorScheme === 'dark';
    const next = dark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
        localStorage.setItem('aimem_theme', next);
    } catch {
        /* ignore */
    }
}

function Header({ poll, setAuto, auto, onToken }: { poll: Poll; auto: boolean; setAuto: (v: boolean) => void; onToken: () => void }) {
    const { data: o, isError, dataUpdatedAt } = useOverview(poll);
    const fetching = useIsFetching() > 0;
    const qc = useQueryClient();
    return (
        <header className="topbar">
            <span className="brand"><MemoryStick size={18} aria-hidden /> ai-memory</span>
            {isError && !o ? (
                <StatusPill health="critical">server unreachable</StatusPill>
            ) : (
                <StatusPill health="good">server · up {o ? duration(o.server.uptime_s) : '…'}</StatusPill>
            )}
            {o && (
                <StatusPill health={o.mlx.state === 'up' ? 'good' : o.mlx.state === 'down' ? 'warning' : 'off'}>
                    {o.mlx.state === 'up' ? 'MLX up' : o.mlx.state === 'down' ? 'MLX down' : 'MLX off'}
                </StatusPill>
            )}
            {o && (
                <StatusPill health={!o.jira.configured ? 'off' : o.jira.last_sync ? 'good' : 'warning'}>
                    {o.jira.configured ? `Jira · synced ${ago(o.jira.last_sync)}` : 'Jira off'}
                </StatusPill>
            )}
            <HeaderMonitor poll={poll} />
            <span className="muted small">{dataUpdatedAt ? `updated ${new Date(dataUpdatedAt).toLocaleTimeString()}` : ''}</span>
            <span className="grow" />
            <label className="pill">
                <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
                <Activity size={13} aria-hidden /> live
            </label>
            <button className="btn" onClick={() => void qc.invalidateQueries()} title="Refresh now">
                <RefreshCw size={14} className={fetching ? 'spin' : ''} aria-hidden /> Refresh
            </button>
            <a className="btn" href="#cheatsheet" title="What to say to Claude / GLM">
                <BookOpen size={14} aria-hidden /> Cheat sheet
            </a>
            <button className="btn btn-icon" onClick={toggleTheme} title="Toggle light / dark" aria-label="Toggle theme">
                <Moon size={15} />
            </button>
            <button className="btn btn-icon" onClick={onToken} title="Login" aria-label="Login">
                <KeyRound size={15} />
            </button>
        </header>
    );
}

export function App({ onToken }: { onToken: () => void }) {
    const [tab, setTab] = useHashTab();
    const [auto, setAuto] = useState(true);
    const poll: Poll = auto ? 5000 : false;

    const newVersion = useNewVersion();

    return (
        <>
            {newVersion && (
                <div className="update-banner" role="status">
                    A new version of the dashboard is available.
                    <button className="btn small btn-primary" onClick={() => location.reload()}>Reload</button>
                </div>
            )}
            <Header poll={poll} auto={auto} setAuto={setAuto} onToken={onToken} />
            <nav className="tabs" role="tablist">
                {(Object.keys(TABS) as TabKey[]).map((k) => {
                    const { label, icon: Icon } = TABS[k];
                    return (
                        <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
                            <Icon size={15} /> {label}
                        </button>
                    );
                })}
            </nav>
            <main>
                {tab === 'overview' && <OverviewPage poll={poll} />}
                {tab === 'jira' && <JiraPage />}
                {tab === 'code' && <CodePage />}
                {tab === 'rules' && <RulesPage />}
                {tab === 'memories' && <MemoriesPage />}
                {tab === 'cleanup' && <CleanupPage />}
                {tab === 'backups' && <BackupsPage />}
                {tab === 'logs' && <LogsPage poll={poll} />}
                {tab === 'cheatsheet' && <CheatsheetPage />}
            </main>
        </>
    );
}
