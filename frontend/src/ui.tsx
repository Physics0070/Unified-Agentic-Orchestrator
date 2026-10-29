import type { ReactNode } from 'react'

/** Shared primitives. Every interactive target is at least 44px tall. */

export function Panel({ title, subtitle, right, children, className = '' }: {
  title: string; subtitle?: string; right?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section
      className={`flex flex-col min-h-0 rounded-[var(--radius-panel)] border border-[var(--color-line)] bg-[var(--color-surface-1)]/60 backdrop-blur-md ${className}`}
    >
      <header className="flex items-center justify-between gap-x-3 gap-y-2 flex-wrap px-4 py-3 border-b border-[var(--color-line)] shrink-0">
        <div className="min-w-0">
          <h2 className="text-[11px] tracking-[0.1em] uppercase text-[var(--color-ink-muted)]">
            {title}
          </h2>
          {subtitle && (
            <p className="text-[11px] text-[var(--color-ink-faint)] truncate mt-0.5">{subtitle}</p>
          )}
        </div>
        {right}
      </header>
      <div className="flex-1 min-h-0 overflow-auto">{children}</div>
    </section>
  )
}

const BUTTON_VARIANTS = {
  // The default is the ghost pill: transparent, a bone hairline that firms up
  // on hover.
  default:
    'bg-transparent text-[var(--color-ink)] border border-[var(--color-line-strong)] hover:border-[var(--color-ink)]',
  // The one filled control: the bone surface, inverted. Only ever the next
  // thing you are meant to do.
  primary:
    'bg-[var(--color-accent)] text-[var(--color-accent-on)] hover:bg-[var(--color-accent-dim)] border border-transparent',
  danger:
    'bg-transparent text-[var(--color-failed)] border border-[color-mix(in_srgb,var(--color-failed)_45%,transparent)] hover:border-[var(--color-failed)]',
  ghost:
    'bg-transparent text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] border border-transparent',
} as const

export function Button({
  children, onClick, disabled, variant = 'default', title, loading, type = 'button', size = 'md',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: keyof typeof BUTTON_VARIANTS
  title?: string
  loading?: boolean
  type?: 'button' | 'submit'
  size?: 'sm' | 'md'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      aria-busy={loading || undefined}
      className={`inline-flex items-center gap-2 ${size === 'sm' ? 'min-h-10 md:min-h-9 px-4' : 'min-h-11 px-6'} rounded-full text-[11px] uppercase tracking-[0.07em] transition-[color,background-color,border-color] disabled:opacity-40 disabled:cursor-not-allowed ${BUTTON_VARIANTS[variant]}`}
    >
      {loading && <Spinner />}
      {children}
    </button>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3.5 w-3.5 rounded-full border-2 border-current border-r-transparent animate-spin ${className}`}
    />
  )
}

const STAT_TONES = {
  default: 'text-[var(--color-ink)]',
  good: 'text-[var(--color-done)]',
  warn: 'text-[var(--color-warn)]',
  bad: 'text-[var(--color-failed)]',
} as const

export function Stat({ label, value, tone = 'default', hint }: {
  label: string; value: ReactNode; tone?: keyof typeof STAT_TONES; hint?: string
}) {
  return (
    <div
      className="border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2.5 transition-colors hover:border-[var(--color-line-strong)]"
      title={hint}
    >
      <div className="text-[10px] uppercase tracking-[0.07em] text-[var(--color-ink-faint)]">
        {label}
      </div>
      <div className={`text-[22px] font-light tabular-nums mt-0.5 ${STAT_TONES[tone]}`}>
        {value}
      </div>
    </div>
  )
}

/** Same three-band read used everywhere a 0-100 score (fitness, quality) is shown. */
export function fitnessTone(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 50) return 'warn'
  return 'bad'
}

const BADGE_TONES = {
  default: 'bg-[var(--color-surface-3)] text-[var(--color-ink-muted)]',
  good: 'bg-[color-mix(in_srgb,var(--color-done)_14%,transparent)] text-[var(--color-done)]',
  bad: 'bg-[color-mix(in_srgb,var(--color-failed)_14%,transparent)] text-[var(--color-failed)]',
  warn: 'bg-[color-mix(in_srgb,var(--color-warn)_14%,transparent)] text-[var(--color-warn)]',
  info: 'bg-[color-mix(in_srgb,var(--color-running)_14%,transparent)] text-[var(--color-running)]',
  accent: 'bg-[color-mix(in_srgb,var(--color-ink)_12%,transparent)] text-[var(--color-accent-ink)]',
} as const

export function Badge({ children, tone = 'default', mono = true }: {
  children: ReactNode; tone?: keyof typeof BADGE_TONES; mono?: boolean
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] whitespace-nowrap ${
        mono ? 'font-mono' : ''
      } ${BADGE_TONES[tone]}`}
    >
      {children}
    </span>
  )
}

export function Empty({ icon, title, children }: {
  icon?: ReactNode; title?: string; children: ReactNode
}) {
  return (
    <div className="h-full grid place-items-center p-8">
      <div className="max-w-sm text-center">
        {icon && <div className="text-2xl mb-3 opacity-40" aria-hidden>{icon}</div>}
        {title && (
          <p className="text-[14px] font-medium text-[var(--color-ink-muted)] mb-1.5">{title}</p>
        )}
        <p className="text-[12px] leading-relaxed text-[var(--color-ink-faint)]">{children}</p>
      </div>
    </div>
  )
}

