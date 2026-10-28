/**
 * Everything the app remembers, in one place.
 *
 * There is no login and no server-side account, so "your history" can only
 * mean this browser's. That is stated wherever it is shown, rather than
 * implied: a usage page that quietly means "usage on this laptop" is a usage
 * page that lies the moment someone opens it on another.
 */
import { useEffect, useState } from 'react'
import type { GraphSpec, LogEvent, RunMetrics, RunOutput } from './api'

export function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

/** False when the write was refused (quota, private mode) - callers decide
 *  what that means; this never throws. */
export function write(key: string, value: unknown): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function usePersisted<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => read(key, initial))
  useEffect(() => { write(key, value) }, [key, value])
  return [value, setValue] as const
}

/* ---------------------------------------------------------------- settings */

export type Accent = 'bone' | 'iris' | 'ember' | 'signal' | 'glacier'
export type Palette = 'prism' | 'aurora' | 'ember' | 'ice' | 'mono' | 'rainbow'

export interface Settings {
  /** Models the orchestrator may use. null = every model the backend offers. */
  models: string[] | null
  accent: Accent
  palette: Palette
  particles: boolean
  density: number
  split: boolean
  hero: boolean
  sidebar: boolean
}

export const DEFAULT_SETTINGS: Settings = {
  models: null,
  accent: 'bone',
  palette: 'prism',
  particles: true,
  density: 520,
  split: true,
  hero: true,
  sidebar: true,
}

/** Only overrides are stored, so a setting added later gets its default
 *  instead of `undefined` for everyone who already has a saved copy. */
export function useSettings() {
  const [stored, setStored] = usePersisted<Partial<Settings>>('aco.settings', {})
  const settings: Settings = { ...DEFAULT_SETTINGS, ...stored }
  const patch = (p: Partial<Settings>) => setStored((prev) => ({ ...prev, ...p }))
  const reset = () => setStored({})
  return { settings, patch, reset }
}

/* ------------------------------------------------------------- run history */

export interface ModelUsage { tokens: number; cost: number; calls: number }

export interface RunRecord {
  id: string
  goal: string
  ts: number
  ok: boolean
  failed: number
  wallMs: number | null
  tokens: number
  cost: number
  parallel: number | null
  handoff: number | null
  verdict: string | null
  weakest: string | null
  hash: string
  models: Record<string, ModelUsage>
  /** The heavy half. Kept for the newest few runs only, so a long history
   *  cannot fill the browser's storage. Without it a run is summary-only. */
  graph?: GraphSpec | null
  events?: LogEvent[]
  output?: RunOutput | null
  metrics?: RunMetrics | null
}

const RUNS_KEY = 'aco.runs'
const KEEP = 40
const KEEP_HEAVY = 12

const strip = (r: RunRecord): RunRecord =>
  ({ ...r, graph: undefined, events: undefined, output: undefined, metrics: undefined })

export function recordFor(a: {
  runId: string; goal: string; graph: GraphSpec | null; events: LogEvent[]
  metrics: RunMetrics | null; output: RunOutput | null
}): RunRecord {
  const end = a.events.find((e) => e.event_type === 'RUN_END')
  const failed = (end?.payload?.failed_nodes as string[] | undefined)?.length ?? 0

  const models: Record<string, ModelUsage> = {}
  for (const e of a.events) {
    if (e.event_type !== 'TOOL_RESULT') continue
    const m = (e.payload?.model as string | undefined) ?? 'unknown'
    const row = (models[m] ??= { tokens: 0, cost: 0, calls: 0 })
    row.tokens += e.tokens_in + e.tokens_out
    row.cost += e.cost_usd
    row.calls += 1
  }

  const t0 = a.events[0]?.ts, t1 = a.events[a.events.length - 1]?.ts
  const sum = (f: (e: LogEvent) => number) => a.events.reduce((x, e) => x + f(e), 0)
  return {
    id: a.runId, goal: a.goal, ts: Date.now(),
    ok: !!end && end.payload?.ok !== false, failed,
    wallMs: (end?.payload?.wall_clock_ms as number | undefined)
      ?? (t0 && t1 ? Date.parse(t1) - Date.parse(t0) : null),
    tokens: a.metrics?.total_tokens ?? sum((e) => e.tokens_in + e.tokens_out),
    cost: a.metrics?.total_cost_usd ?? sum((e) => e.cost_usd),
    parallel: a.metrics?.parallel_efficiency ?? null,
    handoff: a.metrics?.handoff_validity ?? null,
    verdict: a.metrics?.verdict ?? null,
    weakest: a.metrics?.least_valuable_agent ?? null,
    hash: a.graph?.config_hash ?? '',
    models, graph: a.graph, events: a.events, output: a.output, metrics: a.metrics,
  }
}

