/**
 * The brand visual: a field of tiny outlined triangles that resolve into a
 * shape. The hero draws a double helix because Workflow DNA
 * (backend/workflow_dna/) is a real part of this system - runs carry a
 * generation, a parent, a mutation type and a measured fitness - so the
 * signature image states something true rather than decorating.
 *
 * One cloud serves the whole page: it reshapes and travels as you scroll
 * (MorphField), over a still field of ambient triangles (AmbientField).
 */
import { useEffect, useRef } from 'react'

export type Shape = 'helix' | 'lattice' | 'ring' | 'bars' | 'scatter'

interface P {
  x: number; y: number; z: number
  c: string
  s: number
  phase: number
  /** 0-1. Where this particle sits in the stagger: 0 leaves first, 1 last. */
  d: number
}

/**
 * Vivid, never grey. Read from the --spectrum custom property in index.css so
 * the particles and the rest of the product cannot drift apart, and so a skin
 * swaps them too. Resolved once per cloud build; the fallback is the default
 * skin in case this runs before styles are applied.
 */
const FALLBACK = ['#8052ff', '#9a6bff', '#6d3dff', '#ffb829', '#ffcf6b',
                  '#15846e', '#1fb894', '#e85aff', '#4f86ff']

function spectrum(from: Element = document.documentElement): string[] {
  const raw = getComputedStyle(from).getPropertyValue('--spectrum').trim()
  const list = raw ? raw.split(',').map((c) => c.trim()).filter(Boolean) : []
  return list.length ? list : FALLBACK
}

let SPECTRUM = FALLBACK

const TAU = Math.PI * 2
const rnd = (a: number, b: number) => a + Math.random() * (b - a)
/** Sum of three uniforms approximates a gaussian closely enough for jitter. */
const jitter = (k: number) => (Math.random() + Math.random() + Math.random() - 1.5) * k
const pick = <T,>(a: T[]) => a[(Math.random() * a.length) | 0]

function make(x: number, y: number, z: number): P {
  return {
    x, y, z,
    c: pick(SPECTRUM),
    // Bigger marks: the field read as dust at the old size.
    s: rnd(1.9, 5.4),
    phase: Math.random() * TAU,
    d: Math.random(),
  }
}

/** Double helix: two phase-shifted strands plus the rungs between them. */
function helix(n: number): P[] {
  const pts: P[] = []
  const TURNS = 3.05, H = 2.05, R = 0.58
  for (let i = 0; i < n; i++) {
    const t = Math.random()
    const a = t * TURNS * TAU
    const y = (t - 0.5) * H
    const roll = Math.random()
    if (roll < 0.8) {
      // One of the two backbones, thickened by jitter.
      const off = roll < 0.4 ? 0 : Math.PI
      pts.push(make(
        Math.cos(a + off) * R + jitter(0.055),
        y + jitter(0.012),
        Math.sin(a + off) * R + jitter(0.055),
      ))
    } else {
      // A rung, lerped across the two backbones at this height.
      const u = Math.random()
      const ax = Math.cos(a) * R, az = Math.sin(a) * R
      const bx = Math.cos(a + Math.PI) * R, bz = Math.sin(a + Math.PI) * R
      pts.push(make(
        ax + (bx - ax) * u + jitter(0.02),
        y + jitter(0.008),
        az + (bz - az) * u + jitter(0.02),
      ))
    }
  }
  return pts
}

/** A typed grid - one particle cluster per cell, for the handoff-schema section. */
function lattice(n: number): P[] {
  const pts: P[] = []
  const COLS = 6, ROWS = 6
  for (let i = 0; i < n; i++) {
    const cx = ((Math.random() * COLS) | 0) / (COLS - 1) - 0.5
    const cy = ((Math.random() * ROWS) | 0) / (ROWS - 1) - 0.5
    pts.push(make(cx * 1.45 + jitter(0.05), cy * 1.45 + jitter(0.05), jitter(0.1)))
  }
  return pts
}

