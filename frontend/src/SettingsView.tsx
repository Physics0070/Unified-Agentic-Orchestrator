import { useState } from 'react'
import { API_BASE } from './api'
import { download, type Accent, type Palette, type Settings } from './store'
import { Badge, Button, Page } from './ui'

type Patch = (p: Partial<Settings>) => void

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 py-3.5 border-b border-[var(--color-line)]">
      <div className="min-w-0"><p className="text-[13px]">{label}</p>{hint && <p className="text-[11px] text-[var(--color-ink-faint)] mt-0.5">{hint}</p>}</div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className={`relative h-5 w-9 rounded-full border transition-colors ${on ? 'bg-[var(--color-ink)] border-[var(--color-ink)]' : 'border-[var(--color-line-strong)]'}`}>
      <span className={`absolute top-[2px] h-3.5 w-3.5 rounded-full transition-[left] duration-300 ${on ? 'left-[18px] bg-[var(--color-surface-0)]' : 'left-[2px] bg-[var(--color-ink-faint)]'}`} />
    </button>
  )
}

// Names only: each swatch is painted by the stylesheet's own data-accent /
// data-palette rule, so the preview can never drift from what is applied.
const ACCENTS: Accent[] = ['bone', 'iris', 'ember', 'signal', 'glacier']
const PALETTES: Palette[] = ['prism', 'aurora', 'ember', 'ice', 'mono', 'rainbow']
const cap = (s: string) => s[0].toUpperCase() + s.slice(1)

export function CustomizeView({ settings: s, patch }: { settings: Settings; patch: Patch }) {
  return (
    <Page title="Customize" sub="Changes apply instantly and are remembered in this browser.">
      <Row label="Accent" hint="Buttons, focus rings and highlights">
        <div className="flex gap-2" role="radiogroup" aria-label="Accent">
          {ACCENTS.map((a) => (
            <button key={a} role="radio" aria-checked={s.accent === a} title={cap(a)} aria-label={cap(a)} onClick={() => patch({ accent: a })}
              className={`h-7 w-7 rounded-full border-2 ${s.accent === a ? 'border-[var(--color-ink)]' : 'border-transparent'}`}>
              <span data-accent={a} className="block h-full w-full rounded-full border border-black/30 bg-[var(--color-accent)]" />
            </button>))}
        </div>
      </Row>
      <Row label="Particle palette" hint="Colours of the live background">
        <div className="flex gap-2 flex-wrap justify-end max-w-[340px]" role="radiogroup" aria-label="Particle palette">
          {PALETTES.map((p) => (
            <button key={p} role="radio" aria-checked={s.palette === p} onClick={() => patch({ palette: p })}
              className={`flex items-center gap-1.5 h-8 px-3 rounded-full border text-[11px] ${s.palette === p ? 'border-[var(--color-ink)]' : 'border-[var(--color-line)] text-[var(--color-ink-faint)]'}`}>
              <span data-palette={p} className="swatch-spectrum" aria-hidden />
              <span className="ml-1.5">{cap(p)}</span>
            </button>))}
        </div>
      </Row>
      <Row label="Live particles" hint="Turn the animated background off to save battery"><Switch on={s.particles} onChange={(v) => patch({ particles: v })} label="Live particles" /></Row>
      <Row label="Particle density" hint={`${s.density} marks`}>
        <input type="range" min={120} max={900} step={20} value={s.density} disabled={!s.particles} aria-label="Particle density"
          onChange={(e) => patch({ density: +e.target.value })} className="w-44 accent-[var(--color-ink)]" />
      </Row>
      <Row label="RGB split" hint="Each mark separates into red, green and blue"><Switch on={s.split} onChange={(v) => patch({ split: v })} label="RGB split" /></Row>
      <Row label="Iridescent hero" hint="The soft light behind the empty orchestrator"><Switch on={s.hero} onChange={(v) => patch({ hero: v })} label="Iridescent hero" /></Row>
      <Row label="Sidebar open" hint="Also toggled with ⌘B"><Switch on={s.sidebar} onChange={(v) => patch({ sidebar: v })} label="Sidebar open" /></Row>
    </Page>
  )
}

