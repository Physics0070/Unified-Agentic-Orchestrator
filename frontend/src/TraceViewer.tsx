import { useEffect, useMemo, useRef, useState } from 'react'
import type { EventType, GraphSpec, LogEvent } from './api'
import { deriveStatuses, type NodeStatus } from './GraphCanvas'
import { Badge } from './ui'

/** Security events are never a normal log line. */
const ALERT: EventType[] = ['TOOL_BLOCKED', 'INJECTION_BLOCKED']
const BAD: EventType[] = ['HANDOFF_REJECTED', 'BRANCH_FAILED', 'BUDGET_EXCEEDED', 'CANCEL']
const GOOD: EventType[] = ['HANDOFF_VALIDATED', 'NODE_END', 'RUN_END', 'APPROVAL_GRANTED']
const WARN: EventType[] = ['REPAIR_ATTEMPT', 'RETRY', 'COMPENSATE']

function tone(t: EventType) {
  if (ALERT.includes(t)) return 'alert'
  if (BAD.includes(t)) return 'bad'
  if (WARN.includes(t)) return 'warn'
  if (GOOD.includes(t)) return 'good'
  return 'default'
}

function summarise(e: LogEvent): string {
  const p = e.payload ?? {}
  switch (e.event_type) {
    case 'TOOL_BLOCKED':
      return `${p.agent_id ?? e.agent_id} attempted '${p.attempted_tool}' — outside declared capabilities`
    case 'INJECTION_BLOCKED':
      return `payload from '${p.from_node}' quarantined before '${p.into_node}' — ${(p.rules as string[])?.join(', ')}`
    case 'HANDOFF_REJECTED':
      return `schema rejected: ${(p.errors as string[])?.[0] ?? ''}`
    case 'REPAIR_ATTEMPT':
      return `returning validator errors to producer (${p.attempt}/${p.of})`
    case 'HANDOFF_VALIDATED':
      return `contract satisfied on attempt ${p.attempt}`
    case 'BRANCH_FAILED':
      return String(p.reason ?? 'branch failed')
    case 'BUDGET_EXCEEDED':
      return p.reason === 'timeout_s' ? `timeout after ${p.timeout_s}s` : `spent ${p.spent}/${p.budget} tokens`
    case 'RETRY':
      return p.fallback ? `${p.primary} → ${p.fallback}` : `retry ${p.attempt}/${p.of}`
    case 'COUNCIL_VERDICT':
      return `chairman ${p.chairman} · winner ${p.winner_label} (${p.winner_author}) · disagreement ${p.disagreement}`
    case 'COUNCIL_PROPOSAL':
      return `proposal from ${p.model}`
    case 'COUNCIL_RANKING':
      return `anonymous ranking: ${(p.order as string[])?.join(' > ')}`
    case 'RUN_END':
      return `parallel efficiency ${p.parallel_efficiency}× · wall ${p.wall_clock_ms}ms`
    case 'TOOL_RESULT':
      return `${p.model ?? p.tool}${p.replayed ? ' (served from log)' : ''}`
    case 'COMPENSATE':
      return `compensating for '${p.compensates}'`
    default:
      return ''
  }
}

/** Continuous left rule, coloured by severity - the thing that makes a wall
 *  of log lines scannable without reading any of them. */
const GUTTER: Record<string, string> = {
  alert: 'border-l-[var(--color-failed)]',
  bad: 'border-l-[var(--color-failed)]',
  warn: 'border-l-[var(--color-warn)]',
  good: 'border-l-[var(--color-done)]',
  default: 'border-l-[var(--color-line)]',
}

/** Resting state. "No events yet" tells you nothing; the vocabulary that is
 *  about to stream past tells you what this panel is for. */
