import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import {
  api, streamEvents,
  type CouncilSummary, type DnaPredictedPayload, type GraphSpec, type LogEvent,
  type MarginalValueReport, type ReplayDiff, type RunMetrics, type RunOutput, type TraceStep,
} from './api'
import { ChatPanel } from './ChatPanel'
import { ClarifyPanel, type Clarification } from './ClarifyPanel'
import { deriveStatuses, GraphCanvas, StatusLegend } from './GraphCanvas'
import { FinalOutput } from './RunOutputPanel'
import { AmbientField } from './ParticleField'
import { CommandPalette, Sidebar, Toasts, VIEW_TITLES, type Command, type Toast, type View } from './Shell'
import { RunOrb, type OrbState } from './Bubble'
import { recordFor, usePersisted, useRuns, useSettings, type RunRecord } from './store'
import { Badge, Button, Empty, fitnessTone, Icon, Panel, SkeletonRows, StepRail } from './ui'

// recharts (MetricsPanel) and the trace/DNA panels are only reachable from
// the orchestrator tab, never the default chat view, so they're lazy-loaded
// out of the main bundle. GraphCanvas is deliberately NOT here: ChatPanel's
// WorkflowPanel renders it too (chat's own routing graph), so it is already
// on the default path and Vite won't split a module that's also imported
// statically elsewhere - lazy-loading it here would just add a Suspense
// boundary for no bundle-size benefit.
const MetricsPanel = lazy(() => import('./MetricsPanel').then((m) => ({ default: m.MetricsPanel })))
const TraceViewer = lazy(() => import('./TraceViewer').then((m) => ({ default: m.TraceViewer })))
const ExecutionTrace = lazy(() => import('./TracePanel').then((m) => ({ default: m.ExecutionTrace })))
const KnowledgePanel = lazy(() => import('./KnowledgePanel').then((m) => ({ default: m.KnowledgePanel })))
const WorkflowDNAPanel = lazy(() =>
  import('./WorkflowDNAPanel').then((m) => ({ default: m.WorkflowDNAPanel })),
)

const ArtifactsView = lazy(() => import('./ArtifactsView').then((m) => ({ default: m.ArtifactsView })))
const UsageView = lazy(() => import('./UsageView').then((m) => ({ default: m.UsageView })))
const GitHubView = lazy(() => import('./GitHubView').then((m) => ({ default: m.GitHubView })))
const CompareView = lazy(() => import('./CompareView').then((m) => ({ default: m.CompareView })))
const SettingsView = lazy(() => import('./SettingsView').then((m) => ({ default: m.SettingsView })))
const CustomizeView = lazy(() => import('./SettingsView').then((m) => ({ default: m.CustomizeView })))

const viewFromHash = (): View => {
  const v = window.location.hash.split('/')[1]
  return v && v in VIEW_TITLES ? (v as View) : 'chat'
}

const DEMO_GOAL = 'Audit this repository and produce a prioritized remediation report.'

