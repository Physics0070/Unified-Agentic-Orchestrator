export type EventType =
  | 'RUN_START' | 'CLARIFY_ASKED' | 'CLARIFY_ANSWERED' | 'GRAPH_PROPOSED'
  | 'GRAPH_EDITED' | 'GRAPH_LOCKED' | 'GRAPH_APPROVED' | 'NODE_START' | 'NODE_END'
  | 'HANDOFF_EMITTED' | 'HANDOFF_VALIDATED' | 'HANDOFF_REJECTED' | 'REPAIR_ATTEMPT'
  | 'TOOL_CALL' | 'TOOL_RESULT' | 'TOOL_BLOCKED' | 'INJECTION_BLOCKED'
  | 'BUDGET_EXCEEDED' | 'RETRY' | 'COMPENSATE' | 'COUNCIL_PROPOSAL'
  | 'COUNCIL_RANKING' | 'COUNCIL_VERDICT' | 'APPROVAL_REQUESTED' | 'APPROVAL_GRANTED'
  | 'PAUSE' | 'RESUME' | 'CANCEL' | 'BRANCH_FAILED' | 'RUN_END'
  | 'MEMORY_WRITE' | 'MEMORY_READ' | 'MEMORY_EVICTED'
  | 'DNA_PREDICTED' | 'DNA_RECORDED'

export interface LogEvent {
  run_id: string
  seq: number
  event_type: EventType
  node_id: string | null
  agent_id: string | null
  payload: Record<string, unknown> | null
  tokens_in: number
  tokens_out: number
  cost_usd: number
  latency_ms: number | null
  ts: string | null
}

export interface AgentSpec {
  id: string; role: string; system_contract: string
  tools: string[]; model: string; fallback_model: string | null
  budget_tokens: number; timeout_s: number; allowed_side_effects: string[]
  memory_ttl_s?: number | null
}

export interface GraphNode {
  id: string
  type: 'clarify' | 'agent' | 'fanout' | 'join' | 'conditional' | 'verify'
      | 'council' | 'approval' | 'compensate' | 'subgraph'
  agent: AgentSpec | null
  compensates?: string | null
  max_retries: number
  subgraph?: GraphSpec | null
}

export interface GraphEdge { from_node: string; to_node: string; handoff_schema: Record<string, unknown> }
export interface GraphSpec {
  version: number; config_hash: string
  nodes: GraphNode[]; edges: GraphEdge[]; locked: boolean
}

export interface AgentMetric {
  agent_id: string; node_id: string
  marginal_information_gain: number
  tokens: number; cost_usd: number; findings: number
}

export interface RunMetrics {
  run_id: string
  agents: AgentMetric[]
  redundancy_index: number
  duplicate_pairs: { a: string; b: string; cosine: number }[]
  duplicate_work: number
  parallel_efficiency: number
  recovery_rate: number | null
  handoff_validity: number | null
  branch_utilization: number
  approval_frequency: number
  total_cost_usd: number
  total_tokens: number
  unique_findings: number
  cost_per_unique_finding: number | null
  tokens_per_unique_finding: number | null
  least_valuable_agent: string | null
  verdict: string
}

export interface ReplayDiff {
  identical: boolean
  output_diffs: { node: string; original: unknown; replay: unknown }[]
  original_cost_usd: number; replay_cost_usd: number
  original_wall_ms: number; replay_wall_ms: number
  original_tokens: number; replay_tokens: number
  nodes_compared: number
}

// ------------------------------------------------------------------ Workflow DNA

/** Payload shape of a DNA_PREDICTED event (backend/workflow_dna/orchestrator_hooks.py). */
export interface DnaPredictedPayload {
  predicted_fitness: number
  estimate_source: 'history' | 'cold_start'
  similar_workflow_count: number
  threshold_met_without_mutation: boolean
  mutation_suggestion?: {
    mutation_type: string
    predicted_fitness: number
    note: string
  }
}

/** Payload shape of a DNA_RECORDED event. */
export interface DnaRecordedPayload {
  fitness: number
  quality: number
  predicted_fitness: number | null
}

/** GET /api/runs/{run_id}/output — the run's actual deliverable, read back
 * from the artifact store for whichever node(s) nothing else in the graph
 * depends on (a report/verify/merge node, typically). */
export interface RunOutput {
  run_id: string
  status: string
  sink_node_ids: string[]
  outputs: { node_id: string | null; written_by: string; content: unknown }[]
}

