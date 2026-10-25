import { useEffect, useState } from 'react'
import { api, type Priority, type ProviderStatus, type RouteDecision } from './api'
import { Badge, Button, Panel, SkeletonRows } from './ui'

const INPUT =
  'w-full min-h-10 px-3 rounded-[10px] bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[13px] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)] outline-none transition-colors'

const PRIORITIES: Priority[] = ['balanced', 'quality', 'speed', 'cost']

export function ProvidersPanel({ onChange }: { onChange?: () => void }) {
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null)
  const [adminRequired, setAdminRequired] = useState(false)
  const [admin, setAdmin] = useState('') // component state only, never persisted
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.providers()
      .then((r) => { setProviders(r.providers); setAdminRequired(r.admin_required) })
      .catch((e) => setError((e as Error).message))
  }, [])

  const apply = (r: { providers: ProviderStatus[] }) => { setProviders(r.providers); onChange?.() }

  return (
    <div className="h-full overflow-auto p-3">
      <div className="mx-auto max-w-4xl space-y-3">
        {error && <p role="alert" className="text-[12px] text-[var(--color-failed)]">{error}</p>}
        {adminRequired && (
          <Panel title="Admin token" subtitle="Required to change provider configuration. Kept in memory only.">
            <div className="p-3">
              <input
                type="password" value={admin} onChange={(e) => setAdmin(e.target.value)}
                autoComplete="off" aria-label="Admin token" placeholder="X-Admin-Token" className={INPUT}
              />
            </div>
          </Panel>
        )}
        {!providers ? <SkeletonRows n={4} /> : providers.map((p) => (
          <ProviderCard key={p.provider} p={p} admin={admin} onApply={apply} />
        ))}
        <RouterExplain models={providers?.flatMap((p) => p.models) ?? []} />
      </div>
    </div>
  )
}