/** A council seated in a circle, with chords across it for the peer review. */
function ring(n: number): P[] {
  const pts: P[] = []
  const R = 0.66
  for (let i = 0; i < n; i++) {
    if (Math.random() < 0.72) {
      const a = Math.random() * TAU
      pts.push(make(Math.cos(a) * R + jitter(0.05), Math.sin(a) * R * 0.6 + jitter(0.035), jitter(0.3)))
    } else {
      // A chord between two seats: one model ranking another.
      const a = Math.random() * TAU, b = a + rnd(1.4, 2.6), u = Math.random()
      const x1 = Math.cos(a) * R, y1 = Math.sin(a) * R * 0.6
      const x2 = Math.cos(b) * R, y2 = Math.sin(b) * R * 0.6
      pts.push(make(x1 + (x2 - x1) * u + jitter(0.016), y1 + (y2 - y1) * u + jitter(0.016), jitter(0.2)))
    }
  }
  return pts
}

/** Marginal information gain per agent. The fourth column is the one that
 *  earned nothing - the chart the README says this system is willing to draw. */
const MIG = [0.78, 0.61, 0.92, 0.07, 0.44, 0.7]

function bars(n: number): P[] {
  const pts: P[] = []
  const W = 1.3
  for (let i = 0; i < n; i++) {
    const col = (Math.random() * MIG.length) | 0
    const h = MIG[col]
    const x = (col / (MIG.length - 1) - 0.5) * W
    const y = 0.5 - Math.random() * h * 1.05
    const p = make(x + jitter(0.03), y, jitter(0.1))
    // The dead column reads red, so it is legible as a problem before you read a word.
    if (col === 3) p.c = Math.random() < 0.75 ? '#f87171' : '#b91c1c'
    pts.push(p)
  }
  return pts
}

/** No shape at all: the cloud thrown across the whole frame. Two scatter
 *  steps in a row are generated independently, so particles keep churning
 *  between them instead of freezing once the structure is gone. */
function scatter(n: number): P[] {
  const pts: P[] = []
  for (let i = 0; i < n; i++) {
    pts.push(make(rnd(-1.35, 1.35), rnd(-0.78, 0.78), rnd(-0.8, 0.8)))
  }
  return pts
}

const SHAPES: Record<Shape, (n: number) => P[]> = { helix, lattice, ring, bars, scatter }

/** Size, as a multiple of viewport height. The canvas is the whole viewport
 *  now, so height is the stable reference - width varies far too much. */
const FILL: Record<Shape, number> = {
  helix: 0.34, lattice: 0.40, ring: 0.52, bars: 0.44, scatter: 0.98,
}

/**
 * How much each shape turns about its vertical axis, as a multiple of the base
 * rate. The helix is a 3-D object and reads best rotating. The lattice and the
 * bars are charts: spinning them swings the columns edge-on, where cos(angle)
 * squashes them together and the reading is destroyed. They stay face-on at 0.
 * Blending the factor rather than the angle means a morph eases the spin to a
 * stop instead of stopping it abruptly.
 */
const SPIN: Record<Shape, number> = {
  helix: 1, lattice: 0, ring: 0.35, bars: 0, scatter: 0,
}

/** Free-floating drift, once the shape stops holding the particles in place. */
const DRIFT: Record<Shape, number> = {
  helix: 0, lattice: 0, ring: 0, bars: 0, scatter: 1,
}

const SPIN_RATE = 0.00018

/** Hex to [r, g, b], once per shape rather than once per frame. */
function rgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}

/**
 * Builds one point cloud per shape, index-aligned so particle i has a home in
 * every shape and can simply travel between them. Each cloud is sorted by the
 * angle of its point, so a particle moves to the nearest equivalent position
 * in the next shape instead of crossing the whole cloud - the difference
 * between the field reorganising and the field scrambling.
 */