export default function App() {
  const [goal, setGoal] = useState(DEMO_GOAL)
  const [models, setModels] = useState<string[]>([])
  const [clarification, setClarification] = useState<Clarification | null>(null)
  const [graph, setGraph] = useState<GraphSpec | null>(null)
  const [council, setCouncil] = useState<CouncilSummary | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<LogEvent[]>([])
  const [live, setLive] = useState(false)
  const [metrics, setMetrics] = useState<RunMetrics | null>(null)
  const [report, setReport] = useState<MarginalValueReport | null>(null)
  const [replay, setReplay] = useState<{ diff: ReplayDiff; id: string } | null>(null)
  const [dnaPrediction, setDnaPrediction] = useState<DnaPredictedPayload | null>(null)
  const [output, setOutput] = useState<RunOutput | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'trace' | 'agents' | 'metrics' | 'dna'>('trace')
  // Chat is the default surface: the product answers questions, and the
  // orchestrator view explains how. The view lives in the URL (#app/usage) so
  // a refresh or a shared link lands where you were.
  const [mode, setMode] = useState<View>(viewFromHash)
  const setView = useCallback((v: View) => {
    history.replaceState(null, '', `#app/${v}`)
    setMode(v)
  }, [])
  const [steps, setSteps] = useState<TraceStep[]>([])
  const { settings, patch, reset } = useSettings()
  const { runs, upsert, dropOutput, clear } = useRuns()
  const [repo, setRepo] = usePersisted<string | null>('aco.github.repo', null)
  const [palette, setPalette] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [runGoal, setRunGoal] = useState('')
  const [backend, setBackend] = useState<{ ok: boolean | null; ms: number | null }>({ ok: null, ms: null })
  const recorded = useRef<string | null>(null)
  const toast = useCallback((text: string, tone?: Toast['tone']) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200)
  }, [])
  const stopRef = useRef<(() => void) | null>(null)
  const goalRef = useRef<HTMLInputElement>(null)

  // The sidebar's status dot and the model list share one probe: asking the
  // backend what it offers is also the cheapest proof it is up.
  const ping = useCallback(async () => {
    const t = performance.now()
    try {
      const m = await api.models()
      setModels(m.models)
      setBackend({ ok: true, ms: Math.round(performance.now() - t) })
    } catch { setBackend({ ok: false, ms: null }) }
  }, [])
  useEffect(() => {
    ping()
    const i = setInterval(ping, 30000)
    return () => { clearInterval(i); stopRef.current?.() }
  }, [ping])

  // Only models the person left enabled in Settings are offered to the council.
  const activeModels = settings.models ? models.filter((m) => settings.models!.includes(m)) : models
  const usable = activeModels.length ? activeModels : models

  // Back/forward and hand-edited URLs move the view too.
  useEffect(() => {
    const sync = () => { if (window.location.hash.startsWith('#app')) setMode(viewFromHash()) }
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  useEffect(() => { document.title = `${VIEW_TITLES[mode]} · Agent Council` }, [mode])

  // Below 768px the 248px sidebar would eat the screen, so it rests on the rail
  // there. That is a per-window state, not a saved preference: it must not
  // overwrite the user's choice for wide screens.
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 767px)').matches)
  const [railOpen, setRailOpen] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const on = () => { setNarrow(mq.matches); setRailOpen(false) }
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  const sidebarOpen = narrow ? railOpen : settings.sidebar
  const toggleSidebar = () => (narrow ? setRailOpen((o) => !o) : patch({ sidebar: !settingsRef.current.sidebar }))

  // Shortcuts are listed next to the controls they drive; an undiscoverable
  // shortcut is dead code. Bound on window rather than per-input so they work
  // wherever focus happens to be, and skipped while typing so Esc still
  // behaves normally inside a field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      const typing = (e.target as HTMLElement)?.matches?.('input, textarea')
      if (mod && e.key === 'Enter') { e.preventDefault(); runRef.current?.() }
      else if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((p) => !p) }
      else if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); toggleSidebar() }
      else if (mod && e.key === '1') { e.preventDefault(); setView('orchestrator') }
      else if (mod && e.key === '2') { e.preventDefault(); setView('chat') }
      else if (e.key === '/' && !typing) { e.preventDefault(); setView('orchestrator'); setTimeout(() => goalRef.current?.focus(), 0) }
      else if (e.key === 'Escape' && !typing) setError(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const settingsRef = useRef(settings)
  settingsRef.current = settings

  // Per-agent trace: poll while the run is live, then once more when it ends.
  useEffect(() => {
    if (tab !== 'agents' || !runId) return
    let dead = false
    const load = () => api.trace(runId).then((t) => { if (!dead) setSteps(t.steps) }).catch(() => {})
    load()
    if (!live) return () => { dead = true }
    const id = setInterval(load, 2000)
    return () => { dead = true; clearInterval(id) }
  }, [tab, runId, live])

  const guard = useCallback(async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null)
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }, [])

  const statuses = deriveStatuses(events)
  const locked = graph?.locked ?? false

  const step = metrics || report ? 4 : runId ? 3 : graph ? 2 : clarification ? 1 : 0

  // Every finished run is written to local history, once its metrics and
  // output have arrived. Re-saving the same id keeps its original timestamp.
  useEffect(() => {
    if (live || !runId || !events.length || !metrics || recorded.current === `open:${runId}`) return
    upsert(recordFor({ runId, goal: runGoal || goal, graph, events, metrics, output }))
    if (recorded.current !== runId) { recorded.current = runId; toast('Run saved to history') }
  }, [live, runId, events, metrics, output])

  const clearRun = () => {
    stopRef.current?.()
    setGraph(null); setRunId(null); setEvents([]); setMetrics(null); setReplay(null)
    setReport(null); setCouncil(null); setSelected(null); setDnaPrediction(null)
    setOutput(null); setClarification(null); setLive(false); setError(null); setTab('trace')
  }

  const newRun = () => {
    clearRun(); setGoal('')
    setView('orchestrator')
    setTimeout(() => goalRef.current?.focus(), 0)
  }

  const openRun = (r: RunRecord) => {
    clearRun()
    setGoal(r.goal); setRunGoal(r.goal)
    setGraph(r.graph ?? null); setEvents(r.events ?? []); setMetrics(r.metrics ?? null)
    setOutput(r.output ?? null); setRunId(r.id)
    recorded.current = `open:${r.id}`
    setView('orchestrator')
  }

  const askClarify = () => guard('clarify', async () => {
    setGraph(null); setRunId(null); setEvents([]); setMetrics(null); setReplay(null)
    setReport(null); setCouncil(null); setSelected(null)
    setDnaPrediction(null); setOutput(null)
    setClarification(await api.clarify(goal))
  })

  const compile = (answers: Record<string, string>) => guard('compile', async () => {
    const r = await api.compile(goal, answers, usable, clarification?.plan.intent)
    setGraph(r.graph); setCouncil(r.council); setClarification(null)
    setDnaPrediction(r.dna_prediction)
  })

  const lock = () => graph && setGraph({ ...graph, locked: !graph.locked })

  const run = () => guard('run', async () => {
    if (!graph) return
    setEvents([]); setMetrics(null); setReplay(null); setOutput(null); setTab('trace')
    const approvals = graph.nodes.filter((n) => n.type === 'approval').map((n) => n.id)
    const { run_id } = await api.start(goal, graph, approvals)
    setRunGoal(goal); recorded.current = null
    setRunId(run_id); setLive(true)
    stopRef.current?.()
    stopRef.current = streamEvents(
      run_id,
      (e) => setEvents((prev) => (prev.some((x) => x.seq === e.seq) ? prev : [...prev, e])),
      () => {
        setLive(false)
        api.metrics(run_id).then(setMetrics).catch(() => {})
        api.output(run_id).then(setOutput).catch(() => {})
      },
    )
  })

  const runRef = useRef<(() => void) | null>(null)
  runRef.current = () => { if (graph && !busy && !live) run() }

  const redAgent = () => guard('red', async () => {
    const r = await api.redAgent()
    setRunGoal('Red Agent drill'); recorded.current = null
    setRunId(r.run_id); setGraph(null); setMetrics(null); setClarification(null); setTab('trace')
    setEvents((await api.events(r.run_id)).events)
  })

  const doReplay = () => runId && guard('replay', async () => {
    setReplay({ diff: (await api.replay(runId)).diff, id: runId })
  })

  const doMarginal = () => guard('marginal', async () => {
    setTab('metrics')
    setReport(await api.marginalValue(goal, usable))
  })

  const runEnd = events.find((e) => e.event_type === 'RUN_END')
  const orb: { state: OrbState; label: string } =
    live || busy ? { state: 'running', label: busy && !live ? `Working: ${busy}` : 'Run in progress' }
    : runEnd ? (runEnd.payload?.ok === false ? { state: 'failed', label: 'Last run failed' } : { state: 'done', label: 'Last run finished' })
    : { state: 'idle', label: 'No run in progress' }

  const commands: Command[] = [
    ...(Object.keys(VIEW_TITLES) as View[]).map((v) => ({
      id: `go-${v}`, group: 'Go to', label: VIEW_TITLES[v], run: () => setView(v),
      hint: v === 'orchestrator' ? '⌘1' : v === 'chat' ? '⌘2' : undefined,
    })),
    { id: 'new', group: 'Action', label: 'New run', keywords: 'start reset', run: newRun },
    { id: 'prompt', group: 'Action', label: 'Focus the goal prompt', hint: '/', run: () => { setView('orchestrator'); setTimeout(() => goalRef.current?.focus(), 0) } },
    { id: 'run', group: 'Action', label: 'Run the current plan', hint: '⌘↵', keywords: 'execute start', run: () => { setView('orchestrator'); runRef.current?.() } },
    { id: 'sidebar', group: 'Action', label: sidebarOpen ? 'Hide sidebar' : 'Show sidebar', hint: '⌘B', run: toggleSidebar },
    { id: 'particles', group: 'Action', label: settings.particles ? 'Turn particles off' : 'Turn particles on', keywords: 'background animation', run: () => patch({ particles: !settings.particles }) },
    { id: 'landing', group: 'Go to', label: 'Back to the overview', run: () => { window.location.hash = '' } },
    ...runs.filter((r) => r.events).slice(0, 10).map((r) => ({
      id: `run-${r.id}`, group: 'Run', label: r.goal, keywords: 'open history', run: () => openRun(r),
    })),
  ]

  return (
    <div className="vm h-screen flex relative overflow-hidden" data-accent={settings.accent} data-palette={settings.palette}>
      {/* The prism: each mark splits into red, green and blue. */}
      {settings.particles && (
        <AmbientField
          key={`${settings.palette}-${settings.split}`}
          count={settings.density} split={settings.split}
          className="fixed inset-0 -z-10 pointer-events-none"
        />
      )}
      <Sidebar
        open={sidebarOpen} onToggle={toggleSidebar}
        view={mode} onView={setView} onNewRun={newRun}
        runs={runs} onOpenRun={openRun}
        counts={{ artifacts: runs.filter((r) => r.output?.outputs.length).length, runs: runs.length }}
        repo={repo} backend={backend}
      />
      <div className="flex-1 min-w-0 flex flex-col">
      {/* ---------------------------------------------------------- header */}
      <header className="shrink-0 border-b border-[var(--color-line)] bg-[var(--color-surface-1)]/70 backdrop-blur-md">
        <div className="flex items-center gap-2 lg:gap-3 pl-14 pr-3 lg:px-4 py-1.5 lg:py-2.5 lg:flex-wrap">
          <RunOrb state={orb.state} label={orb.label} size={22} />
          <h1 className="text-[12px] lg:text-[13px] truncate uppercase tracking-[0.08em]">{VIEW_TITLES[mode]}</h1>

          {mode === 'orchestrator' && (
            <div className="hidden lg:block ml-2"><StepRail current={step} /></div>
          )}

          <div className="ml-auto flex items-center gap-2 lg:flex-wrap">
            <button
              onClick={() => setPalette(true)}
              className="flex items-center gap-2 h-10 lg:h-8 px-3 rounded-full border border-[var(--color-line)] hover:border-[var(--color-line-strong)] text-[11px] text-[var(--color-ink-faint)]"
            >
              <Icon name="search" size={13} /> <span className="hidden sm:inline">Commands</span> <kbd className="font-mono opacity-70 hidden sm:inline">⌘K</kbd>
            </button>
            {mode === 'orchestrator' && (
              <Button
                onClick={redAgent} variant="danger" loading={busy === 'red'} disabled={!!busy}
                title="Undeclared tool call and a smuggled instruction, both blocked"
              >
                <span className="whitespace-nowrap">Red<span className="max-sm:hidden"> Agent</span></span>
              </Button>
            )}
          </div>
        </div>

        {/* ------------------------------------------------------ goal bar */}
        {mode === 'orchestrator' && <form
          className="flex items-center gap-2 px-3 lg:px-4 pb-2 lg:pb-3 flex-wrap"
          onSubmit={(e) => { e.preventDefault(); askClarify() }}
        >
          <div className="relative flex-1 min-w-[9.5rem] lg:min-w-[260px]">
            {/* A prompt, not a search box. This screen's whole vocabulary is
                a log and a command; typing a goal is the command. */}
            <span
              aria-hidden
              className="absolute left-3 top-1/2 -translate-y-1/2 font-mono text-[13px] text-[var(--color-accent-ink)] pointer-events-none select-none"
            >
              aco&nbsp;❯
            </span>
            <input
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              ref={goalRef}
              aria-label="Goal"
              placeholder="describe the goal in plain English"
              className="w-full min-h-11 pl-[62px] pr-12 lg:pr-16 rounded-[10px] bg-[var(--color-surface-2)] border border-[var(--color-line)] font-mono text-[13px] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)] outline-none transition-colors"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-2 text-[10px] text-[var(--color-ink-faint)] pointer-events-none">
              <kbd className="font-mono opacity-70">/</kbd>
              <span className="tabular-nums">{goal.trim().length}</span>
            </span>
          </div>

          <Button
            type="submit"
            variant={graph || runId || clarification ? 'default' : 'primary'}
            loading={busy === 'clarify'}
            disabled={!!busy || goal.trim().length < 10}
          >
            Start
          </Button>

          {live && runId && (
            <Button onClick={() => api.cancel(runId)} variant="danger">Cancel</Button>
          )}
        </form>}
      </header>

      {mode === 'chat' && <ChatPanel models={usable} />}

      {(mode === 'artifacts' || mode === 'compare' || mode === 'usage' || mode === 'github' || mode === 'settings' || mode === 'customize') && (
        <main className="flex-1 min-h-0 overflow-y-auto">
          <Suspense fallback={<div className="p-6"><SkeletonRows n={6} /></div>}>
            {mode === 'artifacts' && <ArtifactsView runs={runs} onOpenRun={openRun} onDrop={dropOutput} toast={toast} />}
            {mode === 'usage' && <UsageView runs={runs} />}
            {mode === 'compare' && <CompareView runs={runs} onOpenRun={openRun} />}
            {mode === 'github' && (
              <GitHubView
                repo={repo} setRepo={setRepo} toast={toast}
                onGoal={(g) => { setGoal(g); setView('orchestrator'); setTimeout(() => goalRef.current?.focus(), 0) }}
              />
            )}
            {mode === 'settings' && (
              <SettingsView
                settings={settings} patch={patch} reset={reset} models={models} backend={backend}
                onPing={ping} runs={runs.length} onClearRuns={clear} toast={toast}
              />
            )}
            {mode === 'customize' && <CustomizeView settings={settings} patch={patch} />}
          </Suspense>
        </main>
      )}

      {mode === 'knowledge' && (
        <main className="flex-1 min-h-0 overflow-y-auto">
          <Suspense fallback={<SkeletonRows n={8} />}>
            <KnowledgePanel />
          </Suspense>
        </main>
      )}

      {/* ----------------------------------------------------------- alerts */}
      {mode === 'orchestrator' && error && (
        <div
          role="alert"
          className="shrink-0 flex items-start gap-2 px-4 py-2 bg-[#f8717114] border-b border-[#f8717144] text-[12px] text-[var(--color-failed)]"
        >
          <span aria-hidden>⚠</span>
          <span className="flex-1 break-words">{error}</span>
          <button
            onClick={() => setError(null)}
            aria-label="Dismiss error"
            className="shrink-0 opacity-60 hover:opacity-100 px-1"
          >
            ✕
          </button>
        </div>
      )}

      {mode === 'orchestrator' && replay && (
        <div className="shrink-0 px-4 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] flex items-center gap-3 flex-wrap text-[12px]">
          <Badge tone={replay.diff.identical ? 'good' : 'bad'}>
            {replay.diff.identical ? '✓ DIFF IS ZERO' : `${replay.diff.output_diffs.length} DIFFS`}
          </Badge>
          <span className="text-[var(--color-ink-muted)]">
            {replay.diff.nodes_compared} nodes compared · cost $
            {replay.diff.original_cost_usd.toFixed(6)} → ${replay.diff.replay_cost_usd.toFixed(6)}{' '}
            · wall {replay.diff.original_wall_ms}ms → {replay.diff.replay_wall_ms}ms · served from
            the event log, nothing re-executed
          </span>
        </div>
      )}

      {mode === 'orchestrator' && runId && !live && (
        <RunSummary
          metrics={metrics} busy={busy} onReplay={doReplay} onMarginal={doMarginal}
        />
      )}

      {/* ------------------------------------------------------------- main */}
      {mode === 'orchestrator' && (
      <Suspense fallback={<main className="flex-1 min-h-0 p-3"><SkeletonRows n={8} /></main>}>
      <main className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden grid grid-cols-1 lg:grid-cols-[1.15fr_1fr] gap-2 lg:gap-3 p-2 lg:p-3">
        <Panel
          title={clarification ? 'Clarify & approve' : 'Agent graph'}
          subtitle={graph ? `${graph.nodes.length} nodes · ${graph.edges.length} edges` : undefined}
          right={graph ? <StatusLegend /> : undefined}
          className="min-h-[340px] max-lg:min-h-[72vh]"
        >
          {clarification ? (
            <ClarifyPanel
              clarification={clarification}
              onSubmit={compile}
              busy={busy === 'compile'}
            />
          ) : busy === 'compile' ? (
            <Empty icon="⚖" title="The council is deliberating">
              Two models are proposing designs, ranking each other anonymously, and a chairman is
              merging the winner.
            </Empty>
          ) : graph ? (
            <div className="h-full flex flex-col">
              {!runId && (
                <PlanCard
                  graph={graph} prediction={dnaPrediction} locked={locked} busy={busy}
                  onRun={run} onLock={lock}
                />
              )}
              <div className="flex-1 min-h-[320px] max-lg:min-h-[520px]">
                <GraphCanvas
                  graph={graph} statuses={statuses} selected={selected} onSelect={setSelected}
                />
              </div>
              {selected && <NodeInspector graph={graph} id={selected} locked={locked} />}
              {output && <FinalOutput output={output} />}
              {council && <CouncilStrip council={council} />}
            </div>
          ) : (
            <PipelinePreview hero={settings.hero} />
          )}
        </Panel>

        <Panel
          title={tab === 'trace' ? 'Live trace' : tab === 'agents' ? 'Agent trace' : tab === 'metrics' ? 'Honest metrics' : 'Workflow DNA'}
          subtitle={
            tab === 'trace'
              ? runId ? `${events.length} events` : undefined
              : tab === 'agents'
              ? runId ? `${steps.length} steps` : undefined
              : tab === 'metrics'
              ? metrics ? `${metrics.agents.length} agents measured` : undefined
              : runId ? 'predicts, then remembers' : undefined
          }
          className="min-h-[340px] max-lg:h-[78vh]"
          right={
            <div className="flex gap-0.5 p-0.5 rounded-lg bg-[var(--color-surface-2)]">
              {(['trace', 'agents', 'metrics', 'dna'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`min-h-10 lg:min-h-8 px-2.5 rounded-md text-[11px] whitespace-nowrap transition-colors ${
                    tab === t
                      ? 'bg-[var(--color-surface-4)] text-[var(--color-ink)]'
                      : 'text-[var(--color-ink-faint)] hover:text-[var(--color-ink-muted)]'
                  }`}
                >
                  {t === 'dna' ? 'workflow dna' : t}
                </button>
              ))}
            </div>
          }
        >
          {tab === 'trace' ? (
            live && events.length === 0
              ? <SkeletonRows n={6} />
              : <TraceViewer
                  events={events} live={live} graph={graph}
                  selected={selected} onSelect={setSelected}
                />
          ) : tab === 'agents' ? (
            runId
              ? <ExecutionTrace steps={steps} live={live} />
              : <Empty icon="◆" title="No run yet">Run a graph to see per-agent steps.</Empty>
          ) : tab === 'dna' ? (
            <WorkflowDNAPanel events={events} runId={runId} />
          ) : busy === 'marginal' ? (
            <Empty icon="📈" title="Running the same task at depth 1, 2, 3 and 4">
              Each depth is a full run. The report is allowed to conclude that a smaller graph was
              better.
            </Empty>
          ) : (
            <MetricsPanel metrics={metrics} report={report} />
          )}
        </Panel>
      </main>
      </Suspense>
      )}

      {mode === 'orchestrator' && (live || busy) && (
        <LiveBadge
          text={
            ({ clarify: 'asking', compile: 'council', replay: 'replay', marginal: 'measuring' } as Record<string, string>)[busy ?? ''] ?? 'running'
          }
        />
      )}

      {mode === 'orchestrator' && (
        <StatusBar
          busy={busy} live={live} runId={runId} graph={graph}
          clarification={clarification} events={events} metrics={metrics}
        />
      )}
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} />
      <Toasts items={toasts} />
    </div>
  )
}

