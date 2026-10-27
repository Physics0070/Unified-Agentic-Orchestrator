/**
 * The frame around every view: a sidebar that can be put away, a command
 * palette, and toasts. None of it knows what the views do - they hand it
 * lists and callbacks - which is why a new view costs one entry in each list.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ago, type RunRecord } from './store'
import { Icon, type IconName } from './ui'

export type View = 'chat' | 'orchestrator' | 'artifacts' | 'usage' | 'github' | 'customize' | 'settings'

export const VIEW_TITLES: Record<View, string> = {
  chat: 'Chat', orchestrator: 'Orchestrator', artifacts: 'Artifacts', usage: 'Usage',
  github: 'GitHub', customize: 'Customize', settings: 'Settings',
}

/* ------------------------------------------------------------------ sidebar */

function NavButton({ icon, label, active, open, badge, kbd, onClick }: {
  icon: IconName; label: string; active?: boolean; open: boolean
  badge?: string | number; kbd?: string; onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      title={open ? undefined : label}
      aria-current={active ? 'page' : undefined}
      className={`relative flex items-center gap-3 w-full h-9 px-[19px] text-left whitespace-nowrap ${
        active ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]'
      }`}
    >
      {active && <span aria-hidden className="absolute left-0 top-1.5 bottom-1.5 w-[2px] bg-[var(--color-ink)]" />}
      <Icon name={icon} size={18} />
      <span className={`text-[12px] uppercase tracking-[0.08em] flex-1 transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0'}`}>
        {label}
      </span>
      {open && badge != null && (
        <span className="font-mono text-[10px] text-[var(--color-ink-faint)] tabular-nums max-w-[80px] truncate">{badge}</span>
      )}
      {open && kbd && <kbd className="font-mono text-[10px] text-[var(--color-ink-faint)] opacity-70">{kbd}</kbd>}
    </button>
  )
}

function Section({ title, open, children }: { title: string; open: boolean; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      {open
        ? <p className="px-5 mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">{title}</p>
        : <div className="mx-4 mb-1 h-px bg-[var(--color-line)]" />}
      {children}
    </div>
  )
}

export function Sidebar({ open, onToggle, view, onView, onNewRun, runs, onOpenRun, counts, repo, backend }: {
  open: boolean
  onToggle: () => void
  view: View
  onView: (v: View) => void
  onNewRun: () => void
  runs: RunRecord[]
  onOpenRun: (r: RunRecord) => void
  counts: { artifacts: number; runs: number }
  repo: string | null
  backend: { ok: boolean | null; ms: number | null }
}) {
  const dot = backend.ok == null ? 'var(--color-pending)' : backend.ok ? 'var(--color-done)' : 'var(--color-failed)'
  const dotLabel = backend.ok == null ? 'checking api…' : backend.ok ? `api ${backend.ms}ms` : 'api offline'
  return (
    <aside
      aria-label="Sidebar"
      className={`shrink-0 flex flex-col overflow-hidden border-r border-[var(--color-line)] bg-[var(--color-surface-0)]/70 transition-[width] duration-500 ease-[cubic-bezier(0.19,1,0.22,1)] ${open ? 'w-[248px]' : 'w-[56px]'}`}
    >
      <div className="flex items-center h-[58px] px-[14px] gap-3 shrink-0">
        <a href="#" title="Back to the overview" className="grid place-items-center h-7 w-7 shrink-0 bg-[var(--color-accent)] text-[var(--color-accent-on)] text-[13px]">⌘</a>
        {open && (
          <div className="min-w-0 flex-1 leading-tight whitespace-nowrap">
            <p className="text-[13px]">Agent Council</p>
          </div>
        )}
        {open && (
          <button onClick={onToggle} title="Hide sidebar (⌘B)" aria-label="Hide sidebar" className="text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] p-1">
            <Icon name="panel" size={16} />
          </button>
        )}
      </div>

      {!open && (
        <button onClick={onToggle} title="Show sidebar (⌘B)" aria-label="Show sidebar"
          className="grid place-items-center h-9 text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]">
          <Icon name="panel" size={18} />
        </button>
      )}

      <div className="px-3 pt-1 shrink-0">
        <button
          onClick={onNewRun}
          title="New run"
          className={`flex items-center justify-center gap-2 h-9 w-full rounded-full border border-[var(--color-line-strong)] hover:border-[var(--color-ink)] text-[11px] uppercase tracking-[0.07em] whitespace-nowrap`}
        >
          <Icon name="plus" size={14} />
          {open && 'New run'}
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden pb-2">
        <Section title="Workspace" open={open}>
          <NavButton icon="graph" label="Orchestrator" open={open} active={view === 'orchestrator'} onClick={() => onView('orchestrator')} kbd="⌘1" />
          <NavButton icon="chat" label="Chat" open={open} active={view === 'chat'} onClick={() => onView('chat')} kbd="⌘2" />
        </Section>
        <Section title="Library" open={open}>
          <NavButton icon="box" label="Artifacts" open={open} active={view === 'artifacts'} badge={counts.artifacts || undefined} onClick={() => onView('artifacts')} />
          <NavButton icon="chart" label="Usage" open={open} active={view === 'usage'} badge={counts.runs || undefined} onClick={() => onView('usage')} />
        </Section>
        <Section title="Connect" open={open}>
          <NavButton icon="merge" label="GitHub" open={open} active={view === 'github'} badge={repo?.split('/')[1]} onClick={() => onView('github')} />
        </Section>

        {open && runs.length > 0 && (
          <Section title="Recent runs" open>
            {runs.slice(0, 6).map((r) => (
              <button
                key={r.id}
                onClick={() => onOpenRun(r)}
                disabled={!r.events}
                title={r.events ? r.goal : 'Summary only: the details of this run were trimmed to save space'}
                className="flex items-baseline gap-2 w-full px-5 py-1.5 text-left disabled:opacity-40 hover:bg-[var(--color-surface-2)]"
              >
                <span aria-label={r.ok ? 'passed' : 'failed'} className="text-[11px] w-3 shrink-0"
                  style={{ color: r.ok ? 'var(--color-done)' : 'var(--color-failed)' }}>
                  {r.ok ? '✓' : '✕'}
                </span>
                <span className="text-[12px] truncate flex-1 text-[var(--color-ink-muted)]">{r.goal}</span>
                <span className="font-mono text-[10px] text-[var(--color-ink-faint)] shrink-0">{ago(r.ts)}</span>
              </button>
            ))}
          </Section>
        )}
      </div>

      <div className="shrink-0 border-t border-[var(--color-line)] py-1.5">
        <NavButton icon="sliders" label="Customize" open={open} active={view === 'customize'} onClick={() => onView('customize')} />
        <NavButton icon="gear" label="Settings" open={open} active={view === 'settings'} onClick={() => onView('settings')} />
        <div className="flex items-center gap-3 h-8 px-[22px] font-mono text-[10px] text-[var(--color-ink-faint)] whitespace-nowrap" title={dotLabel}>
          <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ background: dot }} />
          {open && dotLabel}
        </div>
      </div>
    </aside>
  )
}

