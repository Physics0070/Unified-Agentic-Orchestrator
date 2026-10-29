/**
 * Sign in / create account. Accounts are optional unless the backend says
 * otherwise (GET /api/auth/me -> auth_required), so this is a plain modal opened
 * from the sidebar footer, or automatically when an API call answers 401.
 */
import { useEffect, useRef, useState } from 'react'
import { api, type AuthUser } from './api'
import { Button } from './ui'

const INPUT =
  'min-h-10 px-3 rounded-[10px] bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[16px] md:text-[13px] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)] outline-none transition-colors w-full'

const MIN_PASSWORD = 10

/** The server's message without the "401 · " status prefix req() adds. */
const clean = (e: unknown) => {
  const m = (e as Error).message ?? 'Something went wrong'
  const i = m.indexOf(' · ')
  return i >= 0 ? m.slice(i + 3) : m
}

export function AuthDialog({ open, onClose, user, authRequired, onAuthed, onSignedOut }: {
  open: boolean
  onClose: () => void
  user: AuthUser | null
  authRequired: boolean
  onAuthed: (u: AuthUser) => void
  onSignedOut: () => void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const emailRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setErr(null); setPassword('')
    setTimeout(() => emailRef.current?.focus(), 0)
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null

  const submit = async () => {
    setErr(null)
    if (!email.trim()) { setErr('Enter your email address'); return }
    if (mode === 'register' && password.length < MIN_PASSWORD) {
      setErr(`Use at least ${MIN_PASSWORD} characters for the password`); return
    }
    if (!password) { setErr('Enter your password'); return }
    setBusy(true)
    try {
      const r = mode === 'login' ? await api.login(email.trim(), password) : await api.register(email.trim(), password)
      setPassword('')
      onAuthed(r.user)
    } catch (e) {
      setErr(clean(e))
    } finally {
      setBusy(false)
    }
  }

  const signOut = async () => {
    setBusy(true)
    try { await api.logout() } catch { /* the cookie is cleared server-side; nothing more to do */ }
    setBusy(false)
    onSignedOut()
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-center items-start pt-[10vh] bg-black/50 backdrop-blur-sm" onMouseDown={onClose}>
      <div
        role="dialog" aria-modal="true" aria-label={user ? 'Account' : 'Sign in'}
        onMouseDown={(e) => e.stopPropagation()}
        className="w-[min(400px,92vw)] glass-panel p-5 space-y-4"
      >
        {user ? (
          <>
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">Signed in as</p>
              <p className="text-[14px] break-all mt-1">{user.email}</p>
            </div>
            <p className="text-[12px] text-[var(--color-ink-muted)]">
              Your runs and knowledge workspaces are private to this account.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>Close</Button>
              <Button onClick={signOut} loading={busy}>Sign out</Button>
            </div>
          </>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); submit() }} className="space-y-4">
            <div className="flex items-center gap-4">
              {(['login', 'register'] as const).map((m) => (
                <button
                  key={m} type="button" onClick={() => { setMode(m); setErr(null) }}
                  aria-pressed={mode === m}
                  className={`text-[12px] uppercase tracking-[0.08em] pb-1 border-b ${
                    mode === m ? 'border-[var(--color-ink)] text-[var(--color-ink)]' : 'border-transparent text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]'
                  }`}
                >
                  {m === 'login' ? 'Sign in' : 'Create account'}
                </button>
              ))}
            </div>
            {authRequired && (
              <p className="text-[12px] text-[var(--color-warn)]">Sign in to use the orchestrator. Chat works without an account.</p>
            )}
            <label className="block space-y-1">
              <span className="text-[11px] text-[var(--color-ink-faint)]">Email</span>
              <input
                ref={emailRef} type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                autoComplete="email" required className={INPUT}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-[11px] text-[var(--color-ink-faint)]">
                Password{mode === 'register' ? ` (at least ${MIN_PASSWORD} characters)` : ''}
              </span>
              <input
                type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required className={INPUT}
              />
            </label>
            {err && <p role="alert" className="text-[12px] text-[var(--color-failed)]">{err}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>{authRequired ? 'Not now' : 'Cancel'}</Button>
              <Button variant="primary" type="submit" loading={busy}>
                {mode === 'login' ? 'Sign in' : 'Create account'}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