function buildClouds(shapes: Shape[], count: number) {
  SPECTRUM = spectrum()          // re-read, so a skin applies on the next build
  const built = shapes.map((name) => {
    const pts = SHAPES[name](count)
    pts.sort((a, b) => Math.atan2(a.y, a.x) - Math.atan2(b.y, b.x))
    return { pts, rgb: pts.map((q) => rgb(q.c)) }
  })

  // Only the first cloud's delays are ever read, so they are assigned here
  // rather than at creation. Every cloud is sorted by angle, which means index
  // order IS angular order - so basing the delay mostly on the index makes the
  // change sweep round the cloud like a wave, instead of particles fizzing
  // away at unrelated points. The remaining quarter is random, which keeps the
  // wavefront from looking machined.
  const n = built[0].pts.length
  for (let k = 0; k < n; k++) {
    built[0].pts[k].d = 0.76 * (k / n) + 0.24 * Math.random()
  }
  return built
}

/** `rgb(r,g,b)` strings, quantised and cached: a morph would otherwise build
 *  one string per particle per frame. */
function colourCache() {
  const cache = new Map<number, string>()
  return (r: number, g: number, b: number) => {
    const key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
    let s = cache.get(key)
    if (s === undefined) {
      s = `rgb(${r},${g},${b})`
      cache.set(key, s)
    }
    return s
  }
}

/**
 * Ease with a plateau: each shape holds while its copy is being read, and the
 * travel happens in the middle of the gap between two steps.
 *
 * Smootherstep rather than smoothstep: its ends are flatter, so the shape
 * creeps out of rest and settles back into it instead of snapping away. That
 * flatness is what replaced the old plateau.
 */
/**
 * No plateau.
 *
 * There used to be one, to keep a shape still while its paragraph was being
 * read. The cost was a dead stretch: the copy would start sliding up and the
 * cloud just sat there until it had gone. Smootherstep's ends are flat enough
 * to do that job on their own - the shape barely stirs for the first fraction
 * of the gap - so motion now begins the instant the copy does, and the
 * transition gets the whole gap between steps instead of the middle half of
 * it, which also makes it slower.
 */
const smoother = (u: number) => u * u * u * (u * (u * 6 - 15) + 10)

function ease(t: number): number {
  return smoother(Math.min(1, Math.max(0, t)))
}

/**
 * How the cloud gets from one shape to the next.
 *
 * Moving every particle along a straight line on one shared clock makes the
 * whole cloud slide across as a single rigid object - the shape does not
 * reshape, it translates. Two things break that up:
 *
 * STAGGER gives each particle its own start, spread over this fraction of the
 * transition, so the old shape comes apart gradually and the new one builds up
 * rather than both snapping over together. Raising it widens the smear but
 * shortens each particle's own crossing, since they all share one window:
 * at 0.60 the smear runs 343px of scroll and a single particle takes 229px.
 * Keeping that per-particle speed as well would need a 167vh step and a page
 * half as long again, which is not worth it.
 *
 * BULGE bows each path outward in its own direction, peaking halfway and
 * returning to nothing, so particles take a curve instead of the shortest line
 * and the cloud swells slightly as it reorganises.
 */
const STAGGER = 0.60
const BULGE = 0.20

/**
 * How long the cloud takes to catch up with the scroll position, in ms.
 *
 * Everything else here is driven straight off scroll, which means the only way
 * to slow a transition down is to spend more scroll distance on it - and that
 * makes the page longer. This is the second lever: the cloud chases the scroll
 * position rather than being pinned to it, so a flick of the wheel no longer
 * drags the shape along with it instantly. It also absorbs trackpad jitter.
 *
 * Only the cloud is damped. The copy keeps tracking scroll exactly, because
 * text that lags behind the scrollbar reads as a broken page rather than a
 * fluid one. At rest the two agree: the chase always converges.
 */
const CHASE_MS = 260

/**
 * The magnet.
 *
 * Particles inside MAGNET_PX of the pointer leave the shape and collect around
 * the cursor; everything outside that radius carries on as if nothing were
 * happening, so the shape is visibly torn rather than merely bent. Each
 * captured particle holds its own small offset from the cursor, so they gather
 * as a swarm instead of stacking on one point.
 *
 * Capture is per particle and smoothed in time, with a deliberately asymmetric
 * rate: quick to grab, slow to let go, so the cloud snaps to the cursor and
 * then drifts back rather than pinging between states. Applied in screen
 * space, after the cloud is positioned and rotated, so the pull is exactly
 * toward the pointer.
 */
