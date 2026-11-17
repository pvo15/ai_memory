import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';

import { silentLogin, tokenStore, UnauthorizedError } from './api/client';
import { App } from './App';
import { TokenDialog, type LoginReason } from './components/TokenDialog';
import { ToastProvider, TooltipProvider } from './components/ui';
import './styles.css';

// Remembered theme choice (otherwise follows macOS).
try {
    const t = localStorage.getItem('aimem_theme');
    if (t) document.documentElement.dataset.theme = t;
} catch {
    /* ignore */
}

function Root() {
    // Logged in automatically by the session cookie; the dialog is only for when that fails.
    const [login, setLogin] = useState<LoginReason | null>(null);

    // One QueryClient for the app. Any 401: first try a silent re-login (fresh cookie),
    // and only if that fails show the login dialog.
    const [qc] = useState(() => {
        let relogging = false;
        let lastTry = 0;
        const onError = (e: Error) => {
            if (!(e instanceof UnauthorizedError) || relogging) return;
            tokenStore.set(''); // a stale saved token must not block the cookie login
            if (Date.now() - lastTry < 30_000) {
                setLogin('failed'); // tried a moment ago: don't loop
                return;
            }
            relogging = true;
            lastTry = Date.now();
            void silentLogin().then((ok) => {
                relogging = false;
                if (ok) void qc.resetQueries();
                else setLogin('failed');
            });
        };
        const qc = new QueryClient({
            queryCache: new QueryCache({ onError }),
            mutationCache: new MutationCache({ onError }),
            defaultOptions: {
                queries: {
                    retry: (n, e) => !(e instanceof UnauthorizedError) && n < 2,
                    refetchOnWindowFocus: true,
                    enabled: true,
                },
            },
        });
        return qc;
    });

    return (
        <QueryClientProvider client={qc}>
            <TooltipProvider>
                <ToastProvider>
                    <App onToken={() => setLogin('manual')} />
                    <TokenDialog
                        open={login !== null}
                        reason={login ?? 'manual'}
                        onClose={() => setLogin(null)}
                        onLoggedIn={() => {
                            setLogin(null);
                            void qc.resetQueries();
                        }}
                    />
                </ToastProvider>
            </TooltipProvider>
        </QueryClientProvider>
    );
}

createRoot(document.getElementById('root')!).render(
    <StrictMode>
        <Root />
    </StrictMode>,
);
