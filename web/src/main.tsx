import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';

import { tokenStore, UnauthorizedError } from './api/client';
import { App } from './App';
import { TokenDialog } from './components/TokenDialog';
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
    // Logged in automatically by the session cookie; the token dialog is only a fallback.
    const [tokenOpen, setTokenOpen] = useState(false);
    const [tokenError, setTokenError] = useState('');

    // One QueryClient for the app. Any 401 anywhere -> ask for the token again.
    const [qc] = useState(() => {
        const onError = (e: Error) => {
            if (e instanceof UnauthorizedError) {
                tokenStore.set(''); // a stale pasted token must not block the cookie login
                setTokenError('Automatic login failed. Reload the page, or paste MEMORY_TOKEN as a fallback.');
                setTokenOpen(true);
            }
        };
        return new QueryClient({
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
    });

    return (
        <QueryClientProvider client={qc}>
            <TooltipProvider>
                <ToastProvider>
                    <App onToken={() => { setTokenError(''); setTokenOpen(true); }} />
                    <TokenDialog
                        open={tokenOpen}
                        error={tokenError}
                        onSubmit={(t) => {
                            tokenStore.set(t);
                            setTokenOpen(false);
                            setTokenError('');
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