/**
 * What a finished run says, and what you can do next. Replay and marginal
 * value used to be header buttons that were live all session and meaningful
 * only here; an action belongs next to the thing it acts on.
 *
 * The verdict leads because it is the one claim this product makes that
 * nothing else does - that the council was sometimes not worth convening.
 */
function RunSummary({ metrics, busy, onReplay, onMarginal }: {
  metrics: RunMetrics | null
  busy: string | null
  onReplay: () => void
  onMarginal: () => void
}) {
  const wasteful = !!metrics && (metrics.duplicate_work > 0 || !!metrics.least_valuable_agent)
  const [open, setOpen] = useState(false)
  return (
    <div className="shrink-0 px-2 lg:px-3 pb-2 lg:pb-3">
      <div
        className="rounded-[10px] border px-3 py-1.5 lg:py-2.5 flex items-center gap-x-3 gap-y-2 flex-wrap"
        style={{
          borderColor: wasteful ? 'var(--color-warn)' : 'var(--color-line)',
          background: wasteful ? 'linear-gradient(#fbbf240d, #fbbf240d), var(--color-surface-1)' : 'var(--color-surface-1)',
        }}
      >
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-ink-faint)] shrink-0">
          verdict
        </span>
        <p className={`text-[12.5px] leading-relaxed text-[var(--color-ink)] min-w-0 lg:min-w-[220px] flex-1 ${open ? '' : 'max-lg:truncate'}`}>
          {metrics ? metrics.verdict : 'Run finished.'}
          {metrics?.least_valuable_agent && (
            <>
              {' '}
              <span className="text-[var(--color-warn)]">
                Least valuable: <b className="font-mono">{metrics.least_valuable_agent}</b>.
              </span>
            </>
          )}
        </p>
        <button
          onClick={() => setOpen((o) => !o)} aria-expanded={open}
          className="lg:hidden shrink-0 min-h-10 px-2 text-[11px] text-[var(--color-ink-muted)]"
        >
          {open ? 'Less' : 'Details'}
        </button>
        <div className={`gap-2 shrink-0 ${open ? 'flex' : 'hidden lg:flex'}`}>
          <Button size="sm" onClick={onReplay} loading={busy === 'replay'} disabled={!!busy}
            title="Serve the run back from the event log; nothing re-executes">
            Replay
          </Button>
          <Button size="sm" onClick={onMarginal} loading={busy === 'marginal'} disabled={!!busy}
            title="Run the same goal at depth 1-4 and compare">
            {busy === 'marginal' ? 'depths 1-4…' : 'Marginal value'}
          </Button>
        </div>
      </div>
    </div>
  )
}

