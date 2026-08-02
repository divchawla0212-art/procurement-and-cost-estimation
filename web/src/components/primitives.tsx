import type { ReactNode } from 'react'
import type { Coverage } from '../types'
import { VERDICT_LABEL, VERDICTS, pct } from '../constants'

/* --------------------------------------------------------------- VerdictTag */

export function VerdictTag({ verdict }: { verdict: string }) {
  return (
    <span className={`vtag vtag--${verdict}`}>
      {VERDICT_LABEL[verdict] ?? verdict}
    </span>
  )
}

/* -------------------------------------------- signature: CoverageInstrument */

/**
 * The segmented gauge that is this product's visual signature. Segments are
 * proportioned over checked cells (`auto` + `stated`) — the same basis the
 * backend Coverage model uses — so the bar never mixes judgement clauses in.
 */
export function CoverageInstrument({
  coverage,
  compact = false,
}: {
  coverage: Coverage
  compact?: boolean
}) {
  const total = coverage.auto_cells + coverage.stated_cells
  if (!total) {
    return (
      <p className="muted mono" style={{ fontSize: '0.8rem', margin: 0 }}>
        No checked cells yet.
      </p>
    )
  }

  const segments = VERDICTS.map((v) => ({
    verdict: v,
    count: coverage.by_verdict[v] ?? 0,
  })).filter((s) => s.count > 0)

  return (
    <div className="cov">
      <div className="cov-bar" role="img" aria-label="Coverage by verdict">
        {segments.map((s) => (
          <div
            key={s.verdict}
            className={`cov-seg ${s.verdict}`}
            style={{ width: `${(s.count / total) * 100}%` }}
            title={`${VERDICT_LABEL[s.verdict]}: ${s.count} (${pct(s.count, total)})`}
          />
        ))}
      </div>
      {!compact && (
        <div className="cov-legend">
          {segments.map((s) => (
            <span className="lg" key={s.verdict}>
              <span className={`sw ${s.verdict}`} />
              {VERDICT_LABEL[s.verdict]} <b>{s.count}</b>
              <span className="muted">{pct(s.count, total)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------- Metric */

export function Metric({
  k,
  v,
  sub,
}: {
  k: string
  v: ReactNode
  sub?: ReactNode
}) {
  return (
    <div className="metric">
      <div className="k">{k}</div>
      <div className="v">
        {v} {sub != null && <small>{sub}</small>}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------------- Card */

export function Card({
  title,
  actions,
  children,
  bodyClass = 'card-body',
}: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  bodyClass?: string
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  )
}

/* --------------------------------------------------------------- PageHeader */

export function PageHeader({
  eyebrow,
  title,
  sub,
  actions,
}: {
  eyebrow: string
  title: ReactNode
  sub?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="page-head">
      <p className="eyebrow">{eyebrow}</p>
      <div
        style={{
          display: 'flex',
          gap: '1rem',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
        }}
      >
        <h1>{title}</h1>
        {actions}
      </div>
      {sub && <p className="sub">{sub}</p>}
    </header>
  )
}

/* ------------------------------------------------------- state placeholders */

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state">
      <div className="glyph skeleton" aria-hidden />
      <p className="muted mono">{label}</p>
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="state error">
      <div className="glyph" aria-hidden>
        !
      </div>
      <h2>Could not load</h2>
      <p>{message}</p>
    </div>
  )
}

export function EmptyState({
  glyph = '∅',
  title,
  children,
}: {
  glyph?: string
  title: string
  children?: ReactNode
}) {
  return (
    <div className="state">
      <div className="glyph" aria-hidden>
        {glyph}
      </div>
      <h2>{title}</h2>
      {children && <p>{children}</p>}
    </div>
  )
}
