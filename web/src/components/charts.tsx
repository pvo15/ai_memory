// Single-series bar charts: one hue (series blue), values printed on every row,
// hover tooltip for detail, a table view for the column chart. No legend needed
// for one series (the card title names it).
import type { Bar } from '../api/types';
import { fmtNum } from '../lib/format';
import { Empty, useTip } from './ui';

export function BarList({ rows, unit, showSummary = false }: { rows: Bar[]; unit: string; showSummary?: boolean }) {
    const tip = useTip();
    if (!rows.length) return <Empty>No data yet</Empty>;
    const max = Math.max(...rows.map((r) => r.n), 1);
    return (
        <div role="list">
            {rows.map((r) => (
                <div
                    key={r.label}
                    className="bar-row"
                    role="listitem"
                    {...tip(
                        <>
                            <b>{r.label || '—'}</b>
                            <br />
                            {fmtNum(r.n)} {unit}
                            {r.summary && (
                                <>
                                    <br />
                                    {r.summary}
                                </>
                            )}
                        </>,
                    )}
                >
                    <div className="bar-label" title={r.label}>
                        {r.label || '—'}
                        {showSummary && r.summary && <span className="muted"> {r.summary}</span>}
                    </div>
                    <div className="bar-track">
                        <div className="bar-fill" style={{ width: `${(r.n / max) * 100}%` }} />
                    </div>
                    <div className="bar-num">{fmtNum(r.n)}</div>
                </div>
            ))}
        </div>
    );
}

export function WeekColumns({ rows, unit }: { rows: Bar[]; unit: string }) {
    const tip = useTip();
    const max = Math.max(...rows.map((r) => r.n), 1);
    return (
        <>
            <div className="cols" role="img" aria-label={`${unit} per week`}>
                {rows.map((r) => (
                    <div
                        key={r.label}
                        className="col"
                        {...tip(
                            <>
                                Week of <b>{r.label}</b>
                                <br />
                                {fmtNum(r.n)} {unit}
                            </>,
                        )}
                    >
                        <div className="col-fill" style={{ height: `${(r.n / max) * 100}%` }} />
                    </div>
                ))}
            </div>
            <div className="cols-axis" aria-hidden>
                {rows.map((r, i) => (
                    <span key={r.label}>{i % 3 === 0 ? r.label.slice(5) : ''}</span>
                ))}
            </div>
            <details style={{ marginTop: 8 }}>
                <summary className="muted">Table view</summary>
                <table>
                    <thead>
                        <tr>
                            <th>Week of</th>
                            <th>{unit}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.label}>
                                <td>{r.label}</td>
                                <td>{fmtNum(r.n)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </details>
        </>
    );
}