/** One row from Workflow DNA's history db — GET /api/workflow-dna/history. */
export interface WorkflowDnaExecution {
  workflow_id: string
  generation: number
  parent_workflow: string | null
  mutation_type: string
  agent_count: number
  parallel_execution: number
  workflow_depth: number
  workflow_length: number
  execution_time: number | null
  retry_count: number | null
  cost: number | null
  quality: number | null
  success: number | null
  fitness: number | null
  predicted_fitness: number | null
  timestamp: string
}

export interface DepthPoint {
  depth: number; agents: string[]; unique_findings: number
  total_cost_usd: number; total_tokens: number; wall_clock_ms: number
  redundancy_index: number; duplicate_work: number; parallel_efficiency: number
  lowest_mig: number; lowest_mig_agent: string
  quality_per_1k_tokens: number; quality_per_rupee: number | null
}

export interface MarginalValueReport {
  goal: string; points: DepthPoint[]; best_depth: number
  recommendation: string; cost_basis: string
}

/**
 * Empty by default: FastAPI serves this bundle from the same origin, so relative
 * paths just work. Set VITE_API_BASE when the frontend is hosted separately
 * (Vercel/Netlify static + the backend on a container host) — the backend cannot
 * run on a serverless platform, since SSE and multi-minute runs outlive a
 * function invocation.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

const url = (path: string) => `${API_BASE}${path}`

// ------------------------------------------------------------------ accounts

export interface AuthUser { id: string; email: string }
export interface AuthState { user: AuthUser | null; auth_required: boolean }

/** Fired when the backend answers 401 "sign in required" (AUTH_REQUIRED is on and
 * there is no session). App listens and opens the sign-in dialog. */
export const AUTH_REQUIRED_EVENT = 'aco:auth-required'

function flagAuthRequired(status: number, detail: string) {
  if (status === 401 && detail.includes('sign in required')) {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url(path), {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!r.ok) {
    const body = await r.text()
    let detail = body.slice(0, 300)
    try {
      const parsed = JSON.parse(body)
      detail = parsed.message ?? parsed.detail ?? detail
      if (typeof detail === 'object') detail = JSON.stringify(detail)
    } catch {
      /* body was not JSON; the raw slice is the best we have */
    }
    flagAuthRequired(r.status, String(detail))
    throw new Error(`${r.status} · ${detail}`)
  }
  return r.json() as Promise<T>
}

export interface ModelProfile {
  reasoning_logic: number; task_proficiency: number; context_relation: number
  agentic_capability: number; factuality_safety: number; real_world_data: number
}

// ------------------------------------------------------------------ providers / knowledge / router / trace

export type Priority = 'balanced' | 'quality' | 'speed' | 'cost'

export interface ProviderStatus {
  provider: string; label: string; configured: boolean
  key_source: 'runtime' | 'env' | null
  base_url: string | null; key_required: boolean
  models: string[]; default_models: string[]; suggested_models: string[]; active_specs: string[]
}

export interface KnowledgeDoc { doc_id: string; name: string; chars: number; chunks: number; added_at: string }

export interface KnowledgeSummary {
  workspace_id: string; documents: KnowledgeDoc[]; chunks: number; persistent: boolean
  semantic_backend: 'embeddings' | 'char-ngram-tfidf'; supported: string[]; max_bytes: number
}

export interface KnowledgeHit {
  doc_id: string; doc_name: string; ordinal: number; text: string; score: number
  scores: { bm25: number | null; semantic: number | null; fused: number; rerank: number | null }
  ranks: { bm25: number | null; semantic: number | null }
}

export interface KnowledgeQueryResult {
  query: string; mode: string; reranked: boolean; semantic_backend: string | null
  documents: number; chunks: number; hits: KnowledgeHit[]
}

export interface RouteDecision {
  model: string; reason: string; priority: Priority | string; complexity: string; intent: string
  weights: { capability: number; cost: number; latency: number }
  candidates: {
    model: string; capability: number; cost: number; latency: number; score: number
    cost_per_mtok_usd: number; typical_latency_ms: number; estimated: boolean
  }[]
}

export interface TraceStep {
  node_id: string; agent_id: string | null; type: string | null; role: string | null
  model: string | null
  status: 'pending' | 'running' | 'done' | 'failed' | 'blocked' | 'skipped'
  error: string | null; started_at: string | null; ended_at: string | null
  duration_ms: number | null; depends_on: string[]; tools_declared: string[]
  tool_calls: { tool: string | null; arguments: string | null }[]
  tools_blocked: (string | null)[]
  input: string | null; output: string | null
  tokens_in: number; tokens_out: number; tokens: number; cost_usd: number
  attempts: number; repairs: number; retries: number; used_fallback: boolean
}

export interface RunTrace {
  run_id: string; status: string; steps: TraceStep[]
  summary: {
    steps: number; by_status: Record<string, number>; tokens: number
    cost_usd: number; wall_clock_ms: number | null; finished: boolean
  }
}

const WS_KEY = 'aco.workspace'
let wsFallback = ''
/** Random per-browser id scoping uploaded knowledge; localStorage when available. */
export function getWorkspaceId(): string {
  try {
    let id = localStorage.getItem(WS_KEY)
    if (!id) { id = crypto.randomUUID().replace(/-/g, ''); localStorage.setItem(WS_KEY, id) }
    return id
  } catch {
    return wsFallback ||= crypto.randomUUID().replace(/-/g, '')
  }
}

const RUNS_KEY = 'aco.runIds'
/** Run ids this browser started (capability model: ids are unguessable UUIDs). */
export function getMyRunIds(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RUNS_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, 50) : []
  } catch { return [] }
}
function rememberRunId(id: string) {
  try {
    localStorage.setItem(RUNS_KEY, JSON.stringify([id, ...getMyRunIds().filter((x) => x !== id)].slice(0, 50)))
  } catch { /* storage unavailable: history simply isn't remembered */ }
}

