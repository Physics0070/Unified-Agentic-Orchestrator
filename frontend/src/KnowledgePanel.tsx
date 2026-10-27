import { useEffect, useRef, useState } from 'react'
import { api, type KnowledgeQueryResult, type KnowledgeSummary } from './api'
import { Badge, Button, Panel, SkeletonRows } from './ui'

const INPUT =
  'min-h-10 px-3 rounded-[10px] bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[13px] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)] outline-none transition-colors'

const n = (v: number | null | undefined) => (v == null ? '-' : v.toFixed(3))

export function KnowledgePanel() {
  const [info, setInfo] = useState<KnowledgeSummary | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [drag, setDrag] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<'hybrid' | 'keyword' | 'semantic'>('hybrid')
  const [rerank, setRerank] = useState(true)
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<KnowledgeQueryResult | null>(null)
  const [qError, setQError] = useState<string | null>(null)

  useEffect(() => {
    api.knowledgeDocs().then(setInfo).catch((e) => setErrors([(e as Error).message]))
  }, [])

  const upload = async (files: FileList | File[]) => {
    setUploading(true); setErrors([])
    const errs: string[] = []
    for (const f of Array.from(files)) {
      try { setInfo(await api.uploadDocument(f)) } catch (e) { errs.push(`${f.name}: ${(e as Error).message}`) }
    }
    setErrors(errs); setUploading(false)
    if (input.current) input.current.value = ''
  }

  const remove = async (id: string) => {
    try { setInfo(await api.deleteDocument(id)) } catch (e) { setErrors([(e as Error).message]) }
  }

  const search = async () => {
    setSearching(true); setQError(null)
    try { setResult(await api.knowledgeQuery(query, { mode, rerank, top_k: 5 })) }
    catch (e) { setQError((e as Error).message) } finally { setSearching(false) }
  }

  return (
    <div className="h-full overflow-auto p-3">
      <div className="mx-auto max-w-4xl space-y-3">
        {info && (
          <div className="rounded-[10px] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2 text-[12px] text-[var(--color-ink-muted)] flex items-center gap-2 flex-wrap">
            <Badge tone={info.persistent ? 'good' : 'warn'}>{info.persistent ? 'persistent' : 'in memory'}</Badge>
            <span>
              {info.persistent
                ? 'Documents are stored on the server.'
                : 'Documents are held in memory only and are lost when the server restarts.'}
            </span>
            <Badge tone="info">{info.semantic_backend}</Badge>
          </div>
        )}

        <Panel title="Documents" subtitle={info ? `${info.documents.length} files · ${info.chunks} chunks` : undefined}>
          <div className="p-3 space-y-3">
            <div
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files) }}
              onClick={() => input.current?.click()}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.current?.click() } }}
              role="button" tabIndex={0} aria-label="Upload documents"
              className={`grid place-items-center min-h-24 rounded-[10px] border border-dashed cursor-pointer text-center p-4 text-[12px] transition-colors ${
                drag
                  ? 'border-[var(--color-accent)] bg-[#5eead418]'
                  : 'border-[var(--color-line-strong)] text-[var(--color-ink-faint)] hover:border-[var(--color-accent)]'
              }`}
            >
              <input
                ref={input} type="file" multiple hidden
                accept={info?.supported.join(',')}
                onChange={(e) => e.target.files && upload(e.target.files)}
              />
              <div>
                <p className="text-[var(--color-ink-muted)]">
                  {uploading ? 'Uploading...' : 'Drop files here or click to upload'}
                </p>
                {info && (
                  <p className="mt-1">
                    {info.supported.join(' ')} · max {(info.max_bytes / 1048576).toFixed(0)} MB
                  </p>
                )}
              </div>
            </div>

            {errors.map((e, i) => (
              <p key={i} role="alert" className="text-[12px] text-[var(--color-failed)] break-words">{e}</p>
            ))}

            {!info ? <SkeletonRows n={3} /> : info.documents.length === 0 ? (
              <p className="text-[12px] text-[var(--color-ink-faint)]">No documents yet.</p>
            ) : (
              <ul className="divide-y divide-[var(--color-line)]">
                {info.documents.map((d) => (
                  <li key={d.doc_id} className="flex items-center gap-2 py-2 text-[12px]">
                    <span className="flex-1 min-w-0 truncate">{d.name}</span>
                    <Badge>{d.chunks} chunks</Badge>
                    <Badge>{d.chars.toLocaleString()} chars</Badge>
                    <button
                      onClick={() => remove(d.doc_id)} aria-label={`Delete ${d.name}`}
                      className="px-2 h-8 rounded-md text-[var(--color-ink-faint)] hover:text-[var(--color-failed)]"
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <Panel title="Test retrieval" subtitle="See what the chat would retrieve">
          <div className="p-3 space-y-3">
            <form
              className="flex items-center gap-2 flex-wrap"
              onSubmit={(e) => { e.preventDefault(); if (query.trim()) search() }}
            >
              <input
                value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Query"
                placeholder="Ask something your documents answer" className={`${INPUT} flex-1 min-w-[220px]`}
              />
              <select
                value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}
                aria-label="Retrieval mode" className={INPUT}
              >
                {(['hybrid', 'keyword', 'semantic'] as const).map((m) => <option key={m}>{m}</option>)}
              </select>
              <label className="inline-flex items-center gap-1.5 min-h-10 text-[12px] text-[var(--color-ink-muted)]">
                <input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} />
                rerank
              </label>
              <Button type="submit" variant="primary" loading={searching} disabled={!query.trim()}>Search</Button>
            </form>
            {qError && <p role="alert" className="text-[12px] text-[var(--color-failed)]">{qError}</p>}
            {result && (
              <div className="space-y-2">
                <p className="text-[11px] text-[var(--color-ink-faint)]">
                  {result.hits.length} hits · {result.documents} docs · {result.chunks} chunks · {result.mode}
                  {result.reranked && ' · reranked'}
                </p>
                {result.hits.map((h, i) => (
                  <article key={`${h.doc_id}-${h.ordinal}`} className="rounded-[10px] border border-[var(--color-line)] bg-[var(--color-surface-2)] p-3 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap text-[12px]">
                      <span className="text-[var(--color-ink-faint)]">#{i + 1}</span>
                      <span className="font-semibold truncate">{h.doc_name}</span>
                      <Badge>part {h.ordinal + 1}</Badge>
                      <Badge tone="accent">{h.score.toFixed(3)}</Badge>
                    </div>
                    <p className="text-[12px] leading-relaxed text-[var(--color-ink-muted)] line-clamp-5 whitespace-pre-wrap">
                      {h.text}
                    </p>
                    <div className="flex gap-1.5 flex-wrap">
                      <Badge>bm25 {n(h.scores.bm25)}{h.ranks.bm25 != null && ` (#${h.ranks.bm25})`}</Badge>
                      <Badge>semantic {n(h.scores.semantic)}{h.ranks.semantic != null && ` (#${h.ranks.semantic})`}</Badge>
                      <Badge>fused {n(h.scores.fused)}</Badge>
                      <Badge>rerank {n(h.scores.rerank)}</Badge>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </Panel>
      </div>
    </div>
  )
}
