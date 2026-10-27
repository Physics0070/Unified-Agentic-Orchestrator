import { useState } from 'react'
import { ago, fmtMs, type RunRecord } from './store'
import { Badge, Button, Empty, Icon, Page, Stat } from './ui'

type Tone = 'default' | 'good' | 'bad'
const TONE_CLS: Record<Tone, string> = {
  default: 'text-[var(--color-ink-faint)]',
  good: 'text-[var(--color-done)]',
  bad: 'text-[var(--color-failed)]',
}

interface Row { label: string; a: number | null; b: number | null; fmt: (n: number) => string; lowerBetter: boolean }

const usd = (n: number) => `$${n.toFixed(4)}`
const num = (n: number) => n.toLocaleString()

/** Delta of B relative to A, with a tone. Never divides by zero. */
function delta(row: Row): { text: string; tone: Tone } {
  const { a, b } = row
  if (a == null || b == null) return { text: '—', tone: 'default' }
  const d = b - a
  if (d === 0) return { text: 'same', tone: 'default' }
  const pct = a === 0 ? '' : ` (${d > 0 ? '+' : ''}${((d / Math.abs(a)) * 100).toFixed(0)}%)`
  const better = row.lowerBetter ? d < 0 : d > 0
  return { text: `${d > 0 ? '+' : '−'}${row.fmt(Math.abs(d))}${pct}`, tone: better ? 'good' : 'bad' }
}

function outputInfo(r: RunRecord) {
  const o = r.output
  if (!o) return null
  let findings: number | null = null
  for (const n of o.outputs ?? []) {
    const f = (n.content as { findings?: unknown } | null)?.findings
    if (Array.isArray(f)) findings = (findings ?? 0) + f.length
  }
  return { nodes: o.outputs?.length ?? 0, findings, json: JSON.stringify(o.outputs ?? null) }
}

const label = (r: RunRecord) => `${r.goal.length > 70 ? r.goal.slice(0, 70) + '…' : r.goal} · ${ago(r.ts)}`
const TH = 'py-1.5 font-normal text-right'

