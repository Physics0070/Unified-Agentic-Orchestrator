import type { ReactNode } from 'react'

/** Shared primitives. Every interactive target is at least 44px tall. */

export function Panel({ title, subtitle, right, children, className = '' }: {
  title: string; subtitle?: string; right?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section
      className={`flex flex-col min-h-0 rounded-[var(--radius-panel)] border border-[var(--color-line)] bg-[var(--color-surface-1)]/50 ${className}`}
    >
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--color-line)] shrink-0">
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
      className={`inline-flex items-center gap-2 ${size === 'sm' ? 'min-h-9 px-4' : 'min-h-11 px-6'} rounded-full text-[11px] uppercase tracking-[0.07em] transition-[color,background-color,border-color] disabled:opacity-40 disabled:cursor-not-allowed ${BUTTON_VARIANTS[variant]}`}
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
  graph: <><circle cx="12" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" /><path d="M12 7v4M12 11 5 17M12 11l7 6" /></>,
  chat: <path d="M4 5h16v11H9l-5 4V5Z" />,
  book: <path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4V4ZM5 16a4 4 0 0 1 4-4h10" />,
  box: <path d="M12 3 4 7v10l8 4 8-4V7l-8-4ZM4 7l8 4 8-4M12 11v10" />,
  chart: <path d="M5 20V10M12 20V4M19 20v-7" />,
  merge: <><circle cx="6" cy="6" r="2" /><circle cx="6" cy="18" r="2" /><circle cx="18" cy="14" r="2" /><path d="M6 8v8M6 8c0 4 4 6 10 6" /></>,
  sliders: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  panel: <><rect x="3" y="4" width="18" height="16" /><path d="M9 4v16" /></>,
  search: <><circle cx="11" cy="11" r="6" /><path d="m20 20-4-4" /></>,
  copy: <><rect x="8" y="8" width="12" height="12" /><path d="M4 16V4h12" /></>,
  download: <path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" />,
  trash: <path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" />,
  check: <path d="m5 12 5 5 9-10" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  chevron: <path d="m9 6 6 6-6 6" />,
} as const

export type IconName = keyof typeof ICONS

export function Icon({ name, size = 16, className = '' }: {
  name: IconName; size?: number; className?: string
}) {
  return (
    <svg
      aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" strokeLinejoin="miter"
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
    <div className="mx-auto w-full max-w-[980px] px-6 py-8">
      <header className="flex items-end justify-between gap-4 flex-wrap mb-6">
        <div>
          <h2 className="vm-display text-[34px] leading-none rise">{title}</h2>
          {sub && <p className="mt-2 text-[12px] text-[var(--color-ink-muted)] max-w-[60ch]">{sub}</p>}
        </div>
        {right}
      </header>
      {children}
    </div>
  )
}