/** Where the user is in the goal → graph → run → measure flow. */
export function StepRail({ current }: { current: number }) {
  const steps = ['Goal', 'Clarify', 'Graph', 'Run', 'Measure']
  return (
    <ol className="flex items-center gap-1.5" aria-label="Progress">
      {steps.map((s, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'todo'
        return (
          <li key={s} className="flex items-center gap-1.5">
            <span
              aria-current={state === 'active' ? 'step' : undefined}
              className={`text-[10px] uppercase tracking-[0.06em] px-2 py-1 rounded-full transition-colors ${
                state === 'done'
                  ? 'text-[var(--color-done)]'
                  : state === 'active'
                    ? 'text-[var(--color-ink)] bg-[color-mix(in_srgb,var(--color-ink)_10%,transparent)]'
                    : 'text-[var(--color-ink-faint)]'
              }`}
            >
              {state === 'done' ? '✓ ' : ''}{s}
            </span>
            {i < steps.length - 1 && (
              <span aria-hidden className="w-3 h-px bg-[var(--color-line-strong)]" />
            )}
          </li>
        )
      })}
    </ol>
  )
}

export function SkeletonRows({ n = 5 }: { n?: number }) {
  return (
    <div className="p-3 space-y-2" aria-hidden>
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="skeleton h-7 rounded-md" style={{ opacity: 1 - i * 0.14 }} />
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------- icons */
/* A dozen stroke icons, drawn here rather than pulled from a library: the
   whole set is a few hundred bytes and an icon dependency is a few hundred kB. */

const ICONS = {
  graph: <><circle cx="12" cy="5" r="2.2" /><circle cx="5" cy="19" r="2.2" /><circle cx="19" cy="19" r="2.2" /><path d="M12 7.2v3.3c0 .8-.3 1.2-.9 1.7L6.2 17M12 10.5c0 .8.3 1.2.9 1.7l4.9 4.8" /></>,
  chat: <path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v7a2.5 2.5 0 0 1-2.5 2.5h-6l-4 3.5v-3.5H7A2.5 2.5 0 0 1 4.5 14V7A2.5 2.5 0 0 1 7 4.5Z" />,
  book: <path d="M6.5 4h9A3.5 3.5 0 0 1 19 7.5V19a1 1 0 0 1-1 1H9a3.5 3.5 0 0 1-3.5-3.5V5a1 1 0 0 1 1-1ZM5.5 16.5A3.5 3.5 0 0 1 9 13h10" />,
  compare: <><rect x="3" y="5" width="7" height="14" rx="2" /><rect x="14" y="5" width="7" height="14" rx="2" /></>,
  box: <path d="M11 3.2a2 2 0 0 1 2 0l6 3.4a2 2 0 0 1 1 1.7v7.4a2 2 0 0 1-1 1.7l-6 3.4a2 2 0 0 1-2 0l-6-3.4a2 2 0 0 1-1-1.7V8.3a2 2 0 0 1 1-1.7l6-3.4ZM4 7.5l8 4.5 8-4.5M12 12v9" />,
  chart: <path d="M5.5 19.5v-8M12 19.5v-15M18.5 19.5v-10" />,
  merge: <><circle cx="6" cy="6" r="2.2" /><circle cx="6" cy="18" r="2.2" /><circle cx="18" cy="14" r="2.2" /><path d="M6 8.2v7.6M6 8.2c0 4 3.5 5.8 9.8 5.8" /></>,
  sliders: <><path d="M4 7h9.5M18.5 7H20M4 17h2.5M11.5 17H20" /><circle cx="16" cy="7" r="2.2" /><circle cx="9" cy="17" r="2.2" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 3.5v2.5M12 18v2.5M3.5 12H6M18 12h2.5M6 6l1.8 1.8M16.2 16.2 18 18M18 6l-1.8 1.8M7.8 16.2 6 18" /></>,
  plus: <path d="M12 5.5v13M5.5 12h13" />,
  user: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  panel: <><rect x="3.5" y="4.5" width="17" height="15" rx="3.5" /><path d="M9.5 4.5v15" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></>,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2.5" /><path d="M15.5 8.5V6.5A2.5 2.5 0 0 0 13 4H6.5A2.5 2.5 0 0 0 4 6.5V13a2.5 2.5 0 0 0 2.5 2.5h2" /></>,
  download: <path d="M12 4.5v10m0 0-4-4m4 4 4-4M5.5 19.5h13" />,
  trash: <path d="M4.5 7h15M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7M6.5 7l.8 11.2A2 2 0 0 0 9.3 20h5.4a2 2 0 0 0 2-1.8L17.5 7" />,
  external: <path d="M14.5 4.5h5v5M19.5 4.5l-8 8M17.5 14v3.5a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2H10" />,
  check: <path d="m5.5 12.5 4.2 4.2L18.5 7.5" />,
  x: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  chevron: <path d="m9.5 6.5 5.5 5.5-5.5 5.5" />,
} as const

export type IconName = keyof typeof ICONS

export function Icon({ name, size = 16, className = '' }: {
  name: IconName; size?: number; className?: string
}) {
  return (
    <svg
      aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
      className={`shrink-0 ${className}`}
    >
      {ICONS[name]}
    </svg>
  )
}

/** The frame every non-canvas view sits in: a title, one line of context, a column. */
export function Page({ title, sub, right, children }: {
  title: string; sub?: string; right?: ReactNode; children: ReactNode
}) {
  return (
    <div className="mx-auto w-full max-w-[980px] px-4 md:px-6 py-8">
      <header className="flex items-end justify-between gap-4 flex-wrap mb-6">
        <div>
          <h2 className="vm-display text-[28px] md:text-[34px] leading-none rise">{title}</h2>
          {sub && <p className="mt-2 text-[12px] text-[var(--color-ink-muted)] max-w-[60ch]">{sub}</p>}
        </div>
        {right}
      </header>
      {children}
    </div>
  )
}