function ProviderCard({ p, admin, onApply }: {
  p: ProviderStatus; admin: string; onApply: (r: { providers: ProviderStatus[] }) => void
}) {
  const [key, setKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(p.base_url ?? '')
  const [picked, setPicked] = useState<string[]>(p.models)
  const [busy, setBusy] = useState<'save' | 'test' | 'rm' | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const isOllama = p.provider === 'ollama'
  const chips = [...new Set([...p.models, ...p.suggested_models])]

  const toggle = (m: string) =>
    setPicked((c) => (c.includes(m) ? c.filter((x) => x !== m) : [...c, m]))

  const run = async (kind: 'save' | 'test' | 'rm', fn: () => Promise<void>) => {
    setBusy(kind); setMsg(null)
    try { await fn() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(null) }
  }

  const save = () => run('save', async () => {
    const body: { api_key?: string; base_url?: string; models?: string[] } = { models: picked }
    if (key.trim()) body.api_key = key.trim()
    if (isOllama && baseUrl.trim()) body.base_url = baseUrl.trim()
    onApply(await api.saveProvider(p.provider, body, admin || undefined))
    setKey('')
    setMsg({ ok: true, text: 'Saved' })
  })

  const test = () => run('test', async () => {
    const r = await api.testProvider(p.provider, picked[0] ?? p.models[0])
    setMsg(r.ok
      ? { ok: true, text: `OK · ${r.latency_ms ?? '?'}ms${r.tokens ? ` · ${r.tokens} tokens` : ''}` }
      : { ok: false, text: r.error ?? 'Test failed' })
  })

  const remove = () => run('rm', async () => {
    onApply(await api.removeProvider(p.provider, admin || undefined))
    setKey('')
  })

  return (
    <section className="rounded-[var(--radius-panel)] border border-[var(--color-line)] bg-[var(--color-surface-1)]/80 p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="text-[14px] font-semibold">{p.label}</h3>
        <Badge tone={p.configured ? 'good' : 'default'}>
          {p.configured ? `configured${p.key_source ? ` · ${p.key_source}` : ''}` : 'not configured'}
        </Badge>
        {p.active_specs.length > 0 && <Badge tone="info">{p.active_specs.length} active</Badge>}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={`${p.label} models`}>
          {chips.map((m) => {
            const on = picked.includes(m)
            return (
              <button
                key={m} onClick={() => toggle(m)} aria-pressed={on}
                className={`px-2 h-7 rounded-md text-[11px] font-mono border transition-colors ${
                  on
                    ? 'bg-[#5eead41f] text-[var(--color-accent)] border-[var(--color-accent)]'
                    : 'text-[var(--color-ink-faint)] border-[var(--color-line)] hover:text-[var(--color-ink-muted)]'
                }`}
              >
                {m}
              </button>
            )
          })}
        </div>
      )}

      <div className={`grid gap-2 ${isOllama ? 'sm:grid-cols-2' : ''}`}>
        {(p.key_required || !isOllama) && (
          <input
            type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off"
            aria-label={`${p.label} API key`} className={INPUT}
            placeholder={p.configured ? 'Replace API key' : p.key_required ? 'API key' : 'API key (optional)'}
          />
        )}
        {isOllama && (
          <input
            value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            aria-label="Base URL" placeholder="http://localhost:11434" className={INPUT}
          />
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <Button variant="primary" onClick={save} loading={busy === 'save'} disabled={!!busy}>Save</Button>
        <Button onClick={test} loading={busy === 'test'} disabled={!!busy || !p.configured}>
          Test connection
        </Button>
        <Button variant="danger" onClick={remove} loading={busy === 'rm'} disabled={!!busy || !p.configured}>
          Disconnect
        </Button>
        {msg && (
          <span
            role="status"
            className={`text-[12px] break-words ${msg.ok ? 'text-[var(--color-done)]' : 'text-[var(--color-failed)]'}`}
          >
            {msg.text}
          </span>
        )}
      </div>
    </section>
  )
}

function RouterExplain({ models }: { models: string[] }) {
  const [message, setMessage] = useState('')
  const [priority, setPriority] = useState<Priority>('balanced')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [res, setRes] = useState<{ decision: RouteDecision; intent: string } | null>(null)

  const explain = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.explainRoute(message, priority, models.length ? models : undefined)
      setRes({ decision: r.decision, intent: r.plan.intent })
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const d = res?.decision
  return (
    <Panel title="Model router" subtitle="Why a message goes to a given model">
      <div className="p-3 space-y-3">
        <form
          className="flex items-center gap-2 flex-wrap"
          onSubmit={(e) => { e.preventDefault(); if (message.trim()) explain() }}
        >
          <input
            value={message} onChange={(e) => setMessage(e.target.value)} aria-label="Message to route"
            placeholder="Type a message to route" className={`${INPUT} flex-1 min-w-[220px]`}
          />
          <select
            value={priority} onChange={(e) => setPriority(e.target.value as Priority)}
            aria-label="Priority" className={`${INPUT} !w-auto`}
          >
            {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <Button type="submit" variant="primary" loading={busy} disabled={!message.trim()}>Explain</Button>
        </form>
        {error && <p role="alert" className="text-[12px] text-[var(--color-failed)]">{error}</p>}
        {d && (
          <div className="space-y-2 text-[12px]">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone="accent">{d.model}</Badge>
              <Badge>{res!.intent}</Badge>
              <Badge>{d.complexity}</Badge>
              <Badge tone="info">{d.priority}</Badge>
            </div>
            <p className="text-[var(--color-ink-muted)] leading-relaxed">{d.reason}</p>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] tabular-nums">
                <thead>
                  <tr className="text-left text-[var(--color-ink-faint)]">
                    <th className="py-1 pr-3 font-medium">model</th>
                    <th className="pr-3 font-medium">capability</th>
                    <th className="pr-3 font-medium">cost</th>
                    <th className="pr-3 font-medium">latency</th>
                    <th className="font-medium">score</th>
                  </tr>
                </thead>
                <tbody>
                  {d.candidates.map((c) => (
                    <tr
                      key={c.model}
                      className={`border-t border-[var(--color-line)] ${c.model === d.model ? 'text-[var(--color-accent)]' : 'text-[var(--color-ink-muted)]'}`}
                    >
                      <td className="py-1.5 pr-3 font-mono">{c.model}{c.estimated && ' *'}</td>
                      <td className="pr-3">{c.capability.toFixed(2)}</td>
                      <td className="pr-3" title={`$${c.cost_per_mtok_usd}/Mtok`}>{c.cost.toFixed(2)}</td>
                      <td className="pr-3" title={`~${c.typical_latency_ms}ms`}>{c.latency.toFixed(2)}</td>
                      <td className="min-w-[96px]">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 flex-1 rounded-full bg-[var(--color-surface-3)]">
                            <div
                              className="h-full rounded-full bg-[var(--color-accent)]"
                              style={{ width: `${Math.max(0, Math.min(1, c.score)) * 100}%` }}
                            />
                          </div>
                          {c.score.toFixed(2)}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-[10px] text-[var(--color-ink-faint)]">* estimated profile</p>
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}
