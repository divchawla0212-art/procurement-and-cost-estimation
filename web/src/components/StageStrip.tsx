import type { StageStripProps } from '../types'

/**
 * Horizontal strip of the nine workflow stages.
 *
 * Reflects state — it does not set it. Nothing here is clickable, and that is
 * deliberate: an RFQ advances only when the server's gate for that edge
 * passes, so a stage that looked like a button would be a button that mostly
 * refuses. The stage vocabulary is the server's too; this renders whatever
 * `/api/workflow/stages` returns rather than hard-coding nine labels that
 * would drift from `workflow/stages.py`.
 *
 * `current` is the stage an RFQ sits at. Omit it on a roster view, where the
 * strip is a set of counts rather than one RFQ's position.
 *
 * `codes` are references into the client's process document, not ordinals —
 * `RFQ-04A` has no position to be computed from, and Negotiation and Awarded
 * deliberately share `RFQ-06`. The server is the one place that vocabulary
 * lives, so the strip renders whatever it sent rather than numbering stages
 * itself.
 */
export function StageStrip({ stages, counts, codes, current }: StageStripProps) {
  const currentIndex = current ? stages.indexOf(current) : -1

  return (
    <ol className="stagestrip" aria-label="RFQ stages">
      {stages.map((stage, i) => {
        const isCurrent = i === currentIndex
        const isDone = currentIndex >= 0 && i < currentIndex
        const count = counts[stage] ?? 0
        const cls = ['stagestep', isCurrent && 'is-current', isDone && 'is-done']
          .filter(Boolean)
          .join(' ')

        return (
          <li key={stage} className={cls} aria-current={isCurrent ? 'step' : undefined}>
            <span className="stagestep-code mono">
              {codes[stage] ?? ''}
              {/* The tick is decorative — `aria-current` already tells a
                  screen reader where the RFQ is, and "✓" read aloud on every
                  earlier stage is noise. */}
              {isDone ? <span aria-hidden="true"> ✓</span> : null}
            </span>
            <span className="stagestep-name">{stage}</span>
            <span className="stagestep-count">{count}</span>
          </li>
        )
      })}
    </ol>
  )
}
