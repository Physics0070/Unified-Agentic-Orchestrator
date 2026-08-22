import type { RunOutput } from './api'
import { Badge, Empty } from './ui'

/** A finding shaped like the demo/test convention used across this repo
 * (backend/demo.py's FIXTURES, tests/test_engine.py's SCHEMA): a title and a
 * severity, optionally a recommendation. Rendered specially when present;
 * any other JSON shape falls back to a plain pretty-printed block. */
interface Finding {
  title?: unknown
  severity?: unknown
  recommendation?: unknown
  [key: string]: unknown
}

function severityTone(s: unknown): 'bad' | 'warn' | 'good' | 'default' {
  const v = String(s ?? '').toLowerCase()
  if (v === 'critical' || v === 'high') return 'bad'
  if (v === 'medium') return 'warn'
  if (v === 'low') return 'good'
  return 'default'
}

function isFindingsShaped(content: unknown): content is { findings: Finding[] } {
  return (
    !!content && typeof content === 'object' &&
    Array.isArray((content as { findings?: unknown }).findings)
  )
}

function FindingsList({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return <p className="text-[11px] text-[var(--color-ink-faint)]">No findings.</p>
  }
  return (
    <ul className="space-y-1.5">
      {findings.map((f, i) => (
        <li key={i} className="neo-raised rounded-lg px-2.5 py-2">
          <div className="flex items-start gap-2">
            {f.severity != null && <Badge tone={severityTone(f.severity)}>{String(f.severity)}</Badge>}
            <span className="text-[12px] text-[var(--color-ink)] leading-snug">
              {f.title != null ? String(f.title) : JSON.stringify(f)}
            </span>
          </div>
          {f.recommendation != null && (
            <p className="mt-1 text-[11px] text-[var(--color-ink-faint)] leading-relaxed">
              {String(f.recommendation)}
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * The answer to the goal itself, not just how it was produced - one entry
 * per sink node (nothing else in the graph depends on it; see
 * GraphSpec.sink_node_ids()), read back from the artifact store via
 * GET /api/runs/{run_id}/output. Shown alongside the graph, not behind a
 * separate tab, so approving a graph and reading its result are the same view.
 */
export function FinalOutput({ output }: { output: RunOutput }) {
  if (output.outputs.length === 0) {
    return (
      <div className="glass shrink-0 border-t border-[var(--glass-border)] px-3 py-2.5">
        <h3 className="text-[10px] uppercase tracking-[0.07em] text-[var(--color-ink-faint)] mb-1.5">
          Output
        </h3>
        <Empty icon="…">
          {output.status === 'running' || output.status === 'pending'
            ? 'Still running - the report fills in once the graph’s final node completes.'
            : 'This run produced no output at its sink node(s).'}
        </Empty>
      </div>
    )
  }

  return (
    <div className="glass shrink-0 border-t border-[var(--glass-border)] px-3 py-2.5 max-h-[280px] overflow-auto">
      <h3 className="text-[10px] uppercase tracking-[0.07em] text-[var(--color-ink-faint)] mb-1.5">
        Output · the answer to the goal
      </h3>
      <div className="space-y-3">
        {output.outputs.map((o) => (
          <div key={o.node_id ?? o.written_by}>
            <div className="flex items-center gap-1.5 mb-1.5">
              <Badge tone="accent">{o.node_id ?? o.written_by}</Badge>
              <span className="text-[10px] text-[var(--color-ink-faint)] font-mono">
                written by {o.written_by}
              </span>
            </div>
            {isFindingsShaped(o.content) ? (
              <FindingsList findings={o.content.findings} />
            ) : (
              <pre className="neo-inset text-[11px] text-[var(--color-ink-muted)] whitespace-pre-wrap break-words rounded-lg px-2.5 py-2">
                {JSON.stringify(o.content, null, 2)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