function TraceLegend({ live }: { live: boolean }) {
  const rows: [string, string, string][] = [
    ['NODE_START', 'good', 'an agent begins'],
    ['HANDOFF_VALIDATED', 'good', 'a typed contract was satisfied'],
    ['HANDOFF_REJECTED', 'bad', 'wrong shape - the error goes back'],
    ['REPAIR_ATTEMPT', 'warn', 'the producer tries again'],
    ['TOOL_BLOCKED', 'alert', 'a capability it never declared'],
    ['RUN_END', 'good', 'wall clock and parallel efficiency'],
  ]
  return (
    <div className="p-4">
      <p className="text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)] mb-2.5">
        {live ? 'waiting for the first event' : 'what streams here'}
      </p>
      <div className="space-y-0">
        {rows.map(([name, t, what]) => (
          <div key={name} className={`flex items-baseline gap-2 border-l-2 pl-2.5 py-[3px] ${GUTTER[t]}`}>
            <span className="text-[var(--color-ink-muted)] w-[150px] shrink-0">{name}</span>
            <span className="text-[var(--color-ink-faint)] min-w-0">{what}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * One event: a severity gutter, the type, a one-line summary, and the payload
 * one click away. <details> rather than a useState set, because the browser
 * already owns open/closed - and a trace viewer that cannot show the payload
 * answers every question except the one you opened it with.
 */
function EventRow({ e, showNode }: { e: LogEvent; showNode: boolean }) {
  const t = tone(e.event_type)
  const hasPayload = !!e.payload && Object.keys(e.payload).length > 0
  return (
    <details
      className={`border-b border-[var(--color-surface-2)] border-l-2 ${GUTTER[t]} ${
        t === 'alert' ? 'bg-[#f8717114]' : ''
      }`}
    >
      <summary
        className={`flex gap-2 px-3 py-1.5 ${hasPayload ? 'cursor-pointer' : 'cursor-default'} marker:content-none [&::-webkit-details-marker]:hidden hover:bg-[var(--color-surface-2)]`}
      >
        <span className="text-[var(--color-ink-faint)] tabular-nums w-8 shrink-0">{e.seq}</span>
        <span className="shrink-0 w-[150px]">
          <Badge tone={t === 'alert' ? 'bad' : t === 'bad' ? 'bad' : t === 'warn' ? 'warn' : t === 'good' ? 'good' : 'default'}>
            {e.event_type}
          </Badge>
        </span>
        {showNode && (
          <span className="text-[var(--color-ink-muted)] w-[86px] shrink-0 truncate">{e.node_id ?? '—'}</span>
        )}
        <span className={`flex-1 min-w-0 break-words ${t === 'alert' ? 'text-[var(--color-failed)] font-semibold' : 'text-[var(--color-ink-muted)]'}`}>
          {summarise(e)}
        </span>
        {hasPayload && (
          <span aria-hidden className="text-[var(--color-ink-faint)] shrink-0 select-none">+</span>
        )}
        {e.latency_ms != null && (
          <span className="text-[var(--color-ink-faint)] tabular-nums shrink-0">{e.latency_ms}ms</span>
        )}
      </summary>
      {hasPayload && (
        <pre className="mx-3 mb-2 ml-[46px] px-2.5 py-2 rounded-md bg-[var(--color-surface-0)] border border-[var(--color-line)] text-[10.5px] leading-relaxed text-[var(--color-ink-muted)] overflow-x-auto">
          {JSON.stringify(e.payload, null, 2)}
        </pre>
      )}
    </details>
  )
}

/* ------------------------------------------------------------- step timeline */
/* The flat log answers "what happened, in order". The question you usually
   arrive with is "which agent went wrong" - so the default view groups events
   by node into one card per step, the way Claude Code and Codex group a run
   into tool calls. A failed or blocked step opens itself; the rest stay shut. */

interface Step {
  id: string
  status: NodeStatus
  ms: number | null
  tokens: number
  events: LogEvent[]
}

/** Events with no node (RUN_START / RUN_END) belong to the run itself. */
const RUN_STEP = 'run'

function groupSteps(events: LogEvent[]): Step[] {
  const order: string[] = []
  const by = new Map<string, LogEvent[]>()
  for (const e of events) {
    const id = e.node_id ?? RUN_STEP
    let list = by.get(id)
    if (!list) { list = []; by.set(id, list); order.push(id) }
    list.push(e)
  }
  return order.map((id) => {
    const evs = by.get(id)!
    let status: NodeStatus
    let ms: number | null = null
    if (id === RUN_STEP) {
      const end = evs.find((e) => e.event_type === 'RUN_END')
      status = !end ? 'running' : end.payload?.ok === false ? 'failed' : 'done'
      ms = (end?.payload?.wall_clock_ms as number | undefined) ?? null
    } else {
      // Same derivation the graph uses, so a step and its node can never
      // disagree about whether they failed.
      status = deriveStatuses(evs)[id] ?? 'running'
      ms = evs.filter((e) => e.event_type === 'NODE_END')
        .reduce((a, e) => a + (e.latency_ms ?? 0), 0) || null
    }
    const tokens = evs.reduce((a, e) => a + e.tokens_in + e.tokens_out, 0)
    return { id, status, ms, tokens, events: evs }
  })
}

function fmtMs(ms: number) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function StatusGlyph({ status }: { status: NodeStatus }) {
  if (status === 'running') {
    return <span aria-label="running" className="h-2 w-2 rounded-full bg-[var(--color-running)] is-running shrink-0" />
  }
  const g: Record<string, [string, string]> = {
    done: ['✓', 'var(--color-done)'],
    failed: ['✕', 'var(--color-failed)'],
    blocked: ['⊘', 'var(--color-blocked)'],
  }
  const [ch, color] = g[status] ?? ['·', 'var(--color-ink-faint)']
  return <span aria-label={status} className="w-2 text-center text-[11px] shrink-0 leading-none" style={{ color }}>{ch}</span>
}

function StepCard({ step, graph, selected, onSelect }: {
  step: Step
  graph?: GraphSpec | null
  selected?: string | null
  onSelect?: (id: string) => void
}) {
  const node = graph?.nodes.find((n) => n.id === step.id)
  const model = node?.agent?.model
    ?? (step.events.find((e) => e.event_type === 'TOOL_RESULT')?.payload?.model as string | undefined)
  const attention = step.status === 'failed' || step.status === 'blocked'
  return (
    <details
      // Remounts when the status changes, so a step that starts running and
      // ends failed opens itself at the moment it fails.
      key={`${step.id}:${step.status}`}
      id={`step-${step.id}`}
      open={attention}
      // A row, not a card: a hairline underneath and a rule on the left that
      // carries state. Nothing is boxed, so a long run reads as one list.
      className={`rise border-b border-[var(--color-line)] border-l-2 ${
        selected === step.id
          ? 'border-l-[var(--color-ink)] bg-[var(--color-surface-4)]/45'
          : attention ? 'border-l-[var(--color-failed)]' : 'border-l-transparent'
      }`}
    >
      <summary
        onClick={() => onSelect?.(step.id)}
        className="flex items-center gap-2.5 px-3 py-2 cursor-pointer marker:content-none [&::-webkit-details-marker]:hidden hover:bg-[var(--color-surface-2)]"
      >
        <StatusGlyph status={step.status} />
        <span className="text-[13px] text-[var(--color-ink)]">{step.id}</span>
        {node && <Badge>{node.type}</Badge>}
        {model && (
          <span className="text-[10px] text-[var(--color-ink-faint)] truncate min-w-0">
            {model.replace(/^(ollama|groq|gemini):/, '')}
          </span>
        )}
        <span className="ml-auto flex items-center gap-3 text-[10.5px] text-[var(--color-ink-faint)] tabular-nums shrink-0">
          {step.tokens > 0 && <span>{step.tokens.toLocaleString()} tok</span>}
          {step.ms != null && <span>{fmtMs(step.ms)}</span>}
          <span aria-hidden className="opacity-60">{step.events.length}</span>
        </span>
      </summary>
      <div className="border-t border-[var(--color-line)]">
        {step.events.map((e) => <EventRow key={e.seq} e={e} showNode={false} />)}
      </div>
    </details>
  )
}

/** Copying the log is how an engineer gets this run in front of a colleague.
 *  The clipboard write can be refused (older webviews), so the confirmation
 *  only appears when it actually resolved. */
async function copyTrace(events: LogEvent[], done: (v: 'ok' | 'fail' | null) => void) {
  try {
    await navigator.clipboard.writeText(JSON.stringify(events, null, 2))
    done('ok')
  } catch {
    // Browsers refuse the clipboard in plenty of ordinary situations (an
    // unfocused window, a locked-down webview). Saying so is better than
    // claiming a copy that never happened - and better than a button that
    // silently does nothing.
    done('fail')
  }
  setTimeout(() => done(null), 1600)
}

export function TraceViewer({ events, live, graph, selected, onSelect }: {
  events: LogEvent[]
  live: boolean
  graph?: GraphSpec | null
  selected?: string | null
  onSelect?: (id: string) => void
}) {
  const [view, setView] = useState<'steps' | 'log'>('steps')
  const [filter, setFilter] = useState<'all' | 'security' | 'contract'>('all')
  const [query, setQuery] = useState('')
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null)
  const [stick, setStick] = useState(true)
  const endRef = useRef<HTMLDivElement>(null)

  const shown = useMemo(() => {
    let out = events
    if (filter === 'security') out = out.filter((e) => ALERT.includes(e.event_type))
    else if (filter === 'contract')
      out = out.filter((e) =>
        ['HANDOFF_EMITTED', 'HANDOFF_VALIDATED', 'HANDOFF_REJECTED', 'REPAIR_ATTEMPT'].includes(e.event_type))
    const q = query.trim().toLowerCase()
    if (q) {
      out = out.filter((e) =>
        `${e.event_type} ${e.node_id ?? ''} ${e.agent_id ?? ''} ${summarise(e)}`
          .toLowerCase().includes(q))
    }
    return out
  }, [events, filter, query])

  const steps = useMemo(() => groupSteps(shown), [shown])

  useEffect(() => {
    if (stick && live) endRef.current?.scrollIntoView({ block: 'end' })
  }, [shown.length, stick, live])

  // Clicking a node in the graph brings its step into view.
  useEffect(() => {
    if (selected && view === 'steps') {
      document.getElementById(`step-${selected}`)?.scrollIntoView({ block: 'nearest' })
    }
  }, [selected, view])

  const alerts = events.filter((e) => ALERT.includes(e.event_type)).length

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-1.5 flex-wrap px-3 py-2 border-b border-[var(--color-line)] shrink-0">
        <div role="tablist" aria-label="Trace view" className="flex p-0.5 mr-1 rounded-md bg-[var(--color-surface-2)]">
          {(['steps', 'log'] as const).map((v) => (
            <button
              key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
              className={`min-h-7 px-2.5 rounded text-[11px] transition-colors ${
                view === v
                  ? 'bg-[var(--color-surface-4)] text-[var(--color-ink)]'
                  : 'text-[var(--color-ink-faint)] hover:text-[var(--color-ink-muted)]'
              }`}
            >
              {v}
            </button>
          ))}
        </div>
        {(['all', 'contract', 'security'] as const).map((f) => (
          <button
            key={f} onClick={() => setFilter(f)}
            className={`min-h-8 px-2.5 rounded text-[11px] transition-colors ${
              filter === f
                ? 'bg-[var(--color-surface-3)] text-[var(--color-ink)]'
                : 'text-[var(--color-ink-faint)] hover:text-[var(--color-ink-muted)]'
            }`}
          >
            {f}
            {f === 'security' && alerts > 0 && (
              <span className="ml-1 text-[var(--color-failed)] font-semibold">{alerts}</span>
            )}
          </button>
        ))}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter…"
          aria-label="Filter events"
          className="ml-2 min-w-0 flex-1 max-w-[150px] h-8 px-2 rounded bg-[var(--color-surface-2)] border border-[var(--color-line)] font-mono text-[11px] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)] outline-none"
        />
        <button
          onClick={() => copyTrace(events, setCopied)}
          disabled={events.length === 0}
          title="Copy the whole event log as JSON"
          className="ml-auto min-h-8 px-2.5 rounded text-[11px] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] disabled:opacity-40 transition-colors"
        >
          {copied === 'ok' ? <span className="text-[var(--color-done)]">copied</span>
            : copied === 'fail' ? <span className="text-[var(--color-warn)]">blocked</span>
            : 'copy'}
        </button>
        <label className="flex items-center gap-1.5 text-[11px] text-[var(--color-ink-faint)] cursor-pointer">
          <input type="checkbox" checked={stick} onChange={(e) => setStick(e.target.checked)} className="accent-[var(--color-accent)]" />
          follow
        </label>
      </div>

      <div className="flex-1 min-h-0 overflow-auto font-mono text-[11px]">
        {shown.length === 0 && events.length > 0 && (
          <p className="p-4 text-[var(--color-ink-faint)]">
            No events match “{query || filter}”.
          </p>
        )}
        {events.length === 0 && <TraceLegend live={live} />}
        {view === 'steps' ? (
          <div>
            {steps.map((step) => (
              <StepCard key={step.id} step={step} graph={graph} selected={selected} onSelect={onSelect} />
            ))}
          </div>
        ) : (
          shown.map((e) => <EventRow key={e.seq} e={e} showNode />)
        )}
        <div ref={endRef} />
      </div>
    </div>
  )
}