const SHORTCUTS: [string, string][] = [
  ['⌘K', 'Command palette'], ['⌘B', 'Hide or show the sidebar'], ['⌘1', 'Orchestrator'], ['⌘2', 'Chat'],
  ['/', 'Focus the goal prompt'], ['⌘↵', 'Run the current plan'], ['Esc', 'Dismiss an error'],
]

export function SettingsView({ settings: s, patch, reset, models, backend, onPing, runs, onClearRuns, toast }: {
  settings: Settings; patch: Patch; reset: () => void; models: string[]
  backend: { ok: boolean | null; ms: number | null }; onPing: () => Promise<void>
  runs: number; onClearRuns: () => void; toast: (t: string, tone?: 'ok' | 'warn') => void
}) {
  const [testing, setTesting] = useState(false)
  const enabled = (m: string) => !s.models || s.models.includes(m)
  const toggle = (m: string) => {
    const cur = s.models ?? models
    const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]
    patch({ models: next.length === models.length ? null : next })
  }
  const test = async () => { setTesting(true); await onPing(); setTesting(false) }

  const exportAll = () => {
    const dump: Record<string, unknown> = {}
    try { for (const k of Object.keys(localStorage)) if (k.startsWith('aco.')) dump[k] = JSON.parse(localStorage.getItem(k) ?? 'null') } catch { /* ignore */ }
    download('agent-council-export.json', JSON.stringify(dump, null, 2))
  }
  const clearChat = () => { try { localStorage.removeItem('aco.chat.conversations') } catch { /* ignore */ } toast('Chat history cleared. Reload to see it empty.') }

  return (
    <Page title="Settings" sub="Models, connection and your data. Everything is stored in this browser only.">
      <section className="mb-8">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-1">Backend</h3>
        <Row label="Connection" hint={API_BASE || 'same origin (/api)'}>
          <div className="flex items-center gap-3">
            <Badge tone={backend.ok == null ? 'default' : backend.ok ? 'good' : 'bad'}>{backend.ok == null ? 'checking' : backend.ok ? `online · ${backend.ms}ms` : 'offline'}</Badge>
            <Button size="sm" onClick={test} loading={testing}>Test</Button>
          </div>
        </Row>
      </section>

      <section className="mb-8">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-1">Models the council may use</h3>
        {models.length === 0 ? <p className="py-3 text-[12px] text-[var(--color-ink-faint)]">No models reported. Start the backend, then press Test.</p> : (
          <ul>{models.map((m) => (
            <li key={m}><label className="flex items-center gap-3 py-2.5 border-b border-[var(--color-line)] cursor-pointer">
              <input type="checkbox" checked={enabled(m)} onChange={() => toggle(m)} className="accent-[var(--color-ink)] h-4 w-4" />
              <span className="font-mono text-[12px]">{m}</span></label></li>))}</ul>)}
        <p className="mt-2 text-[11px] text-[var(--color-ink-faint)]">Applies to the next plan you compile and to Chat. At least one stays in use: with none ticked, all are used.</p>
      </section>

      <section className="mb-8">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-1">Your data</h3>
        <Row label="Export everything" hint="Runs, chats and settings as one JSON file"><Button size="sm" onClick={exportAll}>Export</Button></Row>
        <Row label="Clear run history" hint={`${runs} saved`}><Button size="sm" variant="danger" disabled={!runs} onClick={() => { onClearRuns(); toast('Run history cleared') }}>Clear</Button></Row>
        <Row label="Clear chat history"><Button size="sm" variant="danger" onClick={clearChat}>Clear</Button></Row>
        <Row label="Reset appearance and models"><Button size="sm" onClick={() => { reset(); toast('Settings reset') }}>Reset</Button></Row>
      </section>

      <section>
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-1">Keyboard</h3>
        <dl>{SHORTCUTS.map(([k, d]) => (
          <div key={k} className="flex items-center justify-between py-2 border-b border-[var(--color-line)] text-[12px]">
            <dt className="text-[var(--color-ink-muted)]">{d}</dt><dd><kbd className="font-mono text-[11px] border border-[var(--color-line-strong)] px-1.5 py-0.5">{k}</kbd></dd></div>))}</dl>
      </section>
    </Page>
  )
}
