/**
 * The landing surface.
 *
 * It exists because the app behind it cannot be the first thing a visitor
 * meets: the backend sleeps on a free tier and takes about a minute to wake,
 * so the first question anyone asks appears to hang. This page renders
 * instantly, makes the argument, and hands over to the app on a click.
 *
 * It talks to nothing. No fetch, no provider, no event log - which is the
 * point: it is still correct when the server is asleep.
 */
import { useCallback, useEffect, useRef } from 'react'
import './landing.css'
import { AmbientField, MorphField, type Shape } from './ParticleField'

function Mark() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="d-mark" x1="2" y1="2" x2="18" y2="18">
          <stop offset="0" stopColor="var(--color-accent)" />
          <stop offset="1" stopColor="var(--color-accent-2)" />
        </linearGradient>
      </defs>
      <path d="M10 1.6 18.4 18H1.6L10 1.6Z" fill="url(#d-mark)" />
    </svg>
  )
}

const REPO = 'https://github.com/Physics0070/Unified-Agentic-Orchestrator'

/**
 * The scroll story. Each step is a block of copy, the shape the cloud holds
 * while that copy is centred, and which side the copy sits on - the cloud
 * takes the opposite side, so handing over from one step to the next sends it
 * across the page.
 *
 * The last two steps have no shape: the cloud scatters over the whole frame
 * and drifts while the closing copy runs full width over it.
 */
const STEPS: { shape: Shape; side: 'left' | 'right' | 'full' }[] = [
  { shape: 'helix', side: 'left' },
  { shape: 'lattice', side: 'right' },
  { shape: 'ring', side: 'left' },
  { shape: 'bars', side: 'right' },
  { shape: 'scatter', side: 'full' },
  { shape: 'scatter', side: 'full' },
]

/** Module-level and stable, so the morph effect is not rebuilt every render. */
const SHAPES = STEPS.map((s) => s.shape)
const ANCHORS = STEPS.map((s) => (s.side === 'left' ? 0.72 : s.side === 'right' ? 0.28 : 0.5))

/** How far from the centre of the screen a step fades out completely. Tied to
 *  the step height: taller steps would otherwise leave a long stretch with no
 *  copy on screen at all, rather than the brief handover that is wanted. */
const FADE_SPAN = 0.72