const adminHeaders = (t?: string): Record<string, string> => (t ? { 'X-Admin-Token': t } : {})

async function upload(file: File): Promise<KnowledgeSummary & { document: KnowledgeDoc }> {
  const fd = new FormData()
  fd.append('workspace_id', getWorkspaceId())
  fd.append('file', file)
  const r = await fetch(url('/api/knowledge/upload'), {
    method: 'POST', body: fd, credentials: 'same-origin',
  })
  if (!r.ok) {
    let detail = `${r.status}`
    try { detail = (await r.json()).detail ?? detail } catch { /* non-JSON error body */ }
    flagAuthRequired(r.status, String(detail))
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return r.json()
}

export const api = {
  authMe: () => req<AuthState>('/api/auth/me'),
  register: (email: string, password: string) =>
    req<{ user: AuthUser }>('/api/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    req<{ user: AuthUser }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => req<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),

  models: () => req<{ models: string[]; providers: string[] }>('/api/models'),

  modelCatalog: () =>
    req<{ models: { model: string; matched_key: string; profile: ModelProfile }[] }>(
      '/api/models/catalog'),

  clarify: (goal: string) =>
    req<{
      questions: { id: string; text: string }[]
      permission: string
      plan: {
        intent: string; complexity: string; rationale: string; source: string
        specialists: { id: string; role: string }[]
      }
    }>('/api/clarify', { method: 'POST', body: JSON.stringify({ goal }) }),

  compile: (goal: string, answers: Record<string, string>, models: string[], intent?: string) =>
    req<{
      run_id: string; graph: GraphSpec; council: CouncilSummary | null
      dna_prediction: DnaPredictedPayload | null
    }>(
      '/api/compile',
      { method: 'POST', body: JSON.stringify({ goal, answers, models, intent }) }),

  /** Only this browser's own runs: GET /api/runs lists everyone's and is admin-only. */
  listMine: () => {
    const ids = getMyRunIds()
    return ids.length
      ? req<{ runs: unknown[] }>(`/api/runs?ids=${ids.join(',')}`)
      : Promise.resolve({ runs: [] })
  },

  start: (goal: string, graph: GraphSpec, approvals: string[]) =>
    req<{ run_id: string; config_hash: string; status?: string }>(
      '/api/runs', {
        method: 'POST',
        body: JSON.stringify({ goal, graph, approvals, workspace_id: getWorkspaceId() }),
      }).then((r) => { rememberRunId(r.run_id); return r }),

  /** Serverless hosts only create the run in start(); this runs it (status 'pending'). */
  execute: (runId: string, approvals: string[]) =>
    req<{ run_id: string; status: string }>(`/api/runs/${runId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ approvals, workspace_id: getWorkspaceId() }),
    }),

  events: (runId: string) => req<{ events: LogEvent[] }>(`/api/runs/${runId}/events`),
  output: (runId: string) => req<RunOutput>(`/api/runs/${runId}/output`),
  metrics: (runId: string) => req<RunMetrics>(`/api/runs/${runId}/metrics`),
  replay: (runId: string) =>
    req<{ replay_run_id: string; diff: ReplayDiff; cost_saved_usd: number }>(
      `/api/runs/${runId}/replay`, { method: 'POST' }),
  cancel: (runId: string) => req(`/api/runs/${runId}/cancel`, { method: 'POST' }),
  pause: (runId: string) => req(`/api/runs/${runId}/pause`, { method: 'POST' }),
  resume: (runId: string) => req(`/api/runs/${runId}/resume`, { method: 'POST' }),
  redAgent: () =>
    req<{ run_id: string; tool_blocked: boolean; injection_blocked: boolean; events: LogEvent[] }>(
      '/api/demo/red-agent', { method: 'POST' }),
  marginalValue: (goal: string, models: string[]) =>
    req<MarginalValueReport>('/api/marginal-value',
      { method: 'POST', body: JSON.stringify({ goal, models, depths: [1, 2, 3, 4] }) }),
  workflowDnaHistory: (limit = 20) =>
    req<{ executions: WorkflowDnaExecution[] }>(`/api/workflow-dna/history?limit=${limit}`),

  providers: () => req<{ providers: ProviderStatus[]; admin_required: boolean }>('/api/providers'),
  saveProvider: (
    p: string, body: { api_key?: string; base_url?: string; models?: string[] }, adminToken?: string,
  ) => req<{ providers: ProviderStatus[] }>(`/api/providers/${p}`,
    { method: 'PUT', body: JSON.stringify(body), headers: adminHeaders(adminToken) }),
  removeProvider: (p: string, adminToken?: string) =>
    req<{ providers: ProviderStatus[] }>(`/api/providers/${p}`,
      { method: 'DELETE', headers: adminHeaders(adminToken) }),
  testProvider: (p: string, model?: string) =>
    req<{ ok: boolean; provider: string; model?: string; latency_ms?: number; tokens?: number; error?: string }>(
      `/api/providers/${p}/test`, { method: 'POST', body: JSON.stringify({ model }) }),

  knowledgeDocs: () =>
    req<KnowledgeSummary>(`/api/knowledge/documents?workspace_id=${getWorkspaceId()}`),
  uploadDocument: upload,
  deleteDocument: (docId: string) =>
    req<KnowledgeSummary>(`/api/knowledge/documents/${docId}?workspace_id=${getWorkspaceId()}`,
      { method: 'DELETE' }),
  knowledgeQuery: (
    query: string,
    opts: { top_k?: number; mode?: 'hybrid' | 'keyword' | 'semantic'; rerank?: boolean } = {},
  ) => req<KnowledgeQueryResult>('/api/knowledge/query',
    { method: 'POST', body: JSON.stringify({ workspace_id: getWorkspaceId(), query, ...opts }) }),

  explainRoute: (message: string, priority?: Priority, models?: string[]) =>
    req<{
      plan: { intent: string; complexity: string; rationale: string; source: string }
      decision: RouteDecision
    }>('/api/router/explain', { method: 'POST', body: JSON.stringify({ message, priority, models }) }),

  trace: (runId: string) => req<RunTrace>(`/api/runs/${runId}/trace`),

  githubContext: (repo: string, token?: string) =>
    req<{ repo: string; text: string; truncated: boolean }>('/api/github/context',
      { method: 'POST', body: JSON.stringify({ repo, token: token || null }) }),
}

// ------------------------------------------------------------------ chat

export interface ChatMessage { role: 'user' | 'assistant'; content: string }

export interface PlanInfo {
  intent: string
  complexity: 'simple' | 'moderate' | 'deep'
  rationale: string
  source: string
  route: 'fast' | 'council'
  clarifying_questions: string[]
  specialists: { id: string; role: string }[]
  plan_ms: number
  model_choices: { id: string; role: string; model: string; reason: string }[]
  answer_model: string
  answer_model_reason: string
  // Nodes/edges only for a chat turn's graph - no config_hash/locked meaning
  // applies (never approved, never re-run in place, unlike an Orchestrator run).
  graph: { nodes: GraphNode[]; edges: GraphEdge[] }
  routing?: RouteDecision
}

export interface SpecialistInfo {
  id: string; points: number; model: string | null; reason: string | null
  latency_ms: number | null; tokens: number | null; error: string | null
}

export interface Benchmarks {
  run_id: string
  route: 'fast' | 'council'
  intent: string
  complexity: string
  planner_source: string
  models_used: string[]
  answer_model: string
  answer_model_reason: string
  timing: {
    plan_ms: number; fanout_ms: number
    first_token_ms: number | null; total_ms: number
  }
  specialists: {
    id: string; points: number; model: string | null; reason: string | null
    latency_ms: number | null; tokens: number | null; failed: boolean
  }[]
  parallel_efficiency: number | null
  tokens: number
  cost_usd: number
  answer_chars: number
  marginal_information_gain: {
    available: boolean
    per_agent: Record<string, number>
    overlapping_pairs: { a: string; b: string; similarity: number }[]
  }
  web_search: { used: boolean; sources: { title: string; url: string }[] }
  routing?: RouteDecision
  knowledge?: {
    used: boolean; semantic_backend: string | null
    hits: { doc_name: string; ordinal: number; score: number }[]
  }
  trace?: TraceStep[]
}

export interface ChatFile {
  file_id: string; filename: string
  kind: 'xlsx' | 'csv' | 'docx' | 'pdf' | 'pptx' | 'md' | 'txt'
}

export interface ChatHandlers {
  onPlan?: (p: PlanInfo) => void
  onStatus?: (stage: string) => void
  onSpecialist?: (s: SpecialistInfo) => void
  onToken?: (t: string) => void
  onBenchmarks?: (b: Benchmarks) => void
  onFile?: (f: ChatFile) => void
  onError?: (detail: string) => void
  /** Fires once when the stream closes, however it closed. */
  onDone?: () => void
}

/**
 * Streams a chat turn.
 *
 * `EventSource` is GET-only and the turn carries history in the body, so the
 * SSE frames are parsed off a fetch stream here instead. Returns an abort
 * function so a user can stop a long answer.
 */
export function streamChat(
  body: {
    message: string; history: ChatMessage[]; models?: string[]
    forcedModel?: string; forceWebSearch?: boolean
    priority?: string; workspaceId?: string; useKnowledge?: boolean
  },
  h: ChatHandlers,
): () => void {
  const ctrl = new AbortController()
  const { forcedModel, forceWebSearch, priority, workspaceId, useKnowledge, ...rest } = body

  ;(async () => {
    try {
      const r = await fetch(url('/api/chat'), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...rest, forced_model: forcedModel, force_web_search: !!forceWebSearch,
          priority, workspace_id: workspaceId, use_knowledge: useKnowledge,
        }),
        signal: ctrl.signal,
      })
      if (!r.ok || !r.body) {
        throw new Error(`${r.status} · ${(await r.text()).slice(0, 200)}`)
      }

      const reader = r.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      const dispatch = (frame: string) => {
        let name = 'message'
        const dataLines: string[] = []
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) name = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
        }
        if (!dataLines.length) return

        let data: unknown
        try { data = JSON.parse(dataLines.join('\n')) } catch { return }

        switch (name) {
          case 'plan': h.onPlan?.(data as PlanInfo); break
          case 'status': h.onStatus?.((data as { stage: string }).stage); break
          case 'specialist': h.onSpecialist?.(data as SpecialistInfo); break
          case 'token': h.onToken?.(data as string); break
          case 'benchmarks': h.onBenchmarks?.(data as Benchmarks); break
          case 'file': h.onFile?.(data as ChatFile); break
          case 'error': h.onError?.((data as { detail: string }).detail); break
        }
      }

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break

        // The server terminates SSE lines with CRLF, so splitting raw on '\n\n'
        // never matches - the \r sits between the two newlines and every frame
        // stays stuck in the buffer. Normalise first, then split. A chunk can
        // end mid-CRLF; the lone trailing \r is simply carried to the next pass.
        buf = (buf + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')

        // SSE frames are separated by a blank line. Keep the trailing partial
        // frame in the buffer until its terminator arrives.
        const frames = buf.split('\n\n')
        buf = frames.pop() ?? ''

        for (const frame of frames) dispatch(frame)
      }

      // A final frame may arrive without its blank-line terminator.
      buf = (buf + decoder.decode()).replace(/\r\n/g, '\n')
      if (buf.trim()) dispatch(buf)
      h.onDone?.()
    } catch (e) {
      if ((e as Error).name !== 'AbortError') h.onError?.((e as Error).message)
      h.onDone?.()
    }
  })()

  return () => ctrl.abort()
}

export interface CouncilSummary {
  chairman: string
  borda: Record<string, number>
  winner_label: string
  winner_author: string
  disagreement: number
  escalated: boolean
  proposals: { label: string; author_model: string; tokens: number }[]
  rankings: { ranker: string; order: string[] }[]
}

/** Live tail of the event log. One connection, server-side polling. */
export function streamEvents(
  runId: string,
  onEvent: (e: LogEvent) => void,
  onDone: () => void,
): () => void {
  const es = new EventSource(url(`/api/runs/${runId}/stream`))
  es.addEventListener('log', (m) => onEvent(JSON.parse((m as MessageEvent).data)))
  es.addEventListener('done', () => { es.close(); onDone() })
  es.onerror = () => { es.close(); onDone() }
  return () => es.close()
}