export function useRuns() {
  const [runs, setRuns] = useState<RunRecord[]>(() => read<RunRecord[]>(RUNS_KEY, []))
  useEffect(() => {
    // A refused write is retried with less in it rather than dropped: losing
    // the newest run's summary is worse than losing an old run's events.
    if (!write(RUNS_KEY, runs)) write(RUNS_KEY, runs.map((r, i) => (i < 3 ? r : strip(r))))
  }, [runs])

  const upsert = (rec: RunRecord) =>
    setRuns((prev) => {
      const old = prev.find((r) => r.id === rec.id)
      const merged = old ? { ...rec, ts: old.ts } : rec     // a re-record is not a new run
      return [merged, ...prev.filter((r) => r.id !== rec.id)]
        .slice(0, KEEP)
        .map((r, i) => (i < KEEP_HEAVY ? r : strip(r)))
    })
  const remove = (id: string) => setRuns((prev) => prev.filter((r) => r.id !== id))
  const dropOutput = (id: string, node: string | null) =>
    setRuns((prev) => prev.map((r) => (r.id !== id || !r.output ? r : {
      ...r, output: { ...r.output, outputs: r.output.outputs.filter((o) => o.node_id !== node) },
    })))
  return { runs, upsert, remove, dropOutput, clear: () => setRuns([]) }
}

/* ------------------------------------------------------------------ helpers */

export function ago(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function fmtMs(ms: number | null): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

/** Saves text as a file. Needs a real browser tab: sandboxed previews block
 *  script-started downloads, which is why callers also offer Copy. */
export function download(filename: string, text: string, mime = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const a = document.createElement('a')
  a.href = url; a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'artifact'
}

/** A run's deliverable as a document a person can paste into a ticket. */
export function toMarkdown(goal: string, when: number, node: string | null, content: unknown): string {
  const head = `# ${goal}\n\n_${new Date(when).toLocaleString()}${node ? ` · ${node}` : ''}_\n\n`
  const c = content as { findings?: { title?: unknown; severity?: unknown; recommendation?: unknown }[] }
  if (c && Array.isArray(c.findings)) {
    return head + (c.findings.length
      ? c.findings.map((f) => {
          const sev = f.severity != null ? `**${String(f.severity)}** ` : ''
          const rec = f.recommendation != null ? `\n  - ${String(f.recommendation)}` : ''
          return `- ${sev}${f.title != null ? String(f.title) : JSON.stringify(f)}${rec}`
        }).join('\n')
      : '_No findings._') + '\n'
  }
  return head + '```json\n' + JSON.stringify(content, null, 2) + '\n```\n'
}

/* -------------------------------------------------------------- chat history */

export interface ChatRecord {
  id: string; title: string; updatedAt: number
  turns: {
    question: string
    file?: { file_id: string; filename: string; kind: string }
    bench?: { tokens: number; cost_usd: number; models_used: string[]; timing: { total_ms: number } }
  }[]
}

/** Chat keeps its own storage key; the Usage and Artifacts views only read it. */
export const readChat = () => read<ChatRecord[]>('aco.chat.conversations', [])