const MAGNET_PX = 300
const MAGNET_GRAB_MS = 190
const MAGNET_RELEASE_MS = 620
const CURSOR_FADE_MS = 180

/**
 * One cloud, drawn across the whole viewport, that reshapes AND travels as the
 * page scrolls.
 *
 * The canvas is fixed behind the copy rather than parked in a column, so the
 * cloud can cross the page: it sits opposite whichever side the current step's
 * text is on, and slides to the other side while the text hands over. The last
 * steps have no shape at all - the particles scatter over the full frame and
 * drift.
 *
 * Progress is taken from where each step's centre sits relative to the centre
 * of the viewport, not from a raw scroll fraction, so a shape is fully settled
 * exactly when its paragraph is centred - however tall the steps happen to be.
 */
export function MorphField({ shapes, anchors, count, stageRef, onProgress, className = '' }: {
  shapes: Shape[]
  /** Horizontal home per step, as a fraction of viewport width. */
  anchors: number[]
  count: number
  stageRef: React.RefObject<HTMLElement | null>
  /** Called each frame with the active step pair, for anything else that has
   *  to stay in lockstep with the cloud (the copy's fade). */
  onProgress?: (i: number, t: number) => void
  className?: string
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const cb = useRef(onProgress)
  cb.current = onProgress

  useEffect(() => {
    const cv = ref.current
    const stage = stageRef.current
    if (!cv || !stage) return
    const ctx = cv.getContext('2d')
    if (!ctx) return

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const clouds = buildClouds(shapes, count)
    const base = clouds[0].pts                    // size and twinkle phase stay put
    const colour = colourCache()
    const fills = shapes.map((s) => FILL[s])
    const spins = shapes.map((s) => SPIN[s])
    const drifts = shapes.map((s) => DRIFT[s])

    let raf = 0
    let w = 0, h = 0
    let narrow = false
    let centres: number[] = []
    // Damped progress across the whole stage, in steps: 2.4 means "40% of the
    // way from step 2 to step 3". Negative until the first frame sets it.
    let chased = -1
    let prevTime = 0
    // Pointer in CSS pixels, and how much of the effect is currently applied.
    let ptrX = 0, ptrY = 0, ptrOn = 0, ptrWanted = 0
    // How captured each particle is, 0 (in the shape) to 1 (at the cursor).
    const grip = new Float32Array(count)

    const measure = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const r = cv.getBoundingClientRect()
      w = r.width; h = r.height
      narrow = w < 901
      cv.width = Math.max(1, Math.round(w * dpr))
      cv.height = Math.max(1, Math.round(h * dpr))
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      const steps = stage.querySelectorAll<HTMLElement>('[data-step]')
      const top = stage.getBoundingClientRect().top + window.scrollY
      centres = [...steps].map((el) => {
        const b = el.getBoundingClientRect()
        return b.top + window.scrollY - top + b.height / 2
      })
    }

    /** Which pair of steps we are between, and how far. */
    const progress = (): [number, number] => {
      if (centres.length < 2) return [0, 0]
      const view = -stage.getBoundingClientRect().top + window.innerHeight / 2
      if (view <= centres[0]) return [0, 0]
      const last = centres.length - 1
      if (view >= centres[last]) return [last - 1, 1]
      let i = 0
      while (i < last - 1 && view > centres[i + 1]) i++
      const span = centres[i + 1] - centres[i]
      return [i, span > 0 ? (view - centres[i]) / span : 0]
    }

    const at = <T,>(arr: T[], i: number): T => arr[Math.min(i, arr.length - 1)]

    // Mouse only. A touch would otherwise land as a single pointermove and
    // leave the cloud leaning at wherever the finger last was.
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return
      ptrX = e.clientX; ptrY = e.clientY; ptrWanted = 1
    }
    const onOut = () => { ptrWanted = 0 }

    const draw = (time: number) => {
      const [rawI, rawT] = progress()
      // The copy is driven from the real scroll position, never the damped one.
      cb.current?.(rawI, rawT)

      // Frame-rate independent: the same catch-up in the same wall time
      // whether this runs at 60Hz or 120Hz. Read once, here, because more than
      // one thing below needs it.
      const dt = prevTime ? Math.min(64, time - prevTime) : 16
      prevTime = time

      const target = rawI + rawT
      if (chased < 0 || reduce) {
        chased = target
      } else {
        chased += (target - chased) * (1 - Math.exp(-dt / CHASE_MS))
      }

      const i = Math.max(0, Math.min(clouds.length - 2, Math.floor(chased)))
      const local = Math.min(1, Math.max(0, chased - i))
      const t = reduce ? Math.round(local) : ease(local)

      const a = clouds[i], b = clouds[Math.min(i + 1, clouds.length - 1)]
      const aRgb = a.rgb, bRgb = b.rgb
      const mix = (arr: number[]) => {
        const v = arr[i]
        return v + (at(arr, i + 1) - v) * t
      }

      ctx.clearRect(0, 0, w, h)

      // Stacked, there is no room to sit beside the text, so the cloud stays
      // centred and dims instead - the copy is reading over it.
      const ax = narrow ? 0.5 : mix(anchors)
      const cx = w * ax
      const cy = h * 0.5
      const scale = h * mix(fills)
      const drift = mix(drifts)
      // Comes to rest face-on for the shapes that are charts.
      const th = (reduce ? 0.6 : time * SPIN_RATE) * mix(spins)
      const cos = Math.cos(th), sin = Math.sin(th)
      // Stacked, the copy reads directly over the cloud, so it thins out to a
      // texture: half the particles at a third of the brightness. That is also
      // half the per-frame work on the device least able to afford it.
      const alphaScale = narrow ? 0.34 : 1
      const stride = narrow ? 2 : 1
      ctx.lineWidth = 1

      if (reduce) {
        ptrOn = 0
      } else {
        ptrOn += (ptrWanted - ptrOn) * (1 - Math.exp(-dt / CURSOR_FADE_MS))
      }
      const held = ptrOn > 0.01
      // Same for every particle this frame, so the exp() is paid once.
      const grabK = 1 - Math.exp(-dt / MAGNET_GRAB_MS)
      const freeK = 1 - Math.exp(-dt / MAGNET_RELEASE_MS)

      for (let k = 0; k < base.length; k += stride) {
        const pa = a.pts[k], pb = b.pts[k]
        const p = base[k]

        // This particle's own slice of the transition.
        const u = Math.min(1, Math.max(0, (t - p.d * STAGGER) / (1 - STAGGER)))
        const e = smoother(u)
        let px = pa.x + (pb.x - pa.x) * e
        let py = pa.y + (pb.y - pa.y) * e
        let pz = pa.z + (pb.z - pa.z) * e

        // Nothing at either end, widest in the middle of its own crossing.
        if (u > 0 && u < 1) {
          const bow = Math.sin(u * Math.PI) * BULGE
          px += Math.cos(p.phase) * bow
          py += Math.sin(p.phase * 1.3) * bow
          pz += Math.cos(p.phase * 0.7) * bow
        }

        if (drift > 0 && !reduce) {
          px += Math.sin(time * 0.00022 + p.phase) * 0.07 * drift
          py += Math.cos(time * 0.00019 + p.phase * 1.7) * 0.06 * drift
        }

        const rx = px * cos + pz * sin
        const rz = -px * sin + pz * cos
        const depth = (rz + 0.8) / 1.6
        let sx = cx + rx * scale
        let sy = cy + py * scale

        // --- magnet -------------------------------------------------------
        let want = 0
        if (held) {
          const ddx = ptrX - sx, ddy = ptrY - sy
          const d2 = ddx * ddx + ddy * ddy
          if (d2 < MAGNET_PX * MAGNET_PX) {
            // Doubling before the curve means everything inside half the
            // radius is taken completely, not merely leaned on: the shape
            // tears rather than dents. Beyond that it falls off to nothing.
            const grab = Math.min(1, (1 - Math.sqrt(d2) / MAGNET_PX) * 2.1)
            want = grab * grab * (3 - 2 * grab) * ptrOn
          }
        }
        let g = grip[k]
        g += (want - g) * (want > g ? grabK : freeK)
        grip[k] = g

        if (g > 0.002) {
          // Its own spot in the swarm, so the cluster has volume. ptrX/ptrY
          // are deliberately left stale after the pointer goes: the particle
          // then glides home from where the cursor was, not from the origin.
          // Angle from the particle's phase, radius from a golden-ratio walk
          // on its index. Deriving both from phase locked them together and
          // the swarm came out as a hollow ring; these are independent, and
          // the sqrt spreads them evenly over the area rather than crowding
          // the rim.
          const orbit = 8 + 56 * Math.sqrt((k * 0.618033988749895) % 1)
          sx += (ptrX + Math.cos(p.phase) * orbit - sx) * g
          sy += (ptrY + Math.sin(p.phase) * orbit - sy) * g
        }

        if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue

        const size = p.s * (0.55 + depth * 0.75)
        const twinkle = reduce ? 1 : 0.72 + Math.sin(time * 0.0014 + p.phase) * 0.28
        ctx.globalAlpha =
          Math.min(1, (0.26 + depth * 0.74) * twinkle) * alphaScale * (1 - 0.42 * drift)

        const ca = aRgb[k], cb2 = bRgb[k]
        ctx.strokeStyle = colour(
          (ca[0] + (cb2[0] - ca[0]) * t) | 0,
          (ca[1] + (cb2[1] - ca[1]) * t) | 0,
          (ca[2] + (cb2[2] - ca[2]) * t) | 0,
        )

        ctx.beginPath()
        ctx.moveTo(sx, sy - size)
        ctx.lineTo(sx + size * 0.87, sy + size * 0.6)
        ctx.lineTo(sx - size * 0.87, sy + size * 0.6)
        ctx.closePath()
        ctx.stroke()
      }
      ctx.globalAlpha = 1
    }

    const loop = (t: number) => { draw(t); raf = requestAnimationFrame(loop) }

    measure()
    if (reduce) {
      // No idle animation: repaint only when the scroll position changes.
      const onScroll = () => draw(0)
      draw(0)
      window.addEventListener('scroll', onScroll, { passive: true })
      const onResize = () => { measure(); draw(0) }
      window.addEventListener('resize', onResize)
      const ro = new ResizeObserver(onResize)
      ro.observe(stage)
      document.fonts?.ready.then(onResize).catch(() => {})
      return () => {
        window.removeEventListener('scroll', onScroll)
        window.removeEventListener('resize', onResize)
        ro.disconnect()
      }
    }

    raf = requestAnimationFrame(loop)
    window.addEventListener('resize', measure)
    window.addEventListener('pointermove', onMove, { passive: true })
    document.addEventListener('mouseleave', onOut)
    window.addEventListener('blur', onOut)
    // The step centres are measured once, but the layout keeps moving after
    // mount: the web font swaps in and every block of copy changes height.
    // Stale centres put the settle point tens of pixels away from the
    // paragraph it is supposed to line up with, which is the one thing this
    // whole arrangement depends on. Re-measure whenever the stage resizes,
    // and once more after the fonts land.
    const ro = new ResizeObserver(measure)
    ro.observe(stage)
    document.fonts?.ready.then(measure).catch(() => {})
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('resize', measure)
      window.removeEventListener('pointermove', onMove)
      document.removeEventListener('mouseleave', onOut)
      window.removeEventListener('blur', onOut)
    }
  }, [shapes, anchors, count, stageRef])

  return <canvas ref={ref} className={className} aria-hidden="true" />
}