export function CompareView({ runs, onOpenRun }: { runs: RunRecord[]; onOpenRun: (r: RunRecord) => void }) {
  const [aId, setAId] = useState(runs[1]?.id ?? '')
  const [bId, setBId] = useState(runs[0]?.id ?? '')
  const A = runs.find((r) => r.id === aId)
  const B = runs.find((r) => r.id === bId)

  if (runs.length < 2) {
    return (
      <Page title="Compare" sub="Two saved runs, side by side.">
        <Empty icon="⇄" title="Two runs needed">Comparing needs at least two saved runs. Run the orchestrator again and they will appear here.</Empty>
      </Page>
    )
  }

  const select = (id: string, set: (v: string) => void, name: string) => (
    <label className="flex-1 min-w-[220px] text-[10px] uppercase tracking-[0.07em] text-[var(--color-ink-faint)]">
      {name}
      <select value={id} onChange={(e) => set(e.target.value)}
        className="mt-1 w-full h-10 px-3 bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[12px] normal-case tracking-normal text-[var(--color-ink)]">
        <option value="">Choose a run…</option>
        {runs.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
      </select>
    </label>
  )

  const picker = (
    <div className="flex gap-3 flex-wrap mb-6">
      {select(aId, setAId, 'Run A')}
      {select(bId, setBId, 'Run B')}
    </div>
  )

  if (!A || !B || A.id === B.id) {
    return (
      <Page title="Compare" sub="Two saved runs, side by side.">
        {picker}
        <Empty icon="⇄" title={A && B ? 'Same run twice' : 'Pick two runs'}>
          {A && B ? 'A and B are the same run. Choose a different run for one side.' : 'Choose a run for both A and B.'}
        </Empty>
      </Page>
    )
  }

  const rows: Row[] = [
    { label: 'Cost', a: A.cost, b: B.cost, fmt: usd, lowerBetter: true },
    { label: 'Tokens', a: A.tokens, b: B.tokens, fmt: num, lowerBetter: true },
    { label: 'Wall time', a: A.wallMs, b: B.wallMs, fmt: fmtMs, lowerBetter: true },
    { label: 'Parallel efficiency', a: A.parallel, b: B.parallel, fmt: (n) => `${n.toFixed(1)}×`, lowerBetter: false },
    { label: 'Handoff validity', a: A.handoff, b: B.handoff, fmt: (n) => n.toFixed(2), lowerBetter: false },
    { label: 'Failed nodes', a: A.failed, b: B.failed, fmt: num, lowerBetter: true },
    { label: 'Models used', a: Object.keys(A.models).length, b: Object.keys(B.models).length, fmt: num, lowerBetter: true },
  ]
  const show = (n: number | null, fmt: (n: number) => string) => (n == null ? '—' : fmt(n))

  const names = [...new Set([...Object.keys(A.models), ...Object.keys(B.models)])].sort()
  const oa = outputInfo(A), ob = outputInfo(B)
  const cell = (u: RunRecord['models'][string] | undefined, k: 'tokens' | 'cost' | 'calls') =>
    u ? (k === 'cost' ? usd(u.cost) : num(u[k])) : '—'

  const head = (r: RunRecord, tag: string) => (
    <div className="border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2.5 flex-1 min-w-[220px]">
      <div className="flex items-center gap-2 mb-1.5">
        <Badge tone="accent">{tag}</Badge><Badge tone={r.ok ? 'good' : 'bad'}>{r.ok ? 'ok' : 'failed'}</Badge>
      </div>
      <p className="text-[12px] text-[var(--color-ink)] line-clamp-2">{r.goal}</p>
      <div className="mt-2"><Button size="sm" variant="ghost" onClick={() => onOpenRun(r)}>Open <Icon name="external" size={12} /></Button></div>
    </div>
  )

  return (
    <Page title="Compare" sub="Saved runs from this browser. Green means B is better than A.">
      {picker}
      <div className="flex gap-3 flex-wrap mb-6">{head(A, 'A')}{head(B, 'B')}</div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-6">
        {rows.slice(0, 4).map((r) => {
          const d = delta(r)
          return <Stat key={r.label} label={`${r.label} · B vs A`} value={d.text} tone={d.tone === 'default' ? 'default' : d.tone} />
        })}
      </div>

      <div className="overflow-x-auto"><table className="w-full text-[12px] mb-8 min-w-[440px]">
        <caption className="sr-only">Metric comparison between run A and run B</caption>
        <thead><tr className="text-left font-mono text-[10px] uppercase text-[var(--color-ink-faint)]">
          <th scope="col" className="py-1.5 font-normal">Metric</th>
          <th scope="col" className={TH}>A</th><th scope="col" className={TH}>B</th><th scope="col" className={TH}>Delta (B − A)</th>
        </tr></thead>
        <tbody>{rows.map((r) => {
          const d = delta(r)
          return (
            <tr key={r.label} className="border-t border-[var(--color-line)]">
              <th scope="row" className="py-2 text-left font-normal">{r.label}</th>
              <td className="text-right tabular-nums">{show(r.a, r.fmt)}</td>
              <td className="text-right tabular-nums">{show(r.b, r.fmt)}</td>
              <td className={`text-right tabular-nums ${TONE_CLS[d.tone]}`}>{d.text}</td>
            </tr>)
        })}</tbody>
      </table></div>

      {names.length > 0 && (
        <section className="mb-8">
          <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-3">By model</h3>
          <div className="overflow-x-auto"><table className="w-full text-[12px] min-w-[440px]">
            <caption className="sr-only">Per-model usage for run A and run B</caption>
            <thead><tr className="text-left font-mono text-[10px] uppercase text-[var(--color-ink-faint)]">
              <th scope="col" className="py-1.5 font-normal">Model</th>
              {['Tokens', 'Cost', 'Calls'].flatMap((h) => [`${h} A`, `${h} B`]).map((h) => <th key={h} scope="col" className={TH}>{h}</th>)}
            </tr></thead>
            <tbody>{names.map((k) => (
              <tr key={k} className="border-t border-[var(--color-line)]">
                <th scope="row" className="py-2 text-left font-normal font-mono">{k}</th>
                {(['tokens', 'cost', 'calls'] as const).flatMap((f) => [
                  <td key={f + 'a'} className="text-right tabular-nums">{cell(A.models[k], f)}</td>,
                  <td key={f + 'b'} className="text-right tabular-nums">{cell(B.models[k], f)}</td>,
                ])}
              </tr>))}
            </tbody>
          </table></div>
        </section>
      )}

      <section>
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-3">Output</h3>
        {oa && ob ? (
          <div className="border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2.5 text-[12px] space-y-1">
            <p>{oa.json === ob.json ? 'The outputs are identical.' : 'The outputs differ.'}</p>
            <p className="text-[var(--color-ink-muted)]">Output nodes: A {oa.nodes}, B {ob.nodes}</p>
            {(oa.findings != null || ob.findings != null) && (
              <p className="text-[var(--color-ink-muted)]">Findings: A {oa.findings ?? '—'}, B {ob.findings ?? '—'}</p>
            )}
          </div>
        ) : (
          <p className="text-[12px] text-[var(--color-ink-faint)]">
            Output is kept for the newest runs only{!oa && !ob ? '; neither run has it.' : `; ${oa ? 'B' : 'A'} no longer has it.`}
          </p>
        )}
      </section>
    </Page>
  )
}
