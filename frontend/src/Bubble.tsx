import type { ReactNode } from 'react'
import './bubble.css'

export type OrbState = 'idle' | 'running' | 'done' | 'failed'

/** Glass sphere whose look is entirely a function of `state`. */
export function RunOrb({ state, label, size = 20 }: { state: OrbState; label: string; size?: number }) {
  return <span role="img" aria-label={label} data-state={state} className="orb" style={{ width: size, height: size }} />
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return <span className="chip" title={title}>{children}</span>
}
