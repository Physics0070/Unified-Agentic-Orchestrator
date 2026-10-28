/**
 * The chat surface.
 *
 * This is the product: you ask, it answers. The orchestration is visible but
 * subordinate - the routing line sits under each answer and the full
 * benchmarks live in a drawer, so the transcript reads like a conversation
 * rather than a dashboard that happens to contain prose.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api, API_BASE, getWorkspaceId,
  type Benchmarks, type ChatFile, type ChatMessage, type PlanInfo, type SpecialistInfo,
  streamChat,
} from './api'
import { GraphCanvas, type NodeStatus } from './GraphCanvas'
import { usePersisted } from './store'
import { ExecutionTrace } from './TracePanel'
import { Badge, Button, Empty, Icon, Spinner } from './ui'

interface Turn {
  question: string
  answer: string
  plan?: PlanInfo
  specialists: SpecialistInfo[]
  bench?: Benchmarks
  file?: ChatFile
  status?: string
  done: boolean
  error?: string
}

const FILE_KIND_LABEL: Record<ChatFile['kind'], string> = {
  xlsx: 'Excel sheet', csv: 'CSV file', docx: 'Word document', pdf: 'PDF',
  pptx: 'Slide deck', md: 'Markdown file', txt: 'Text file',
}

function shortModelName(m: string): string {
  return m.replace(/^(ollama|groq|gemini):/, '')
}

/**
 * Conversation persistence. There is no login in this app, so a conversation
 * has no natural server-side owner - localStorage (browser-scoped) is the
 * right home for it, not a stretch to avoid building auth. Capped well above
 * the "at least 10" floor rather than tuned to exactly 10.
 */
interface StoredConversation {
  id: string
  title: string
  turns: Turn[]
  updatedAt: number
}

const STORAGE_KEY = 'aco.chat.conversations'
const MAX_STORED = 50

function loadConversations(): StoredConversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function saveConversations(list: StoredConversation[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_STORED)))
  } catch {
    // Storage full or unavailable (private browsing) - the chat still works
    // this session, it just won't survive a reload.
  }
}

function titleFor(turns: Turn[]): string {
  const first = turns[0]?.question.trim() || 'New chat'
  return first.length > 48 ? `${first.slice(0, 48)}…` : first
}

function newConversationId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `c${Date.now()}_${Math.random().toString(36).slice(2)}`
}

/** Per-turn node status, derived from the same SSE data already rendered in
 * the transcript - no separate event vocabulary needed for the chat graph. */
/** Resting state for the workflow panel. The two shapes a turn can take,
 *  shown rather than described - the same move as the orchestrator's pipeline
 *  preview and trace legend. */
function RouteLegend() {
  const routes: [string, string][] = [
    ['fast', 'one model answers directly'],
    ['council', 'specialists run in parallel, then merge'],
  ]
  return (
    <div className="p-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)] mb-2.5">
        what gets drawn here
      </p>
      <ol className="space-y-0">
        {routes.map(([name, what], i) => (
          <li key={name} className="flex items-baseline gap-2.5 font-mono text-[11px] leading-[1.9]">
            <span className="text-[var(--color-ink-faint)] select-none">
              {i === routes.length - 1 ? '└─' : '├─'}
            </span>
            <span className="text-[var(--color-accent-ink)] w-[56px] shrink-0">{name}</span>
            <span className="text-[var(--color-ink-faint)] min-w-0">{what}</span>
          </li>
        ))}
      </ol>
      <p className="font-mono text-[10px] text-[var(--color-ink-faint)] mt-3 leading-relaxed">
        the router picks, and says which it picked under every answer.
      </p>
    </div>
  )
}