/**
 * The ambient field: triangles drifting behind everything, on both surfaces.
 *
 * Live rather than a single painted frame - it is the only motion on the app
 * screen, which does not scroll through shapes the way the landing page does,
 * and a still field there read as a texture bitmap rather than as the same
 * thing the landing page is made of. Deliberately slow and sparse: this runs
 * behind a working UI, so it twinkles and drifts and does nothing else. No
 * cursor magnet here; that belongs to the hero, not to a trace viewer.
 */
export function AmbientField({ count = 90, className = 'dala-ambient', split = false }: {
  count?: number
  className?: string
  /** Draw each mark as three offset copies in the first three palette entries
   *  (red, green, blue) under additive blending: chromatic aberration, where
   *  the overlap burns white and the edges fringe. */
  split?: boolean
}) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const cv = ref.current
    if (!cv) return
    const ctx = cv.getContext('2d')
    if (!ctx) return

    const spec = spectrum(cv)
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let w = 0, h = 0, raf = 0

    // Positions are fractions of the canvas, so a resize does not teleport
    // anything - it just re-reads the box.
    const pts = Array.from({ length: count }, () => ({
      fx: Math.random(), fy: Math.random(),
      s: rnd(1.8, 4.6),
      c: pick(spec),
      phase: Math.random() * TAU,
      amp: rnd(24, 80),
    }))

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const r = cv.getBoundingClientRect()
      // A fixed, full-viewport layer measures 0x0 at mount - the box is not
      // laid out yet when the effect runs, and the ResizeObserver never fires
      // afterwards because nothing about the element changes. The loop then
      // ran all along, 60 times a second, into a 1x1 canvas. Fall back to the
      // viewport, which is what this layer covers by definition.
      w = r.width >= 2 ? r.width : window.innerWidth
      h = r.height >= 2 ? r.height : window.innerHeight
      cv.width = Math.max(1, Math.round(w * dpr))
      cv.height = Math.max(1, Math.round(h * dpr))
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const tri = (x: number, y: number, s: number) => {
      ctx.beginPath()
      ctx.moveTo(x, y - s)
      ctx.lineTo(x + s * 0.87, y + s * 0.6)
      ctx.lineTo(x - s * 0.87, y + s * 0.6)
      ctx.closePath()
      ctx.stroke()
    }

    const draw = (time: number) => {
      ctx.clearRect(0, 0, w, h)
      ctx.lineWidth = 1
      ctx.globalCompositeOperation = split ? 'lighter' : 'source-over'
      for (const p of pts) {
        const x = p.fx * w + (reduce ? 0 : Math.sin(time * 0.00034 + p.phase) * p.amp)
        const y = p.fy * h + (reduce ? 0 : Math.cos(time * 0.00029 + p.phase * 1.4) * p.amp)
        const tw = reduce ? 1 : 0.62 + Math.sin(time * 0.0022 + p.phase) * 0.38
        ctx.globalAlpha = (0.24 + 0.5 * tw) * (reduce ? 1.2 : 1)
        if (split) {
          // Red left, blue right, green nudged down: the channels disagree by
          // about a pixel, which at this size is a fringe, not a smear.
          const d = 0.6 + p.s * 0.2
          ctx.strokeStyle = spec[0]; tri(x - d, y, p.s)
          ctx.strokeStyle = spec[1]; tri(x, y + d * 0.6, p.s)
          ctx.strokeStyle = spec[2]; tri(x + d, y, p.s)
        } else {
          ctx.strokeStyle = p.c
          tri(x, y, p.s)
        }
      }
      ctx.globalAlpha = 1
      ctx.globalCompositeOperation = 'source-over'
    }

    const loop = (t: number) => { draw(t); raf = requestAnimationFrame(loop) }

    resize()
    if (reduce) {
      draw(0)
      const ro = new ResizeObserver(() => { resize(); draw(0) })
      ro.observe(cv)
      return () => ro.disconnect()
    }
    raf = requestAnimationFrame(loop)
    const ro = new ResizeObserver(resize)
    ro.observe(cv)
    window.addEventListener('resize', resize)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('resize', resize)
    }
  }, [count])

  // The size MUST come from CSS. A canvas with no CSS size takes its layout
  // size from its width/height attributes, so the ResizeObserver below would
  // see every resize it had just caused and grow without limit - it reached
  // 33 million pixels square before this was pinned down. Setting it here
  // rather than trusting each caller's class is the whole fix.
  return (
    <canvas
      ref={ref}
      className={className}
      style={{ width: '100%', height: '100%' }}
      aria-hidden="true"
    />
  )
}
