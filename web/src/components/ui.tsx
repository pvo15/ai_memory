import { CircleCheck, CircleX, TriangleAlert, Minus } from 'lucide-react';
import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useState,
    type ReactNode,
    type MouseEvent,
} from 'react';

/* ---------------------------------------------------------------- layout bits */
export function Card({ title, icon, children, className = '', actions }: {
    title?: ReactNode;
    icon?: ReactNode;
    children: ReactNode;
    className?: string;
    actions?: ReactNode;
}) {
    return (
        <section className={`card ${className}`}>
            {title && (
                <h2 className="card-title">
                    {icon}
                    {title}
                    {actions && <span className="grow" />}
                    {actions}
                </h2>
            )}
            {children}
        </section>
    );
}

export function StatTile({ value, label, sub, icon }: { value: ReactNode; label: string; sub?: string; icon?: ReactNode }) {
    return (
        <div className="card">
            <div className="tile-value">{value}</div>
            <div className="tile-label">{icon}{label}</div>
            {sub && <div className="tile-sub">{sub}</div>}
        </div>
    );
}

export const Empty = ({ children }: { children: ReactNode }) => <div className="empty">{children}</div>;
export const Tag = ({ children }: { children: ReactNode }) => <span className="tag">{children}</span>;

/* ---------------------------------------------------------------- status (icon + label, never colour alone) */
export type Health = 'good' | 'warning' | 'critical' | 'off';
const HEALTH_ICON = { good: CircleCheck, warning: TriangleAlert, critical: CircleX, off: Minus };

export function StatusPill({ health, children }: { health: Health; children: ReactNode }) {
    const Icon = HEALTH_ICON[health];
    const color = health === 'off' ? 'var(--text-3)' : `var(--${health})`;
    return (
        <span className="pill" role="status">
            <Icon size={14} color={color} aria-hidden />
            {children}
        </span>
    );
}

/* ---------------------------------------------------------------- tooltip */
interface TipState { content: ReactNode; x: number; y: number }
const TipCtx = createContext<(t: TipState | null) => void>(() => {});

export function TooltipProvider({ children }: { children: ReactNode }) {
    const [tip, setTip] = useState<TipState | null>(null);
    // A chart can unmount under the cursor (tab switch) without a mouseleave:
    // drop the tooltip on navigation, scroll and clicks so it never gets stuck.
    useEffect(() => {
        const clear = () => setTip(null);
        const events = ['hashchange', 'scroll', 'mousedown', 'blur'] as const;
        events.forEach((e) => addEventListener(e, clear, true));
        return () => events.forEach((e) => removeEventListener(e, clear, true));
    }, []);
    return (
        <TipCtx.Provider value={setTip}>
            {children}
            {tip && (
                <div
                    className="tooltip"
                    role="tooltip"
                    style={{ left: Math.min(tip.x + 14, window.innerWidth - 330), top: tip.y + 14 }}
                >
                    {tip.content}
                </div>
            )}
        </TipCtx.Provider>
    );
}

/** Spread onto any element to give it a hover tooltip. */
export function useTip() {
    const set = useContext(TipCtx);
    return useCallback(
        (content: ReactNode) => ({
            onMouseMove: (e: MouseEvent) => set({ content, x: e.clientX, y: e.clientY }),
            onMouseLeave: () => set(null),
        }),
        [set],
    );
}

/* ---------------------------------------------------------------- toast */
const ToastCtx = createContext<(msg: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
    const [msg, setMsg] = useState<string | null>(null);
    useEffect(() => {
        if (!msg) return;
        const t = setTimeout(() => setMsg(null), 3500);
        return () => clearTimeout(t);
    }, [msg]);
    return (
        <ToastCtx.Provider value={setMsg}>
            {children}
            {msg && <div className="toast" role="status"><CircleCheck size={16} aria-hidden />{msg}</div>}
        </ToastCtx.Provider>
    );
}
export const useToast = () => useContext(ToastCtx);

/* ---------------------------------------------------------------- hooks */
export function useDebounced<T>(value: T, ms = 350): T {
    const [v, setV] = useState(value);
    useEffect(() => {
        const t = setTimeout(() => setV(value), ms);
        return () => clearTimeout(t);
    }, [value, ms]);
    return v;
}