/**
 * The line along the bottom of a coding agent: what state it is in, and what
 * the session has cost so far. Replaces four stat tiles that spent a row of
 * screen on em-dashes until something had run.
 */
function StatusBar({ busy, live, runId, graph, clarification, events, metrics }: {
  busy: string | null
  live: boolean
  runId: string | null
  graph: GraphSpec | null
  clarification: Clarification | null
  events: LogEvent[]
  metrics: RunMetrics | null
}) {
  const end = events.find((e) => e.event_type === 'RUN_END')
  const failed = (end?.payload?.failed_nodes as string[] | undefined)?.length ?? 0
  const blocked = events.filter(
    (e) => e.event_type === 'TOOL_BLOCKED' || e.event_type === 'INJECTION_BLOCKED',
  ).length

  let label = 'idle'
  let color = 'var(--color-pending)'
  if (busy === 'clarify') { label = 'asking questions'; color = 'var(--color-running)' }
  else if (busy === 'compile') { label = 'council deliberating'; color = 'var(--color-running)' }
  else if (live) { label = 'running'; color = 'var(--color-running)' }
  else if (end) {
    label = failed ? `done · ${failed} failed, compensated` : 'done'
    color = failed ? 'var(--color-warn)' : 'var(--color-done)'
  } else if (graph && !runId) { label = 'plan ready · awaiting approval'; color = 'var(--color-accent-ink)' }
  else if (clarification) { label = 'awaiting your answers'; color = 'var(--color-accent-ink)' }

  const wall = end?.payload?.wall_clock_ms as number | undefined
  const t0 = events[0]?.ts, t1 = events[events.length - 1]?.ts
  const ms = wall ?? (t0 && t1 ? Date.parse(t1) - Date.parse(t0) : null)
  const tokens = metrics?.total_tokens ?? events.reduce((a, e) => a + e.tokens_in + e.tokens_out, 0)
  const cost = metrics?.total_cost_usd ?? events.reduce((a, e) => a + e.cost_usd, 0)

  const Item = ({ k, v, tone: c }: { k: string; v: string; tone?: string }) => (
    <span className="whitespace-nowrap">
      <span className="text-[var(--color-ink-faint)]">{k} </span>
      <span style={{ color: c ?? 'var(--color-ink-muted)' }}>{v}</span>
    </span>
  )

  return (
    <footer className="shrink-0 border-t border-[var(--color-line)] bg-[var(--color-surface-1)]/70 backdrop-blur-md px-3 lg:px-4 py-1.5 flex items-center gap-x-4 gap-y-1 max-lg:overflow-x-auto lg:flex-wrap font-mono text-[11px]">
      <span className="flex items-center gap-2 whitespace-nowrap" style={{ color }}>
        <span className={`h-1.5 w-1.5 rounded-full bg-current ${live || busy ? 'animate-pulse' : ''}`} />
        {label}
      </span>
      {runId && <Item k="run" v={runId.slice(0, 8)} />}
      {graph && <Item k="cfg" v={graph.config_hash.slice(0, 8)} />}
      {events.length > 0 && ms != null && <Item k="elapsed" v={ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`} />}
      {events.length > 0 && <Item k="tokens" v={tokens.toLocaleString()} />}
      {events.length > 0 && <Item k="cost" v={`$${cost.toFixed(6)}`} />}
      {metrics && <Item k="parallel" v={`${metrics.parallel_efficiency}×`} />}
      {metrics?.handoff_validity != null && (
        <Item
          k="handoffs" v={`${Math.round(metrics.handoff_validity * 100)}%`}
          tone={metrics.handoff_validity === 1 ? 'var(--color-done)' : 'var(--color-warn)'}
        />
      )}
      {blocked > 0 && <Item k="blocked" v={String(blocked)} tone="var(--color-failed)" />}
      <span className="ml-auto hidden lg:flex gap-3 text-[var(--color-ink-faint)] whitespace-nowrap">
        <span>/ prompt</span><span>⌘K commands</span><span>⌘↵ run</span><span>esc dismiss</span>
      </span>
    </footer>
  )
}

/**
 * The resting state of the graph panel: one statement, set large and light,
 * over iridescent light. It is the only place the interface is allowed
 * colour that is not state, and it goes away the moment there is a graph.
 * Below it, the pipeline's actual shape - the same information as the old
 * sentence, shown rather than described.
 */
function PipelinePreview({ hero }: { hero: boolean }) {
  const steps = [
    ['clarify', 'asks what it needs to know'],
    ['approval', 'you grant permission'],
    ['fanout', 'specialists run in parallel'],
    ['join', 'typed handoffs validate'],
    ['verify', 'a council checks the result'],
    ['report', 'the answer, with its cost'],
  ]
  return (
    <div className="relative h-full overflow-hidden">
      {/* Confined to the upper half so the small type below sits on plain
          obsidian, where it keeps its contrast. */}
      {hero && <div className="iridescent" aria-hidden style={{ inset: '-34% -18% 34% -18%' }} />}
      <div className="relative h-full flex flex-col justify-center p-8 gap-10">
        <h2 className="vm-display text-[var(--color-ink)] rise">
          One goal in,<br />an agent team out.
        </h2>
        <div className="w-full max-w-sm">
          <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)] mb-3">
            what Start will build
          </p>
          <ol>
            {steps.map(([name, what], i) => (
              <li key={name} className="flex items-baseline gap-2.5 font-mono text-[11px] leading-[1.9]">
                <span className="text-[var(--color-ink-faint)] select-none">
                  {i === steps.length - 1 ? '└─' : '├─'}
                </span>
                <span className="text-[var(--color-ink)] w-[64px] shrink-0">{name}</span>
                <span className="text-[var(--color-ink-faint)] min-w-0">{what}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  )
}

/**
 * monopo's rotating badge: text set round a circle, turning slowly. Here it
 * says what the system is doing. It sits in a corner like the original and,
 * like it, is punctuation rather than a control. Bottom-right, because the
 * graph's zoom controls already own the bottom-left.
 */
function LiveBadge({ text }: { text: string }) {
  const word = `${text.toUpperCase()} · `
  return (
    <svg
      aria-hidden
      className="spin-badge max-lg:hidden fixed right-5 bottom-11 z-20 pointer-events-none text-[var(--color-ink-muted)]"
      width="68" height="68" viewBox="0 0 68 68"
    >
      <defs>
        <path id="vm-ring" d="M34 34 m-25 0 a25 25 0 1 1 50 0 a25 25 0 1 1 -50 0" />
      </defs>
      <text fontSize="6.4" fill="currentColor" fontFamily="var(--font-mono)">
        {/* ~3.8 units a glyph on a 157-unit ring: 42 characters closes the loop.
            textLength is ignored on a textPath, so the word is repeated enough
            to reach the seam and the overflow is simply not drawn. */}
        <textPath href="#vm-ring">{word.repeat(Math.round(42 / word.length))}</textPath>
      </text>
    </svg>
  )
}

function NodeInspector({ graph, id, locked }: { graph: GraphSpec; id: string; locked: boolean }) {
  const node = graph.nodes.find((n) => n.id === id)
  if (!node) return null
  const incoming = graph.edges.filter((e) => e.to_node === id)

  return (
    <div className="shrink-0 border-t border-[var(--color-line)] bg-[var(--color-surface-2)] p-3 text-[11px] max-h-[200px] overflow-auto">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="font-semibold text-[12px]">{node.id}</span>
        <Badge>{node.type}</Badge>
        {locked && <Badge tone="warn">locked</Badge>}
        {node.agent?.allowed_side_effects.length ? (
          <Badge tone="warn">side effects</Badge>
        ) : (
          <Badge tone="good">read-only</Badge>
        )}
      </div>

      {node.agent ? (
        <dl className="grid grid-cols-[104px_1fr] gap-y-1 text-[var(--color-ink-muted)]">
          <dt className="text-[var(--color-ink-faint)]">model</dt>
          <dd className="font-mono">{node.agent.model}</dd>
          <dt className="text-[var(--color-ink-faint)]">fallback</dt>
          <dd className="font-mono">{node.agent.fallback_model ?? '—'}</dd>
          <dt className="text-[var(--color-ink-faint)]">tools</dt>
          <dd className="font-mono">{node.agent.tools.join(', ') || 'none declared'}</dd>
          <dt className="text-[var(--color-ink-faint)]">budget</dt>
          <dd className="font-mono">
            {node.agent.budget_tokens} tokens · {node.agent.timeout_s}s
          </dd>
          <dt className="text-[var(--color-ink-faint)]">contract</dt>
          <dd className="leading-relaxed">{node.agent.system_contract}</dd>
        </dl>
      ) : (
        <p className="text-[var(--color-ink-faint)]">Control node — carries no model call.</p>
      )}

      {incoming.length > 0 && (
        <div className="mt-2 pt-2 border-t border-[var(--color-line)]">
          <div className="text-[var(--color-ink-faint)] mb-1">Incoming handoffs</div>
          {incoming.map((e, i) => (
            <div key={i} className="font-mono text-[10px] text-[var(--color-ink-muted)]">
              {e.from_node} → {e.to_node}{' '}
              {Object.keys(e.handoff_schema ?? {}).length ? (
                <span className="text-[var(--color-done)]">schema enforced</span>
              ) : (
                <span className="text-[var(--color-ink-faint)]">control edge</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The plan, with the decision attached. This is where a coding agent asks "go
 * ahead?": a summary of what is about to happen, then the button. The
 * fitness prediction lives here because this is the only point it can inform
 * anything - once a run starts the graph is already approved and execution
 * begins immediately (see backend/api/routes.py's start_run()), so a
 * prediction logged only there would arrive too late to matter.
 */
function PlanCard({ graph, prediction, locked, busy, onRun, onLock }: {
  graph: GraphSpec
  prediction: DnaPredictedPayload | null
  locked: boolean
  busy: string | null
  onRun: () => void
  onLock: () => void
}) {
  const agents = graph.nodes.filter((n) => n.agent).length
  const parallel = Math.max(
    0,
    ...graph.nodes.filter((n) => n.type === 'fanout')
      .map((f) => graph.edges.filter((e) => e.from_node === f.id).length),
  )
  const gates = graph.nodes.filter((n) => n.type === 'approval').length
  const effects = [...new Set(graph.nodes.flatMap((n) => n.agent?.allowed_side_effects ?? []))]
  const mut = prediction?.mutation_suggestion
  return (
    <div className="shrink-0 m-2.5 mb-0 rounded-[12px] border border-[var(--color-line-strong)] bg-[var(--color-surface-1)]/60 p-3">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-0 flex-1 basis-[240px]">
          <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
            plan · {graph.config_hash.slice(0, 8)}
          </p>
          <p className="mt-1 text-[13px] leading-snug text-[var(--color-ink)]">
            {agents} agents
            {parallel > 1 && ` · ${parallel} in parallel`}
            {gates > 0 && ` · ${gates} approval gate${gates > 1 ? 's' : ''}`}
            {' · '}
            {effects.length
              ? <span className="text-[var(--color-warn)]">can {effects.join(', ')}</span>
              : <span className="text-[var(--color-done)]">read-only</span>}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button size="sm" onClick={onLock} title={locked ? 'Unlock the graph' : 'Freeze this graph'}>
            {locked ? '🔒 Locked' : 'Lock'}
          </Button>
          <Button
            size="sm" variant="primary" onClick={onRun} loading={busy === 'run'} disabled={!!busy}
            title="Approve and run the graph (⌘↵)"
          >
            Approve &amp; run
            <kbd className="font-mono text-[10px] opacity-60 ml-0.5">⌘↵</kbd>
          </Button>
        </div>
      </div>
      {prediction && (
        <p className="mt-2.5 pt-2.5 border-t border-[var(--color-line)] flex items-center gap-2 flex-wrap text-[11px] text-[var(--color-ink-faint)]">
          <span className="uppercase tracking-[0.07em]">predicted fitness</span>
          <Badge tone={fitnessTone(prediction.predicted_fitness)}>{prediction.predicted_fitness}</Badge>
          <span>
            {prediction.estimate_source === 'history'
              ? `from ${prediction.similar_workflow_count} similar past runs`
              : 'cold start - no similar history yet'}
          </span>
          {mut && (
            <span className="text-[var(--color-warn)]">
              · the {mut.mutation_type.replace(/_/g, ' ')} variant predicts{' '}
              <b className="font-semibold">{mut.predicted_fitness}</b> - this graph runs as approved
              either way, Workflow DNA never rewrites it for you
            </span>
          )}
        </p>
      )}
    </div>
  )
}

function CouncilStrip({ council }: { council: CouncilSummary }) {
  return (
    <div className="shrink-0 border-t border-[var(--color-line)] px-3 py-2 text-[10px] flex items-center gap-2 flex-wrap">
      <span className="text-[var(--color-ink-faint)] uppercase tracking-[0.07em]">Council</span>
      <Badge tone="info">chairman {council.chairman.split(':').pop()}</Badge>
      <Badge>winner {council.winner_label}</Badge>
      <Badge tone={council.escalated ? 'bad' : 'good'}>
        disagreement {council.disagreement}
      </Badge>
      <span className="text-[var(--color-ink-faint)] font-mono">
        {council.proposals.length} proposals · {council.rankings.length} anonymous rankings
      </span>
    </div>
  )
}
