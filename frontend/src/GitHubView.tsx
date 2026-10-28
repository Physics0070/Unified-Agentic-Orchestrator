import { useEffect, useState } from 'react'
import { api } from './api'
import { ago } from './store'
import { Badge, Button, Empty, Icon, Page, SkeletonRows } from './ui'

interface Repo { full_name: string; description: string | null; stargazers_count: number; open_issues_count: number; language: string | null; default_branch: string; html_url: string; pushed_at: string }
interface Commit { sha: string; html_url: string; commit: { message: string; author: { name: string; date: string } } }
interface Pull { number: number; title: string; html_url: string; user: { login: string }; created_at: string; draft: boolean }

// "owner/name" or a github.com URL; anything else is rejected before a request is made.
const parse = (s: string) => /^(?:https?:\/\/github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/.exec(s.trim())?.slice(1, 3).join('/') ?? null

export function GitHubView({ repo, setRepo, onGoal, toast }: {
  repo: string | null
  setRepo: (r: string | null) => void
  onGoal: (goal: string) => void
  toast: (t: string, tone?: 'ok' | 'warn') => void
}) {
  const [input, setInput] = useState(repo ?? '')
  const [token, setToken] = useState('')
  const [data, setData] = useState<{ repo: Repo; commits: Commit[]; pulls: Pull[]; left: string | null } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async (full: string) => {
    setBusy(true); setErr(null)
    const headers: Record<string, string> = { Accept: 'application/vnd.github+json' }
    if (token) headers.Authorization = `Bearer ${token}`
    const get = async (p: string) => {
      const r = await fetch(`https://api.github.com/repos/${full}${p}`, { headers })
      if (!r.ok) throw new Error(r.status === 404 ? 'Repository not found (or private: add a token).'
        : r.status === 403 || r.status === 429 ? 'GitHub rate limit reached. Add a token for a higher limit.' : `GitHub answered ${r.status}.`)
      return r
    }
    try {
      const [a, b, c] = await Promise.all([get(''), get('/commits?per_page=5'), get('/pulls?state=open&per_page=5')])
      setData({ repo: await a.json(), commits: await b.json(), pulls: await c.json(), left: a.headers.get('x-ratelimit-remaining') })
      setRepo(full)
    } catch (e) {
      setData(null)
      setErr(e instanceof TypeError ? 'Could not reach api.github.com (offline or blocked).' : (e as Error).message)
    } finally { setBusy(false) }
  }

  useEffect(() => { if (repo) load(repo) }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Goal max is 2000 chars (backend MAX_GOAL): trim the context, never the goal.
  const attach = async (full: string) => {
    setBusy(true)
    try {
      const goal = `Audit ${full} and produce a prioritized remediation report.`
      const head = `${goal}\n\nRepository context:\n`
      const { text } = await api.githubContext(full, token)
      onGoal(head + text.slice(0, Math.max(0, 2000 - head.length)))
      toast('Repository context attached')
    } catch (e) { toast((e as Error).message, 'warn') } finally { setBusy(false) }
  }

  const connect = () => {
    const full = parse(input)
    if (!full) { setErr('Enter owner/name or a github.com URL.'); return }
    load(full)
  }

  return (
    <Page title="GitHub" sub="Live repository activity from GitHub. Audit and Review send a goal about the repo; Attach repo context also appends its description, top-level files, recent commits and README excerpt, fetched by the backend (code beyond that is not read).">
      <form onSubmit={(e) => { e.preventDefault(); connect() }} className="flex gap-2 flex-wrap mb-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} aria-label="Repository" placeholder="owner/repo or github.com URL"
          className="flex-1 min-w-[240px] h-10 px-3 font-mono text-[12px] bg-[var(--color-surface-2)] border border-[var(--color-line)] outline-none focus:border-[var(--color-ink)]" />
        <Button type="submit" variant="primary" loading={busy}>Connect</Button>
        {repo && <Button onClick={() => { setRepo(null); setData(null); setInput(''); toast('Repository disconnected') }}>Disconnect</Button>}
      </form>
      <details className="mb-6 text-[11px] text-[var(--color-ink-faint)]">
        <summary className="cursor-pointer">Private repo or rate-limited? Add a token</summary>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} aria-label="GitHub token" placeholder="read-only personal access token"
          className="mt-2 w-full max-w-[420px] h-9 max-md:h-10 max-md:text-[16px] px-3 font-mono text-[12px] bg-[var(--color-surface-2)] border border-[var(--color-line)] outline-none" />
        <p className="mt-1">Held in memory only, never saved: it is gone when you reload. It is sent to this app's server, which forwards it to api.github.com for that request and does not log or store it.</p>
      </details>

      {err && <p role="alert" className="mb-4 text-[12px] text-[var(--color-failed)]">{err}</p>}
      {busy && !data && <SkeletonRows n={5} />}
      {!busy && !data && !err && <Empty icon="⑂" title="No repository connected">Paste a public repo above to see its latest commits and open pull requests.</Empty>}

      {data && (
        <div className="grid gap-8">
          <section className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <a href={data.repo.html_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 text-[18px] hover:underline">
                {data.repo.full_name} <Icon name="external" size={14} />
              </a>
              <p className="mt-1 text-[12px] text-[var(--color-ink-muted)]">{data.repo.description ?? 'No description.'}</p>
              <div className="mt-2 flex gap-2 flex-wrap">
                {data.repo.language && <Badge>{data.repo.language}</Badge>}
                <Badge>★ {data.repo.stargazers_count}</Badge>
                <Badge>{data.repo.open_issues_count} open</Badge>
                <Badge>pushed {ago(Date.parse(data.repo.pushed_at))}</Badge>
                {data.left && <Badge tone="info">{data.left} requests left</Badge>}
              </div>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => attach(data.repo.full_name)} loading={busy}>Attach repo context</Button>
              <Button variant="primary" onClick={() => onGoal(`Audit ${data.repo.full_name} and produce a prioritized remediation report.`)}>Audit this repo</Button>
            </div>
          </section>

          <section>
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-2">Open pull requests</h3>
            {data.pulls.length === 0 ? <p className="text-[12px] text-[var(--color-ink-faint)]">None open.</p> : (
              <ul className="border-t border-[var(--color-line)]">{data.pulls.map((p) => (
                <li key={p.number} className="flex items-center gap-3 py-2.5 border-b border-[var(--color-line)]">
                  <span className="font-mono text-[11px] text-[var(--color-ink-faint)]">#{p.number}</span>
                  <a href={p.html_url} target="_blank" rel="noreferrer" className="flex-1 min-w-0 truncate text-[13px] hover:underline">{p.title}</a>
                  {p.draft && <Badge>draft</Badge>}
                  <span className="text-[11px] text-[var(--color-ink-faint)] hidden sm:inline">{p.user.login} · {ago(Date.parse(p.created_at))}</span>
                  <Button size="sm" onClick={() => onGoal(`Review pull request #${p.number} "${p.title}" in ${data.repo.full_name}: list risks, missing tests and a merge recommendation.`)}>Review</Button>
                </li>))}</ul>)}
          </section>

          <section>
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)] mb-2">Latest commits on {data.repo.default_branch}</h3>
            <ul className="border-t border-[var(--color-line)]">{data.commits.map((c) => (
              <li key={c.sha} className="flex items-center gap-3 py-2.5 border-b border-[var(--color-line)]">
                <a href={c.html_url} target="_blank" rel="noreferrer" className="font-mono text-[11px] text-[var(--color-accent-ink)] hover:underline">{c.sha.slice(0, 7)}</a>
                <span className="flex-1 min-w-0 truncate text-[13px]">{c.commit.message.split('\n')[0]}</span>
                <span className="text-[11px] text-[var(--color-ink-faint)] hidden sm:inline">{c.commit.author.name} · {ago(Date.parse(c.commit.author.date))}</span>
              </li>))}</ul>
          </section>
        </div>
      )}
    </Page>
  )
}
