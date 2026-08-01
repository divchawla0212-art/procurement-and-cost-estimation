import type { Coverage } from '../types'
import { pct } from '../matrixUtils'

export function CoverageStrip({ coverage }: { coverage: Coverage }) {
  const checked = coverage.auto_cells
  if (!checked) {
    return (
      <section className="coverage">
        <p className="muted">No machine-checkable requirements are stored yet.</p>
      </section>
    )
  }

  const pass = coverage.by_verdict.pass ?? 0
  const fail = coverage.by_verdict.fail ?? 0
  const unanswered = coverage.by_verdict.unanswered ?? 0

  return (
    <section className="coverage">
      <h2>Coverage</h2>
      <div className="coverage-metrics">
        <div className="metric">
          <span className="metric-label">Machine-checkable cells</span>
          <span className="metric-value">{checked}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Pass</span>
          <span className="metric-value verdict-pass">
            {pass} <small>({pct(pass, checked)})</small>
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">Fail</span>
          <span className="metric-value verdict-fail">
            {fail} <small>({pct(fail, checked)})</small>
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">Unanswered</span>
          <span className="metric-value verdict-unanswered">
            {unanswered} <small>({pct(unanswered, checked)})</small>
          </span>
        </div>
      </div>
      {(coverage.unanswered_silent > 0 || coverage.unanswered_refused > 0) && (
        <ul className="coverage-notes">
          <li>
            <strong>{coverage.unanswered_silent}</strong> unanswered because no
            vendor document stated the parameter — the real coverage gap.
          </li>
          <li>
            <strong>{coverage.unanswered_refused}</strong> unanswered because the
            fact was found and the comparison could not be made — measures our
            reach, not the vendor&apos;s answer.
          </li>
        </ul>
      )}
      <p className="muted caption">
        Counted over machine-checkable requirements only. A percentage over every
        requirement would be dominated by human-judgement clauses.
      </p>
    </section>
  )
}