/* ----------------------------------------------------------- command palette */

export interface Command {
  id: string
  label: string
  group: string
  hint?: string
  keywords?: string
  run: () => void
}

export function CommandPalette({ open, onClose, commands }: {
  open: boolean; onClose: () => void; commands: Command[]
}) {
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (open) { setQ(''); setIdx(0); setTimeout(() => inputRef.current?.focus(), 0) } }, [open])

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    return commands.filter((c) => {
      const hay = `${c.label} ${c.group} ${c.keywords ?? ''}`.toLowerCase()
      return words.every((w) => hay.includes(w))
    })
  }, [commands, q])

  useEffect(() => { setIdx(0) }, [q])
  // Keep the highlighted row in view when arrowing through a long list.
  useEffect(() => {
    document.getElementById(`cmd-${idx}`)?.scrollIntoView({ block: 'nearest' })
  }, [idx])

  if (!open) return null

  const fire = (c: Command | undefined) => { if (c) { onClose(); c.run() } }

  return (
    <div className="fixed inset-0 z-50 flex justify-center pt-[14vh] bg-black/60" onMouseDown={onClose}>
      <div
        role="dialog" aria-modal="true" aria-label="Command palette"
        onMouseDown={(e) => e.stopPropagation()}
        className="w-[min(580px,92vw)] max-h-[60vh] flex flex-col border border-[var(--color-line-strong)] bg-[var(--color-surface-1)]"
      >
        <div className="flex items-center gap-3 px-4 h-12 border-b border-[var(--color-line)] shrink-0">
          <Icon name="search" size={16} className="text-[var(--color-ink-faint)]" />
          <input
            ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Type a command…" aria-label="Command"
            role="combobox" aria-expanded="true" aria-controls="cmd-list"
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(shown.length - 1, i + 1)) }
              else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)) }
              else if (e.key === 'Enter') { e.preventDefault(); fire(shown[idx]) }
              else if (e.key === 'Escape') { e.preventDefault(); onClose() }
            }}
            className="flex-1 min-w-0 bg-transparent outline-none text-[14px] placeholder:text-[var(--color-ink-faint)]"
          />
          <kbd className="font-mono text-[10px] text-[var(--color-ink-faint)]">esc</kbd>
        </div>
        <div id="cmd-list" role="listbox" className="overflow-y-auto py-1">
          {shown.length === 0 && <p className="px-4 py-6 text-[12px] text-[var(--color-ink-faint)]">No command matches “{q}”.</p>}
          {shown.map((c, i) => (
            <button
              key={c.id} id={`cmd-${i}`} role="option" aria-selected={i === idx}
              onMouseMove={() => setIdx(i)} onClick={() => fire(c)}
              className={`flex items-baseline gap-3 w-full px-4 py-2 text-left ${i === idx ? 'bg-[var(--color-surface-4)]' : ''}`}
            >
              <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-ink-faint)] w-[78px] shrink-0">{c.group}</span>
              <span className="text-[13px] flex-1 min-w-0 truncate">{c.label}</span>
              {c.hint && <kbd className="font-mono text-[10px] text-[var(--color-ink-faint)]">{c.hint}</kbd>}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------- toasts */

export interface Toast { id: number; text: string; tone?: 'ok' | 'warn' }

export function Toasts({ items }: { items: Toast[] }) {
  return (
    <div role="status" aria-live="polite" className="fixed left-1/2 -translate-x-1/2 bottom-12 z-40 flex flex-col items-center gap-2 pointer-events-none">
      {items.map((t) => (
        <div key={t.id} className="rise flex items-center gap-2.5 px-4 py-2 border border-[var(--color-line-strong)] bg-[var(--color-surface-1)] text-[12px]">
          <span aria-hidden style={{ color: t.tone === 'warn' ? 'var(--color-warn)' : 'var(--color-done)' }}>
            {t.tone === 'warn' ? '!' : '✓'}
          </span>
          {t.text}
        </div>
      ))}
    </div>
  )
}
