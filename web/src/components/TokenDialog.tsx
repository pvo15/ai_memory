import { KeyRound } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export function TokenDialog({ open, error, onSubmit }: { open: boolean; error: string; onSubmit: (t: string) => void }) {
    const ref = useRef<HTMLDialogElement>(null);
    const [value, setValue] = useState('');

    useEffect(() => {
        const d = ref.current;
        if (!d) return;
        if (open && !d.open) d.showModal();
        if (!open && d.open) d.close();
    }, [open]);

    return (
        <dialog ref={ref} onCancel={(e) => e.preventDefault()} aria-labelledby="token-title">
            <form
                method="dialog"
                onSubmit={(e) => {
                    e.preventDefault();
                    if (value.trim()) onSubmit(value.trim());
                    setValue('');
                }}
            >
                <h3 id="token-title" className="row" style={{ marginTop: 0 }}>
                    <KeyRound size={18} aria-hidden /> Connect to ai-memory
                </h3>
                <p className="muted">
                    Normally you're logged in automatically when the page opens. If that failed, reload the page —
                    or paste <code>MEMORY_TOKEN</code> from <code>~/Documents/ai-memory/.env</code> (stays in this
                    browser only).
                </p>
                <pre className="code small">grep MEMORY_TOKEN ~/Documents/ai-memory/.env | cut -d= -f2 | pbcopy</pre>
                <input
                    className="input"
                    style={{ width: '100%', marginTop: 12 }}
                    type="password"
                    placeholder="token"
                    autoComplete="off"
                    autoFocus
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                />
                {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
                <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
                    <button className="btn btn-primary" type="submit">Connect</button>
                </div>
            </form>
        </dialog>
    );
}
