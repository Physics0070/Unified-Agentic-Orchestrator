/**
 * Execution trace: one expandable row per agent/node with the input it was
 * given, the output it produced, and which tools it declared / called / was
 * blocked from. Purely presentational - the data arrives with the run.
 */
import { useState } from 'react'
import type { TraceStep } from './api'
import { Badge, Empty } from './ui'

const DOT: Record<TraceStep['status'], string> = {
  pending: 'bg-[var(--color-pending)]',
  running: 'bg-[var(--color-running)] animate-pulse',
  done: 'bg-[var(--color-done)]',
  failed: 'bg-[var(--color-failed)]',
  blocked: 'bg-[var(--color-blocked)]',
  skipped: 'bg-[var(--color-skipped)]',
}

const TEXT_CAP = 4000

function dur(n: number | null) {
  if (n === null) return '—'
  return n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`
}

function Block({ label, text }: { label: string; text: string | null }) {
  const long = (text?.length ?? 0) > TEXT_CAP
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.07em] text-[var(--color-ink-faint)] mb-1">
        {label}{long && ` · first ${TEXT_CAP.toLocaleString()} of ${text!.length.toLocaleString()} chars`}
      </div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[var(--color-line)] bg-[var(--color-surface-0)] p-2 text-[11px] leading-relaxed font-mono text-[var(--color-ink-muted)]">
        {text ? (long ? text.slice(0, TEXT_CAP) : text) : '(empty)'}
      </pre>
    </div>
  )
}

function Chips({ label, items, tone }: {
  label: string; items: (string | null)[]; tone?: 'good' | 'bad'
}) {
  return (
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className="text-[10px] uppercase tracking-[0.07em] text-[var(--color-ink-faint)] w-16 shrink-0">
        {label}
      </span>
      {items.length === 0
        ? <span className="text-[11px] text-[var(--color-ink-faint)]">none</span>
        : items.map((t, i) => <Badge key={i} tone={tone}>{t ?? '?'}</Badge>)}
    </div>
  )
}

function Row({ s }: { s: TraceStep }) {
  const failed = s.status === 'failed'
  const [open, setOpen] = useState(failed)   // failed steps open themselves, error visible
  const called = s.tool_calls.map((c) => c.tool)
  return (
    <div className="border-b border-[var(--color-line)] last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 px-3 min-h-11 text-left hover:bg-[var(--color-surface-2)] transition-colors"
      >
        <span aria-hidden className={`h-2 w-2 rounded-full shrink-0 ${DOT[s.status]}`} />
        <span className={`min-w-0 flex-1 truncate text-[12px] ${failed ? 'text-[var(--color-failed)] font-semibold' : 'text-[var(--color-ink)]'}`}>
          {s.node_id}
          {s.type && <span className="text-[var(--color-ink-faint)]"> · {s.type}</span>}
        </span>
        {s.model && <span className="hidden sm:inline text-[11px] font-mono text-[var(--color-ink-faint)] truncate max-w-40">{s.model}</span>}
        <span className="text-[11px] tabular-nums text-[var(--color-ink-muted)]">{dur(s.duration_ms)}</span>
        <span className="text-[11px] tabular-nums text-[var(--color-ink-muted)]">{s.tokens.toLocaleString()} tok</span>
        <span className="text-[11px] tabular-nums text-[var(--color-ink-muted)]">${s.cost_usd.toFixed(4)}</span>
        <span aria-hidden className="text-[var(--color-ink-faint)] text-[10px]">{open ? '▾' : '▸'}</span>
      </button>

      {!open && failed && s.error && (
        <div className="mx-3 mb-2 ml-8 rounded-md border border-[#f8717155] bg-[#f871711f] px-2 py-1.5 text-[11px] text-[var(--color-failed)] break-words">
          {s.error}
        </div>
      )}

      {open && (
        <div className="px-3 pb-3 pl-8 space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone={s.status === 'done' ? 'good' : s.status === 'failed' || s.status === 'blocked' ? 'bad' : s.status === 'running' ? 'info' : 'default'}>
              {s.status}
            </Badge>
            {s.role && <span className="text-[11px] text-[var(--color-ink-muted)]">{s.role}</span>}
            {s.agent_id && <Badge>{s.agent_id}</Badge>}
            {s.repairs > 0 && <Badge tone="warn">{s.repairs} repair{s.repairs === 1 ? '' : 's'}</Badge>}
            {s.retries > 0 && <Badge tone="warn">{s.retries} retr{s.retries === 1 ? 'y' : 'ies'}</Badge>}
            {s.attempts > 1 && <Badge tone="warn">{s.attempts} attempts</Badge>}
            {s.used_fallback && <Badge tone="warn">fallback model</Badge>}
            <span className="text-[11px] tabular-nums text-[var(--color-ink-faint)]">
              {s.tokens_in.toLocaleString()} in · {s.tokens_out.toLocaleString()} out
            </span>
          </div>

          {s.error && (
            <div className="rounded-md border border-[#f8717155] bg-[#f871711f] px-2 py-1.5 text-[11px] text-[var(--color-failed)] break-words">
              {s.error}
            </div>
          )}

          <Chips label="Depends" items={s.depends_on} />
          <Chips label="Declared" items={s.tools_declared} />
          <Chips label="Called" items={called} tone="good" />
          {s.tools_blocked.length > 0 && <Chips label="Blocked" items={s.tools_blocked} tone="bad" />}

          <Block label="Input" text={s.input} />
          <Block label="Output" text={s.output} />
        </div>
      )}
    </div>
  )
}

export function ExecutionTrace({ steps, live }: { steps: TraceStep[]; live?: boolean }) {
  if (steps.length === 0) {
    return (
      <Empty icon="◆" title="No trace yet">
        {live ? 'Steps will appear here as agents run.' : 'This run recorded no steps.'}
      </Empty>
    )
  }
  const tokens = steps.reduce((a, s) => a + s.tokens, 0)
  const total = steps.reduce((a, s) => a + (s.duration_ms ?? 0), 0)
  return (
    <div className="rounded-[10px] border border-[var(--color-line)] bg-[var(--color-surface-1)]">
      <div className="flex items-center gap-3 px-3 py-2 border-b border-[var(--color-line)] text-[11px] text-[var(--color-ink-muted)] tabular-nums">
        <span>{steps.length} step{steps.length === 1 ? '' : 's'}</span>
        <span>{tokens.toLocaleString()} tok</span>
        <span>{dur(total)} total</span>
        {live && <Badge tone="info">live</Badge>}
      </div>
      {steps.map((s) => <Row key={s.node_id} s={s} />)}
    </div>
  )
}