export function Landing({ onEnter }: { onEnter: () => void }) {
  const stageRef = useRef<HTMLDivElement>(null)
  const stepsRef = useRef<HTMLElement[]>([])
  const reduceRef = useRef(
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    stepsRef.current = [
      ...(stageRef.current?.querySelectorAll<HTMLElement>('[data-step]') ?? []),
    ]
  }, [])

  /**
   * Driven from the cloud's own frame loop rather than a second one, so the
   * copy and the particles can never disagree about where the page is. Each
   * step rises into place as it reaches the middle of the screen and leaves
   * upward, which is what makes the next one read as arriving from below on
   * the other side.
   */
  const paintCopy = useCallback(() => {
    // The cloud stops its frame loop under reduced motion, so the fade would
    // freeze at whatever it last wrote - and a step stranded at opacity 0 is an
    // invisible page. Reset and leave; the copy simply does not animate.
    if (reduceRef.current) {
      for (const el of stepsRef.current) {
        el.style.opacity = ''
        el.style.transform = ''
        el.style.pointerEvents = ''
      }
      return
    }
    const mid = window.innerHeight / 2
    const span = window.innerHeight * FADE_SPAN
    const last = stepsRef.current.length - 1
    stepsRef.current.forEach((el, idx) => {
      const b = el.getBoundingClientRect()
      let d = (b.top + b.height / 2 - mid) / span            // -1 above, +1 below
      // The first and last steps cannot be scrolled to the centre - the page
      // runs out - so they would sit permanently dimmed. Each only fades on
      // the side it can actually leave from.
      if (idx === 0 && d > 0) d = 0
      if (idx === last && d < 0) d = 0
      const k = Math.min(1, Math.abs(d))
      const opacity = 1 - k * k
      el.style.opacity = String(opacity)
      el.style.transform = `translateY(${(d * 64).toFixed(1)}px)`
      // A faded-out step is still in the layout; it must not eat clicks meant
      // for the one that is actually on screen.
      el.style.pointerEvents = opacity < 0.12 ? 'none' : ''
    })
  }, [])

  return (
    <div className="dala">
      <AmbientField count={420} />
      <MorphField
        shapes={SHAPES} anchors={ANCHORS} count={4000}
        stageRef={stageRef} onProgress={paintCopy} className="d-field"
      />

      <div className="dala-shell">
        <nav className="d-nav">
          <a className="d-brand" href="#" onClick={(e) => { e.preventDefault(); onEnter() }}>
            <Mark />
            Agent&nbsp;Council
          </a>
          <div className="d-nav-links">
            <a className="d-nav-hide" href="#how">How it works</a>
            <a className="d-nav-hide" href={`${REPO}/blob/main/docs/architecture.md`} target="_blank" rel="noreferrer">
              Architecture
            </a>
            <a className="d-nav-hide-sm" href={REPO} target="_blank" rel="noreferrer">GitHub</a>
            <button className="d-pill" onClick={onEnter}>Open the app</button>
          </div>
        </nav>

        <div className="d-stage" ref={stageRef}>
          <section className="d-step d-step--left d-step--hero" data-step>
            <p className="d-label">Multi-agent orchestration · AE-03</p>
            <h1 className="d-display">Ask anything. Then see what it cost.</h1>
            <p className="d-body">
              Simple questions go straight to one model and stream back in about two seconds.
              Harder ones are split across specialists that run in parallel and merge into one
              reply — and the system tells you, out loud, when its own council was overkill.
            </p>
            <div className="d-hero-ctas">
              <button className="d-pill" onClick={onEnter}>Try a question →</button>
              <a className="d-ghost" href="#how">See how it works</a>
            </div>
            <div className="d-proof">
              <span>Zero-diff replay</span>
              <span>Typed handoffs</span>
              <span>₹0 to run</span>
              <span>Apache 2.0</span>
            </div>
          </section>

          <section className="d-step d-step--right" data-step id="how">
            <p className="d-label">Typed handoffs</p>
            <h2 className="d-h2">Agents pass forms, not chat messages.</h2>
            <p className="d-body d-body--quiet">
              Every edge in the graph carries a JSON Schema. A wrong shape is rejected, the exact
              validator error goes back to the agent that produced it, and it tries again. Like a
              hospital handover chart with required fields, not “the patient seems unwell.”
            </p>
          </section>

          <section className="d-step d-step--left" data-step>
            <p className="d-label">Anonymous peer review</p>
            <h2 className="d-h2">A council, not a single boss.</h2>
            <p className="d-body d-body--quiet">
              Several models each propose, then rank each other with authorship stripped. A
              reviewer shown “proposal by qwen2.5” would rate the label instead of the content, so
              it never sees one. A chairman merges the winner.
            </p>
          </section>

          <section className="d-step d-step--right" data-step>
            <p className="d-label">Marginal information gain</p>
            <h2 className="d-h2">It reports when it is overkill.</h2>
            <p className="d-body d-body--quiet">
              We measure whether the extra agents actually helped. One scoring below 0.1 produced
              nothing the others did not already have, and the system names it out loud rather
              than quietly billing you for it.
            </p>
          </section>

          <section className="d-step d-step--full" data-step>
            <div className="d-stats">
              <div className="d-stat">
                <b>Zero</b>
                <span>Difference between a run and its replay. Same seed, same config hash, served from the event log.</span>
              </div>
              <div className="d-stat">
                <b>One table</b>
                <span>An append-only event log is the only source of truth. Every metric, trace and replay is a read of it.</span>
              </div>
              <div className="d-stat">
                <b>₹0</b>
                <span>Free tiers end to end. Ollama Cloud answers by default; Groq and Gemini widen the council when configured.</span>
              </div>
            </div>
          </section>

          <section className="d-step d-step--full d-step--close" data-step>
            <p className="d-label">Two surfaces</p>
            <h2 className="d-h3">
              Ask it a question, or hand it a goal and watch it design the agent team itself.
            </h2>
            <p className="d-body d-body--quiet">
              Type a goal and the system asks clarifying questions, requests permission, compiles a
              graph for your approval, runs it parallel-first and streams live state. You never
              hand-code an agent.
            </p>
            <button className="d-pill" onClick={onEnter}>Open the app</button>
            <p className="d-caption">
              Hosted on a free tier that sleeps. The first question may take about a minute to wake it.
            </p>
          </section>
        </div>

        <footer className="d-foot">
          <span>Team Latency Zero · VIT Pune</span>
          <a href={REPO} target="_blank" rel="noreferrer">Source</a>
          <a href={`${REPO}/blob/main/docs/threat-model.md`} target="_blank" rel="noreferrer">Threat model</a>
          <a href={`${REPO}/blob/main/docs/reproducibility.md`} target="_blank" rel="noreferrer">Reproducibility</a>
          <span className="d-foot-end">Apache 2.0</span>
        </footer>
      </div>
    </div>
  )
}

