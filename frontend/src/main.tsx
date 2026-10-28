import { lazy, StrictMode, Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Landing } from './Landing'
import './index.css'

// The app is the heavy half (reactflow, the chat stream, the whole event
// vocabulary). The landing has to paint while the free-tier backend is still
// waking, so the app is split out and only fetched once someone asks for it.
const App = lazy(() => import('./App'))

function Root() {
  const [entered, setEntered] = useState(() => window.location.hash.startsWith('#app'))

  useEffect(() => {
    const sync = () => setEntered(window.location.hash.startsWith('#app'))
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  if (!entered) {
    return <Landing onEnter={() => { window.location.hash = '#app' }} />
  }
  // Nothing is shown during the chunk fetch: it is one small file from the
  // same origin, and a flash of skeleton reads worse than the half-beat.
  return <Suspense fallback={null}><App /></Suspense>
}

// Colour skins: ?skin=ember | signal | glacier. Anything else is the default.
// A query parameter rather than a picker, because this is for choosing a
// direction, not a feature anyone asked to keep.
// Two independent knobs: ?skin= moves the UI accent, ?particles= moves only
// the cloud's colours. Both are query parameters rather than a picker, because
// this is for choosing a direction, not a feature anyone asked to keep.
const q = new URLSearchParams(window.location.search)
const skin = q.get('skin')
if (skin && ['ember', 'signal', 'glacier'].includes(skin)) {
  document.documentElement.dataset.skin = skin
}
const particles = q.get('particles')
if (particles && ['aurora', 'ember', 'ice', 'prism', 'mono'].includes(particles)) {
  document.documentElement.dataset.particles = particles
}

const root = document.getElementById('root')
if (!root) throw new Error('#root missing from index.html')

createRoot(root).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
