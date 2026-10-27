import { useMemo, useState } from 'react'
import { fmtMs, readChat, type RunRecord } from './store'
import { Empty, Page, Stat } from './ui'

type Metric = 'cost' | 'tokens' | 'wall'
const METRICS: Record<Metric, { label: string; get: (r: RunRecord) => number; fmt: (n: number) => string }> = {
  cost: { label: 'Cost', get: (r) => r.cost, fmt: (n) => `$${n.toFixed(4)}` },
  tokens: { label: 'Tokens', get: (r) => r.tokens, fmt: (n) => n.toLocaleString() },
  wall: { label: 'Wall time', get: (r) => r.wallMs ?? 0, fmt: (n) => fmtMs(n) },
}

export function UsageView({ runs }: { runs: RunRecord[] }) {
  const [metric, setMetric] = useState<Metric>('cost')
  const m = METRICS[metric]

  const chat = useMemo(() => {
    let turns = 0, tokens = 0, cost = 0
    for (const c of readChat()) for (const t of c.turns) if (t.bench) {
      turns++; tokens += t.bench.tokens; cost += t.bench.cost_usd
    }
    return { turns, tokens, cost }
  }, [])

  const total = useMemo(() => ({
    tokens: runs.reduce((a, r) => a + r.tokens, 0),
    cost: runs.reduce((a, r) => a + r.cost, 0),
    parallel: (() => { const p = runs.map((r) => r.parallel).filter((x): x is number => x != null); return p.length ? p.reduce((a, b) => a + b, 0) / p.length : null })(),
    failed: runs.filter((r) => !r.ok).length,
  }), [runs])

  const models = useMemo(() => {
    const acc: Record<string, { tokens: number; cost: number; calls: number }> = {}
    for (const r of runs) for (const [k, v] of Object.entries(r.models)) {
      const row = (acc[k] ??= { tokens: 0, cost: 0, calls: 0 })
      row.tokens += v.tokens; row.cost += v.cost; row.calls += v.calls
    }
    return Object.entries(acc).sort((a, b) => b[1].cost - a[1].cost)
  }, [runs])

  const series = runs.slice(0, 14).reverse()
  const max = Math.max(1e-9, ...series.map(m.get))

  if (!runs.length && !chat.turns) {
    return (
      <Page title="Usage" sub="Tokens, cost and time, from the runs you make here.">
        <Empty icon="▁▃▅" title="Nothing to measure yet">Run the orchestrator once and its tokens, cost and wall time appear here.</Empty>
      </Page>
    )
  }

  return (
    <Page title="Usage" sub="Counted from the runs and chats saved in this browser. It is not your provider's bill and not shared across devices.">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-8">
        <Stat label="Runs" value={runs.length} hint={`${total.failed} failed`} />
        <Stat label="Run cost" value={`$${total.cost.toFixed(4)}`} />
        <Stat label="Run tokens" value={total.tokens.toLocaleString()} />
        <Stat label="Avg parallel efficiency" value={total.parallel == null ? '—' : `${total.parallel.toFixed(1)}×`} hint="Mean over runs that reported it" />
        <Stat label="Chat answers" value={chat.turns} />
        <Stat label="Chat cost" value={`$${chat.cost.toFixed(4)}`} />
        <Stat label="Chat tokens" value={chat.tokens.toLocaleString()} />
        <Stat label="Combined" value={`$${(total.cost + chat.cost).toFixed(4)}`} />
      </div>

      {series.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">Last {series.length} runs</h3>
            <div className="flex gap-1">
              {(Object.keys(METRICS) as Metric[]).map((k) => (
                <button key={k} onClick={() => setMetric(k)} aria-pressed={metric === k}
                  className={`h-7 px-3 rounded-full border text-[10px] uppercase tracking-[0.07em] ${metric === k ? 'border-[var(--color-ink)]' : 'border-[var(--color-line)] text-[var(--color-ink-faint)]'}`}>
                  {METRICS[k].label}
                </button>
              ))}
            </div>
          </div>
          <div role="img" aria-label={`${m.label} per run`} className="flex items-end gap-1.5 h-36 border-b border-[var(--color-line-strong)]">
            {series.map((r) => (
              <div key={r.id} className="flex-1 min-w-0 h-full flex items-end" title={`${r.goal}\n${m.fmt(m.get(r))}`}>
                <div className="w-full transition-[height] duration-500 ease-[cubic-bezier(0.19,1,0.22,1)]"
                  style={{ height: `${Math.max(2, (m.get(r) / max) * 100)}%`, background: r.ok ? 'var(--color-ink)' : 'var(--color-failed)', opacity: r.ok ? 0.85 : 1 }} />
              </div>
            ))}
          </div>
          <p className="mt-1.5 font-mono text-[10px] text-[var(--color-ink-faint)]">peak {m.fmt(max)} · red bars are failed runs · oldest left</p>
        </section>
      )}

      {models.length > 0 && (
        <section>
          <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-3">By model</h3>
          <table className="w-full text-[12px]">
            <thead><tr className="text-left font-mono text-[10px] uppercase text-[var(--color-ink-faint)]">
              <th className="py-1.5 font-normal">Model</th><th className="font-normal text-right">Calls</th>
              <th className="font-normal text-right">Tokens</th><th className="font-normal text-right">Cost</th></tr></thead>
            <tbody>{models.map(([k, v]) => (
              <tr key={k} className="border-t border-[var(--color-line)]">
                <td className="py-2 font-mono">{k}</td><td className="text-right tabular-nums">{v.calls}</td>
                <td className="text-right tabular-nums">{v.tokens.toLocaleString()}</td>
                <td className="text-right tabular-nums">${v.cost.toFixed(4)}</td></tr>))}
            </tbody>
          </table>
        </section>
      )}
    </Page>
  )
}
