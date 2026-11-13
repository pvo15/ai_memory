import { HardDriveDownload } from 'lucide-react';

import { useAction, useBackups, useOverview } from '../api/hooks';
import { Card, Empty, useToast } from '../components/ui';
import { ago, bytes, dateTime, until } from '../lib/format';

const RESTORE = `cd ~/Documents/ai-memory
docker compose stop
cp data/memory.db data/memory-before-restore.db
cp backups/<file> data/memory.db
rm -f data/memory.db-wal data/memory.db-shm
docker compose up -d`;

export function BackupsPage() {
    const { data } = useBackups();
    const { data: o } = useOverview(false);
    const action = useAction();
    const toast = useToast();
    return (
        <div className="page">
            <Card
                title={`Backups · ${data?.dir ?? ''}`}
                icon={<HardDriveDownload size={14} />}
                actions={
                    <button
                        className="btn"
                        disabled={action.isPending}
                        onClick={() => action.mutate('backup_now', { onSuccess: (r) => toast(r.message) })}
                    >
                        Backup now
                    </button>
                }
            >
                {o && (
                    <p>
                        Next automatic backup: <b>{dateTime(o.backup.next)}</b> ({until(o.backup.next)}, Mon–Fri 10:00{' '}
                        {o.backup.tz})
                    </p>
                )}
                <table>
                    <thead><tr><th>File</th><th>Size</th><th>Created</th></tr></thead>
                    <tbody>
                        {data?.files.map((f) => (
                            <tr key={f.file}>
                                <td className="mono">{f.file}</td>
                                <td>{bytes(f.bytes)}</td>
                                <td>{dateTime(f.mtime)} <span className="muted">({ago(f.mtime)})</span></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {data && !data.files.length && <Empty>No backups yet</Empty>}
                <details style={{ marginTop: 12 }}>
                    <summary>How to restore</summary>
                    <pre className="code">{RESTORE}</pre>
                </details>
            </Card>
        </div>
    );
}
