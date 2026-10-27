import { useMemo, useState } from 'react'
import { API_BASE } from './api'
import { FinalOutput } from './RunOutputPanel'
import { ago, download, readChat, slug, toMarkdown, type RunRecord } from './store'
import { Badge, Button, Empty, Icon, Page } from './ui'

interface Item {
  key: string
  kind: 'report' | 'file'
  title: string
  detail: string
  ts: number
  run?: RunRecord
  node: string | null
  content?: unknown
  href?: string
  fileKind?: string
}

export function ArtifactsView({ runs, onOpenRun, onDrop, toast }: {
  runs: RunRecord[]
  onOpenRun: (r: RunRecord) => void
  onDrop: (runId: string, node: string | null) => void
  toast: (t: string, tone?: 'ok' | 'warn') => void
}) {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<'all' | 'report' | 'file'>('all')
  const [open, setOpen] = useState<string | null>(null)

  const items = useMemo<Item[]>(() => {
    const out: Item[] = []
    for (const r of runs) for (const o of r.output?.outputs ?? []) {
      out.push({ key: `${r.id}:${o.node_id}`, kind: 'report', title: r.goal, ts: r.ts,
        detail: `Run output · ${o.written_by}${o.node_id ? ` · ${o.node_id}` : ''}`, run: r, node: o.node_id, content: o.content })
    }
    for (const c of readChat()) for (const t of c.turns) if (t.file) {
      out.push({ key: `file:${t.file.file_id}`, kind: 'file', title: t.file.filename, ts: c.updatedAt,
        detail: `Chat file · ${t.file.kind.toUpperCase()} · ${c.title}`, node: null,
        href: `${API_BASE}/api/chat/files/${t.file.file_id}`, fileKind: t.file.kind })
    }
    return out.sort((a, b) => b.ts - a.ts)
  }, [runs])

  const shown = items.filter((i) =>
    (kind === 'all' || i.kind === kind) && `${i.title} ${i.detail}`.toLowerCase().includes(q.toLowerCase()))

  const copy = async (i: Item) => {
    try { await navigator.clipboard.writeText(JSON.stringify(i.content, null, 2)); toast('Copied as JSON') }
    catch { toast('Clipboard is blocked here: use Download', 'warn') }
  }

  return (
    <Page title="Artifacts" sub="Everything your runs and chats produced, kept in this browser. Nothing here is uploaded anywhere.">
      <div className="flex items-center gap-2 flex-wrap mb-5">
        <div className="relative flex-1 min-w-[220px]">
          <Icon name="search" size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-ink-faint)]" />
          <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search artifacts" placeholder="search artifacts"
            className="w-full h-9 pl-9 pr-3 bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[12px] outline-none focus:border-[var(--color-ink)] placeholder:text-[var(--color-ink-faint)]" />
        </div>
        {(['all', 'report', 'file'] as const).map((k) => (
          <button key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
            className={`h-8 px-3 rounded-full border text-[11px] uppercase tracking-[0.07em] ${kind === k ? 'border-[var(--color-ink)] text-[var(--color-ink)]' : 'border-[var(--color-line)] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]'}`}>
            {k === 'all' ? `All ${items.length}` : k === 'report' ? 'Reports' : 'Files'}
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <Empty icon="▢" title={items.length ? 'Nothing matches' : 'No artifacts yet'}>
          {items.length ? 'Try a different search or filter.' : 'Run the orchestrator, or ask chat for a spreadsheet or document, and the result lands here.'}
        </Empty>
      ) : (
        <ul className="border-t border-[var(--color-line)]">
          {shown.map((i) => (
            <li key={i.key} className="border-b border-[var(--color-line)]">
              <div className="flex items-center gap-3 py-3">
                <Badge tone={i.kind === 'report' ? 'accent' : 'info'}>{i.kind}</Badge>
                <button onClick={() => i.kind === 'report' && setOpen(open === i.key ? null : i.key)} className="min-w-0 flex-1 text-left">
                  <p className="text-[13px] truncate">{i.title}</p>
                  <p className="font-mono text-[10px] text-[var(--color-ink-faint)] truncate">{i.detail} · {ago(i.ts)}</p>
                </button>
                {i.kind === 'report' ? (
                  <div className="flex items-center gap-1.5 shrink-0">
                    <Button size="sm" onClick={() => copy(i)}><Icon name="copy" size={12} /> JSON</Button>
                    <Button size="sm" onClick={() => download(`${slug(i.title)}.md`, toMarkdown(i.title, i.ts, i.node, i.content), 'text/markdown')}>
                      <Icon name="download" size={12} /> .md
                    </Button>
                    <Button size="sm" onClick={() => onOpenRun(i.run!)}>Open run</Button>
                    <Button size="sm" variant="danger" title="Remove this output from history" onClick={() => { onDrop(i.run!.id, i.node); toast('Output removed') }}>
                      <Icon name="trash" size={12} />
                    </Button>
                  </div>
                ) : (
                  <a href={i.href} target="_blank" rel="noreferrer" className="shrink-0 inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-[var(--color-line-strong)] text-[11px] uppercase tracking-[0.07em] hover:border-[var(--color-ink)]">
                    <Icon name="external" size={12} /> Open
                  </a>
                )}
              </div>
              {open === i.key && i.run?.output && (
                <div className="pb-4"><FinalOutput output={{ ...i.run.output, outputs: i.run.output.outputs.filter((o) => o.node_id === i.node) }} /></div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Page>
  )
}
