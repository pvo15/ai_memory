import { CircleCheck, CircleX, Copy, KeyRound, Loader2, LogIn, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { checkToken, serverUp, tokenStore } from '../api/client';

/** Why the dialog is open: automatic login failed, or the user clicked the key button. */
export type LoginReason = 'failed' | 'manual';

const START_CMD = 'cd ~/Documents/ai-memory && docker compose up -d';
const TOKEN_CMD = 'grep ^MEMORY_TOKEN= ~/Documents/ai-memory/.env | cut -d= -f2 | tr -d "\\n" | pbcopy';

function CopyLine({ cmd }: { cmd: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
            <pre className="code small" style={{ flex: 1, margin: 0, minWidth: 0 }}>{cmd}</pre>
            <button
                type="button"
                className="btn small"
                onClick={() => {
                    void navigator.clipboard?.writeText(cmd).then(() => {
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                    });
                }}
            >
                <Copy size={13} aria-hidden /> {copied ? 'Copied' : 'Copy'}
            </button>
        </div>
    );
}

/** Step-by-step login help. Normally never shown: the page logs in by itself (cookie). */
export function TokenDialog({ open, reason, onClose, onLoggedIn }: {
    open: boolean;
    reason: LoginReason;
    onClose: () => void;
    onLoggedIn: () => void;
}) {
    const ref = useRef<HTMLDialogElement>(null);
    const [server, setServer] = useState<'checking' | 'up' | 'down'>('checking');
    const [value, setValue] = useState('');
    const [tokenState, setTokenState] = useState<'idle' | 'checking' | 'wrong' | 'offline'>('idle');
    const [saved, setSaved] = useState(!!tokenStore.get());

    const check = () => {
        setServer('checking');
        void serverUp().then((up) => setServer(up ? 'up' : 'down'));
    };

    useEffect(() => {
        const d = ref.current;
        if (!d) return;
        if (open && !d.open) {
            d.showModal();
            setValue('');
            setTokenState('idle');
            setSaved(!!tokenStore.get());
            check();
        }
        if (!open && d.open) d.close();
    }, [open]);

    const connect = async () => {
        const t = value.trim();
        if (!t) return;
        setTokenState('checking');
        const r = await checkToken(t);
        if (r === 'ok') {
            tokenStore.set(t);
            onLoggedIn();
        } else {
            setTokenState(r);
        }
    };

    const failed = reason === 'failed';

    return (
        <dialog
            ref={ref}
            onCancel={(e) => {
                e.preventDefault();
                if (!failed) onClose();
            }}
            aria-labelledby="login-title"
            style={{ width: 'min(560px, 94vw)' }}
        >
            <div className="row" style={{ marginBottom: 6, flexWrap: 'nowrap' }}>
                <h3 id="login-title" className="row" style={{ margin: 0, flex: 1 }}>
                    <KeyRound size={18} aria-hidden /> {failed ? 'Please log in again' : 'Login'}
                </h3>
                {!failed && (
                    <button type="button" className="btn btn-icon" onClick={onClose} aria-label="Close">
                        <X size={15} />
                    </button>
                )}
            </div>

            {/* 1. Is the server running? */}
            {server === 'checking' && (
                <p className="row muted"><Loader2 size={15} className="spin" aria-hidden /> Checking the ai-memory server…</p>
            )}
            {server === 'down' && (
                <div>
                    <p className="row" style={{ color: 'var(--critical)' }}>
                        <CircleX size={16} aria-hidden /> <b>The ai-memory server is not running.</b>
                    </p>
                    <p className="muted">Start it in Terminal, wait about 10 seconds, then press <b>Try again</b>:</p>
                    <CopyLine cmd={START_CMD} />
                    <div className="row" style={{ justifyContent: 'flex-end', marginTop: 14 }}>
                        <button type="button" className="btn btn-primary" onClick={check}>Try again</button>
                    </div>
                </div>
            )}

            {server === 'up' && (
                <>
                    {/* 2. The one-click fix */}
                    {failed ? (
                        <>
                            <p className="muted">
                                The server is running, but this page is no longer logged in. This normally happens after
                                ai-memory restarted with a new token. One click fixes it:
                            </p>
                            <button
                                type="button"
                                className="btn btn-primary"
                                style={{ width: '100%', justifyContent: 'center', padding: '10px 14px' }}
                                onClick={() => location.reload()}
                                autoFocus
                            >
                                <LogIn size={16} aria-hidden /> Log in again
                            </button>
                        </>
                    ) : (
                        <p className="row">
                            <CircleCheck size={16} color="var(--good)" aria-hidden />
                            {saved ? 'Logged in with a saved token.' : 'Logged in automatically. Nothing to do.'}
                        </p>
                    )}
                    {!failed && saved && (
                        <p className="muted small row">
                            The saved token is only needed when automatic login doesn't work.
                            <button
                                type="button"
                                className="btn small"
                                onClick={() => {
                                    tokenStore.set('');
                                    setSaved(false);
                                }}
                            >
                                Remove saved token
                            </button>
                        </p>
                    )}

                    {/* 3. Fallback: paste the token */}
                    <details style={{ marginTop: 16 }}>
                        <summary className="muted" style={{ cursor: 'pointer' }}>
                            {failed ? 'Still asks to log in? Use your token instead' : 'Log in with your token instead'}
                        </summary>
                        <form
                            style={{ marginTop: 10 }}
                            onSubmit={(e) => {
                                e.preventDefault();
                                void connect();
                            }}
                        >
                            <p className="small" style={{ margin: '0 0 6px' }}>
                                <b>Step 1.</b> Copy this command, paste it in Terminal and press Enter. It copies your
                                token (nothing is shown on screen):
                            </p>
                            <CopyLine cmd={TOKEN_CMD} />
                            <p className="small" style={{ margin: '12px 0 6px' }}>
                                <b>Step 2.</b> Paste the token here (⌘V) and press <b>Log in</b>:
                            </p>
                            <div className="row" style={{ flexWrap: 'nowrap' }}>
                                <input
                                    className="input"
                                    style={{ flex: 1 }}
                                    type="password"
                                    placeholder="paste token"
                                    autoComplete="off"
                                    aria-label="Token"
                                    value={value}
                                    onChange={(e) => {
                                        setValue(e.target.value);
                                        setTokenState('idle');
                                    }}
                                />
                                <button className="btn btn-primary" type="submit" disabled={!value.trim() || tokenState === 'checking'}>
                                    {tokenState === 'checking' ? <Loader2 size={14} className="spin" aria-hidden /> : <LogIn size={14} aria-hidden />}
                                    Log in
                                </button>
                            </div>
                            {tokenState === 'wrong' && (
                                <p className="small" style={{ color: 'var(--critical)' }}>
                                    That token is not right. Run Step 1 again and paste the new value.
                                </p>
                            )}
                            {tokenState === 'offline' && (
                                <p className="small" style={{ color: 'var(--critical)' }}>
                                    Could not reach the server. Check it is running and try again.
                                </p>
                            )}
                            <p className="muted small" style={{ marginBottom: 0 }}>
                                The token stays in this browser only. If automatic login keeps failing, your browser may
                                be blocking cookies for localhost.
                            </p>
                        </form>
                    </details>
                </>
            )}
        </dialog>
    );
}