function deriveChatStatuses(turn: Turn): Record<string, NodeStatus> {
  const p = turn.plan
  const s: Record<string, NodeStatus> = {}
  if (!p) return s

  if (p.route === 'council') {
    s.fanout = 'done'
    for (const spec of p.specialists) {
      const reported = turn.specialists.find((x) => x.id === spec.id)
      s[spec.id] = reported ? (reported.error ? 'failed' : 'done') : 'running'
    }
    s.join = turn.specialists.length >= p.specialists.length ? 'done' : 'pending'
  }

  if (turn.error) s.answer = 'failed'
  else if (turn.done) s.answer = 'done'
  else if (turn.answer || turn.status === 'answering') s.answer = 'running'
  else s.answer = 'pending'

  return s
}

const STAGE_LABEL: Record<string, string> = {
  planning: 'Deciding how to answer this',
  consulting: 'Consulting specialists in parallel',
  answering: 'Writing the answer',
}

const COMPLEXITY_TONE: Record<string, 'default' | 'good' | 'warn'> = {
  simple: 'good', moderate: 'default', deep: 'warn',
}

function isTableRow(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line)
}
function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}
function isTableSep(line: string): boolean {
  if (!isTableRow(line)) return false
  const cells = tableCells(line)
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c))
}

/** Minimal markdown: fenced code, inline code, bold, links, headings, bullets, tables. */
function renderMarkdown(src: string) {
  const out: React.ReactNode[] = []
  const blocks = src.split(/```/)

  blocks.forEach((block, bi) => {
    if (bi % 2 === 1) {
      const nl = block.indexOf('\n')
      const body = nl === -1 ? block : block.slice(nl + 1)
      out.push(
        <pre key={`c${bi}`} className="chat-code">
          <code>{body.replace(/\n$/, '')}</code>
        </pre>,
      )
      return
    }
    const lines = block.split('\n')
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li]
      const key = `${bi}-${li}`
      if (!line.trim()) { out.push(<div key={key} className="chat-gap" />); continue }

      // Header row, separator row, then 1+ data rows.
      if (isTableRow(line) && isTableSep(lines[li + 1] ?? '')) {
        const header = tableCells(line)
        const rows: string[][] = []
        let j = li + 2
        while (j < lines.length && isTableRow(lines[j])) { rows.push(tableCells(lines[j])); j++ }
        out.push(
          <table key={key} className="chat-table">
            <thead><tr>{header.map((h, hi) => <th key={hi}>{inline(h)}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inline(c)}</td>)}</tr>
              ))}
            </tbody>
          </table>,
        )
        li = j - 1
        continue
      }

      const heading = /^(#{1,4})\s+(.*)$/.exec(line)
      if (heading) {
        out.push(
          <div key={key} className="chat-h" style={{ fontSize: heading[1].length <= 2 ? 15 : 14 }}>
            {inline(heading[2])}
          </div>,
        )
        continue
      }
      const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
      if (bullet) {
        out.push(<div key={key} className="chat-li">{inline(bullet[1])}</div>)
        continue
      }
      const num = /^\s*(\d+)\.\s+(.*)$/.exec(line)
      if (num) {
        out.push(
          <div key={key} className="chat-li">
            <span className="chat-num">{num[1]}.</span> {inline(num[2])}
          </div>,
        )
        continue
      }
      out.push(<div key={key} className="chat-p">{inline(line)}</div>)
    }
  })
  return out
}

/** The link target comes from model output. Only web and mail links are
 * allowed; `javascript:` and `data:` URLs would run on click. */
function safeHref(href: string): string {
  return /^(https?:\/\/|mailto:)/i.test(href) ? href : '#'
}

function inline(text: string): React.ReactNode[] {
  // Split on `code`, **bold** and [text](url), keeping delimiters so they can be styled.
  return text
    .split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\))/g)
    .filter(Boolean)
    .map((part, i) => {
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
        return <code key={i} className="chat-inline-code">{part.slice(1, -1)}</code>
      }
      if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part)
      if (link) {
        return (
          <a key={i} href={safeHref(link[2])} target="_blank" rel="noreferrer" className="chat-link">
            {link[1]}
          </a>
        )
      }
      return <span key={i}>{part}</span>
    })
}

function ms(n: number | null | undefined) {
  if (n === null || n === undefined) return '—'
  return n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`
}

function RouteLine({ turn, onOpen }: { turn: Turn; onOpen: () => void }) {
  const p = turn.plan
  if (!p) return null
  const b = turn.bench
  return (
    <div className="route-line">
      <Badge tone={COMPLEXITY_TONE[p.complexity] ?? 'default'}>{p.complexity}</Badge>
      <span className="route-sep">·</span>
      <span>{p.route === 'fast' ? 'answered directly' : `${p.specialists.length} specialists`}</span>
      {b && <><span className="route-sep">·</span><span>{ms(b.timing.total_ms)}</span></>}
      {b && b.parallel_efficiency !== null && (
        <><span className="route-sep">·</span><span>{b.parallel_efficiency}× parallel</span></>
      )}
      {b && (
        <button className="route-more" onClick={onOpen}>benchmarks</button>
      )}
    </div>
  )
}

function BenchDrawer({ turn, onClose }: { turn: Turn; onClose: () => void }) {
  const b = turn.bench
  if (!b) return null
  const mig = b.marginal_information_gain

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h3>Benchmarks</h3>
            <p className="drawer-sub">Measured for this turn, not estimated.</p>
          </div>
          <button className="drawer-x" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <section className="drawer-sec">
          <h4>Routing</h4>
          <div className="kv"><span>Route</span><b>{b.route}</b></div>
          <div className="kv"><span>Intent</span><b>{b.intent}</b></div>
          <div className="kv"><span>Complexity</span><b>{b.complexity}</b></div>
          <div className="kv"><span>Decided by</span><b>{b.planner_source}</b></div>
          {turn.plan?.rationale && (
            <div className="drawer-note">{renderMarkdown(turn.plan.rationale)}</div>
          )}
        </section>

        <section className="drawer-sec">
          <h4>Model routing</h4>
          <div className="kv"><span>Answer model</span><b>{b.answer_model}</b></div>
          <div className="drawer-note">{renderMarkdown(b.answer_model_reason)}</div>
          {b.specialists.length > 0 && (
            <>
              {b.specialists.map((s) => (
                <div key={s.id} className="spec-row">
                  <div className="spec-id">{s.id}</div>
                  <div className="spec-meta">
                    {s.model ?? '—'}
                    {s.reason && <> · {s.reason}</>}
                  </div>
                </div>
              ))}
            </>
          )}
        </section>

        {b.routing && (
          <section className="drawer-sec">
            <h4>Priority routing</h4>
            <div className="kv"><span>Chosen</span><b>{b.routing.model}</b></div>
            <div className="kv"><span>Priority</span><b>{b.routing.priority}</b></div>
            <div className="kv">
              <span>Weights</span>
              <b>
                cap {b.routing.weights.capability} · cost {b.routing.weights.cost} · lat {b.routing.weights.latency}
              </b>
            </div>
            <div className="drawer-note">{renderMarkdown(b.routing.reason)}</div>
            {b.routing.candidates.length > 0 && (
              <table className="chat-table">
                <thead>
                  <tr><th>Model</th><th>Cap</th><th>Cost</th><th>Lat</th><th>Score</th></tr>
                </thead>
                <tbody>
                  {b.routing.candidates.map((c) => (
                    <tr key={c.model}>
                      <td>{shortModelName(c.model)}</td>
                      <td>{c.capability.toFixed(2)}</td>
                      <td>{c.cost.toFixed(2)}</td>
                      <td>{c.latency.toFixed(2)}</td>
                      <td>{c.score.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}

        {b.knowledge && (
          <section className="drawer-sec">
            <h4>Knowledge</h4>
            {b.knowledge.used ? (
              <>
                {b.knowledge.semantic_backend && (
                  <div className="kv"><span>Search</span><b>{b.knowledge.semantic_backend}</b></div>
                )}
                {b.knowledge.hits.map((h, i) => (
                  <div key={i} className="kv">
                    <span>{h.doc_name} · part {h.ordinal + 1}</span><b>{h.score.toFixed(2)}</b>
                  </div>
                ))}
              </>
            ) : (
              <p className="drawer-note">No uploaded documents were used for this answer.</p>
            )}
          </section>
        )}

        <section className="drawer-sec">
          <h4>Timing</h4>
          <div className="kv"><span>Planning</span><b>{ms(b.timing.plan_ms)}</b></div>
          {b.route === 'council' && (
            <div className="kv"><span>Specialist fanout</span><b>{ms(b.timing.fanout_ms)}</b></div>
          )}
          <div className="kv"><span>First token</span><b>{ms(b.timing.first_token_ms)}</b></div>
          <div className="kv"><span>Total</span><b>{ms(b.timing.total_ms)}</b></div>
          <div className="kv">
            <span>Parallel efficiency</span>
            <b>{b.parallel_efficiency === null ? 'n/a — fast path' : `${b.parallel_efficiency}×`}</b>
          </div>
        </section>

        {b.specialists.length > 0 && (
          <section className="drawer-sec">
            <h4>Specialists</h4>
            {b.specialists.map((s) => (
              <div key={s.id} className="spec-row">
                <div className="spec-id">
                  {s.id}
                  {s.failed && <Badge tone="bad">failed</Badge>}
                </div>
                <div className="spec-meta">
                  {s.points} points · {ms(s.latency_ms)} · {s.tokens ?? 0} tok
                  {mig.available && mig.per_agent[s.id] !== undefined && (
                    <> · MIG {mig.per_agent[s.id].toFixed(3)}</>
                  )}
                </div>
              </div>
            ))}
          </section>
        )}

        {b.web_search?.used && (
          <section className="drawer-sec">
            <h4>Web search</h4>
            <div className="drawer-note">
              {renderMarkdown(
                b.web_search.sources.map((s) => `- [${s.title}](${s.url})`).join('\n'),
              )}
            </div>
          </section>
        )}

        {mig.available && mig.overlapping_pairs.length > 0 && (
          <section className="drawer-sec">
            <h4>Overlap detected</h4>
            <p className="drawer-note">
              These specialists produced near-identical content. The council was
              wider than this question needed.
            </p>
            {mig.overlapping_pairs.map((p, i) => (
              <div key={i} className="kv">
                <span>{p.a} ↔ {p.b}</span><b>{(p.similarity * 100).toFixed(0)}%</b>
              </div>
            ))}
          </section>
        )}

        <section className="drawer-sec">
          <h4>Cost</h4>
          <div className="kv"><span>Specialist tokens</span><b>{b.tokens.toLocaleString()}</b></div>
          <div className="kv">
            <span>Cost</span>
            <b>{b.cost_usd > 0 ? `$${b.cost_usd.toFixed(6)}` : '$0 — local models'}</b>
          </div>
          <div className="kv"><span>Answer length</span><b>{b.answer_chars} chars</b></div>
          <div className="kv"><span>Models</span><b>{b.models_used.join(', ') || '—'}</b></div>
        </section>

        {b.trace && b.trace.length > 0 && (
          <section className="drawer-sec">
            <h4>Execution trace</h4>
            <ExecutionTrace steps={b.trace} />
          </section>
        )}

        <section className="drawer-sec">
          <h4>Trace</h4>
          <p className="drawer-note">
            Every step above is a row in the append-only event log, replayable
            at zero provider cost.
          </p>
          <code className="drawer-run">{b.run_id}</code>
        </section>
      </aside>
    </div>
  )
}

const SUGGESTIONS = [
  'What is a race condition?',
  'Compare optimistic and pessimistic locking for a high-write ledger',
  'Review the trade-offs of event sourcing for an audit system',
]

function ConversationSidebar({ conversations, activeId, onSelect, onNew, onHide }: {
  onHide: () => void
  conversations: StoredConversation[]
  activeId: string
  onSelect: (id: string) => void
  onNew: () => void
}) {
  return (
    <aside className="chat-sidebar">
      <div className="chat-sidebar-head">
        <span className="chat-sidebar-title">Chats</span>
        <span className="flex items-center gap-1">
          <button className="chat-sidebar-new" onClick={onNew}>+ New</button>
          <button onClick={onHide} aria-label="Hide chat list" title="Hide chat list" className="p-1.5 text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]">
            <Icon name="panel" size={15} />
          </button>
        </span>
      </div>
      <div className="chat-sidebar-list">
        {conversations.length === 0 && (
          <p className="chat-sidebar-empty">Your conversations will appear here.</p>
        )}
        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`chat-sidebar-item ${c.id === activeId ? 'is-active' : ''}`}
          >
            <span className="chat-sidebar-item-title">{c.title}</span>
            <span className="chat-sidebar-item-meta">
              {c.turns.length} turn{c.turns.length === 1 ? '' : 's'}
            </span>
          </button>
        ))}
      </div>
    </aside>
  )
}

function WorkflowPanel({ turn }: { turn: Turn | undefined }) {
  const [selected, setSelected] = useState<string | null>(null)
  const p = turn?.plan

  return (
    <aside className="workflow-panel">
      <div className="workflow-head">
        <h4>Workflow</h4>
        {p && (
          <span className="workflow-sub">
            {p.route === 'fast' ? 'answered directly' : `${p.specialists.length} specialists`}
          </span>
        )}
      </div>

      {p ? (
        <div className="workflow-canvas">
          <GraphCanvas
            graph={p.graph} statuses={deriveChatStatuses(turn!)}
            selected={selected} onSelect={setSelected}
          />
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-auto">
          <div className="h-44"><Empty title="No workflow yet">Ask a question and the route the router picked is drawn here.</Empty></div>
          <RouteLegend />
        </div>
      )}

      {p && p.clarifying_questions.length > 0 && (
        <div className="workflow-questions">
          <h5>Could be more specific</h5>
          <ul>
            {p.clarifying_questions.map((q, i) => <li key={i}>{q}</li>)}
          </ul>
        </div>
      )}
    </aside>
  )
}

export function ChatPanel({ models }: { models: string[] }) {
  const [listOpen, setListOpen] = usePersisted('aco.chat.list', true)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const [forceSearch, setForceSearch] = useState(false)
  const [selectedModel, setSelectedModel] = useState<string>('auto')
  const [priority, setPriority] = useState('balanced')
  const [useKnowledge, setUseKnowledge] = useState(true)
  const [docCount, setDocCount] = useState(0)
  const [conversations, setConversations] = useState<StoredConversation[]>(loadConversations)
  const [activeId, setActiveId] = useState<string>(newConversationId)
  // Phones hide the sidebar/workflow columns; these open them as overlay sheets.
  const [sheet, setSheet] = useState<'chats' | 'flow' | null>(null)
  const abortRef = useRef<(() => void) | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    api.knowledgeDocs().then((k) => setDocCount(k.documents.length)).catch(() => {})
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  // Persist the active conversation on every change. Skipped while empty so
  // opening the app (or hitting "New") doesn't clutter the sidebar with
  // placeholders nobody asked anything in yet.
  useEffect(() => {
    if (turns.length === 0) return
    setConversations((prev) => {
      const updated: StoredConversation = {
        id: activeId, title: titleFor(turns), turns, updatedAt: Date.now(),
      }
      const next = prev.some((c) => c.id === activeId)
        ? prev.map((c) => (c.id === activeId ? updated : c))
        : [updated, ...prev]
      next.sort((a, b) => b.updatedAt - a.updatedAt)
      saveConversations(next)
      return next
    })
  }, [turns, activeId])

  const patch = useCallback((i: number, fn: (t: Turn) => Turn) => {
    setTurns((prev) => prev.map((t, j) => (j === i ? fn(t) : t)))
  }, [])

  const startNew = useCallback(() => {
    abortRef.current?.()
    setBusy(false)
    setOpen(null)
    setTurns([])
    setActiveId(newConversationId())
  }, [])

  const loadConversation = useCallback((id: string) => {
    const convo = conversations.find((c) => c.id === id)
    if (!convo) return
    abortRef.current?.()
    setBusy(false)
    setOpen(null)
    setActiveId(id)
    // A turn that was mid-stream when the tab closed has no stream to resume -
    // show it settled rather than spinning forever.
    setTurns(convo.turns.map((t) => ({ ...t, done: true, status: undefined })))
  }, [conversations])

  const send = useCallback((question: string) => {
    const q = question.trim()
    if (!q || busy) return

    // History is the completed turns only; the in-flight one is not context yet.
    const history: ChatMessage[] = turns.flatMap((t) =>
      t.done && t.answer
        ? [{ role: 'user' as const, content: t.question },
           { role: 'assistant' as const, content: t.answer }]
        : [],
    )

    let index = 0
    setTurns((prev) => {
      index = prev.length
      return [...prev, { question: q, answer: '', specialists: [], done: false }]
    })
    setInput('')
    setBusy(true)

    abortRef.current = streamChat(
      {
        message: q, history, models,
        forcedModel: selectedModel === 'auto' ? undefined : selectedModel,
        forceWebSearch: forceSearch,
        priority, useKnowledge, workspaceId: getWorkspaceId(),
      },
      {
        onStatus: (stage) => patch(index, (t) => ({ ...t, status: stage })),
        onPlan: (plan) => patch(index, (t) => ({ ...t, plan })),
        onSpecialist: (s) =>
          patch(index, (t) => ({ ...t, specialists: [...t.specialists, s] })),
        onToken: (tok) => patch(index, (t) => ({ ...t, answer: t.answer + tok })),
        onBenchmarks: (bench) => patch(index, (t) => ({ ...t, bench })),
        onFile: (file) => patch(index, (t) => ({ ...t, file })),
        onError: (detail) => {
          patch(index, (t) => ({ ...t, error: detail, done: true, status: undefined }))
          setBusy(false)
        },
        // Settle on the stream closing rather than on `benchmarks` arriving:
        // a turn that ends without them must still release the composer.
        onDone: () => {
          patch(index, (t) => ({ ...t, done: true, status: undefined }))
          setBusy(false)
        },
      },
    )
  }, [busy, models, patch, turns, selectedModel, forceSearch, priority, useKnowledge])

  const stop = () => {
    abortRef.current?.()
    setBusy(false)
    setTurns((prev) => prev.map((t) => (t.done ? t : { ...t, done: true, status: undefined })))
  }

  return (
    <div className={`chat-shell ${listOpen ? '' : 'no-list'}`}>
      {listOpen ? (
        <ConversationSidebar
          conversations={conversations} activeId={activeId}
          onSelect={loadConversation} onNew={startNew} onHide={() => setListOpen(false)}
        />
      ) : (
        <button onClick={() => setListOpen(true)} aria-label="Show chat list" title="Show chat list"
          className="chat-list-reopen"><Icon name="chat" size={14} /> Chats</button>
      )}
      <div className="chat-wrap">
      <div className="chat-mobilebar">
        <button onClick={() => setSheet('chats')}><Icon name="chat" size={14} /> Chats</button>
        <button onClick={() => { startNew() }}>+ New</button>
        {turns.length > 0 && <button onClick={() => setSheet('flow')}>Workflow</button>}
      </div>
      <div className="chat-scroll">
        {turns.length === 0 && (
          <div className="chat-hero">
            <h1>Ask anything.</h1>
            <p>
              Simple questions are answered directly. Harder ones are split
              across specialists that run in parallel — and every answer shows
              what it cost to produce.
            </p>
            <div className="chat-sugg">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <article key={i} className="turn">
            <div className="bubble-user">{t.question}</div>

            {t.status && !t.answer && (
              <div className="thinking">
                <Spinner />
                <span>{STAGE_LABEL[t.status] ?? t.status}</span>
              </div>
            )}

            {t.specialists.length > 0 && !t.answer && (
              <div className="spec-chips">
                {t.specialists.map((s) => (
                  <Badge key={s.id} tone={s.error ? 'bad' : 'good'}>
                    {s.id} {s.error ? '·  failed' : `· ${s.points}`}
                  </Badge>
                ))}
              </div>
            )}

            {t.answer && <div className="bubble-bot">{renderMarkdown(t.answer)}</div>}

            {t.file && (
              <a
                className="file-chip"
                href={`${API_BASE}/api/chat/files/${t.file.file_id}`}
                download={t.file.filename}
              >
                ⬇ {FILE_KIND_LABEL[t.file.kind]} · {t.file.filename}
              </a>
            )}

            {t.error && <div className="turn-error">{t.error}</div>}

            <RouteLine turn={t} onOpen={() => setOpen(i)} />
          </article>
        ))}
        <div ref={endRef} />
      </div>

      <div className="composer-tools">
        <button
          type="button"
          className={`composer-toggle${forceSearch ? ' is-active' : ''}`}
          onClick={() => setForceSearch((v) => !v)}
          title="Force a web search for this message, instead of only when the question looks like it needs current information"
        >
          🔎 Search {forceSearch ? 'on' : 'auto'}
        </button>
        <select
          className="composer-select"
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          title="Which model answers - auto picks the best fit for the question"
        >
          <option value="auto">Auto (best fit)</option>
          {models.map((m) => (
            <option key={m} value={m}>{shortModelName(m)}</option>
          ))}
        </select>
        <select
          className="composer-select"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          title="What the router optimises for when picking a model"
        >
          {['balanced', 'quality', 'speed', 'cost'].map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <button
          type="button"
          className={`composer-toggle${useKnowledge ? ' is-active' : ''}`}
          onClick={() => setUseKnowledge((v) => !v)}
          title="Ground answers in the documents you uploaded"
        >
          📄 My documents {useKnowledge ? 'on' : 'off'}{docCount > 0 && ` (${docCount})`}
        </button>
      </div>

      <div className="composer">
        <div className="composer-field">
          {/* Same prompt as the orchestrator's goal bar: both surfaces take a
              command, so both should look like they do. */}
          <span aria-hidden className="composer-prompt">aco&nbsp;❯</span>
          <textarea
          ref={taRef}
          value={input}
          rows={1}
          placeholder="Ask a question…"
          onChange={(e) => {
            setInput(e.target.value)
            const el = e.target
            el.style.height = 'auto'
            el.style.height = `${Math.min(el.scrollHeight, 200)}px`
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send(input)
              if (taRef.current) taRef.current.style.height = 'auto'
            }
          }}
        />
        </div>
        {busy
          ? <Button onClick={stop} variant="ghost">Stop</Button>
          : <Button onClick={() => send(input)} disabled={!input.trim()}>Send</Button>}
      </div>

      {open !== null && turns[open] && (
        <BenchDrawer turn={turns[open]} onClose={() => setOpen(null)} />
      )}
      </div>

      <WorkflowPanel turn={turns[turns.length - 1]} />

      {sheet && (
        <div className="chat-sheet" onClick={() => setSheet(null)}>
          <div className="chat-sheet-body" onClick={(e) => e.stopPropagation()}>
            {sheet === 'chats'
              ? <ConversationSidebar
                  conversations={conversations} activeId={activeId}
                  onSelect={(id) => { loadConversation(id); setSheet(null) }}
                  onNew={() => { startNew(); setSheet(null) }} onHide={() => setSheet(null)} />
              : <WorkflowPanel turn={turns[turns.length - 1]} />}
          </div>
        </div>
      )}
    </div>
  )
}
