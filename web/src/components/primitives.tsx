import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import type { Coverage } from '../types'
import { VERDICT_LABEL, VERDICTS, pct } from '../constants'

/* -------------------------------------------------------------- ApprovalPills */

/**
 * Who has approved a vendor, as one pill per approver.
 *
 * A component rather than a `.map` at each call site because three screens
 * render this same fact — the wizard's candidate card, its shortlist row, and
 * the item screen's vendor table — and three copies is three places to fix
 * when a fourth approver appears.
 *
 * The class is a **slug, not a lookup**, so an approver the stylesheet does not
 * name falls through to the neutral base rule instead of vanishing. A lookup
 * would have to decide what to do with a name it does not know, and the honest
 * answer — show it, uncoloured — is what the cascade gives for free.
 *
 * It renders identity, never a verdict. Whether an approval is *missing* is
 * the server's `client_approved`, and callers render that separately: a vendor
 * we have qualified and the client has not is both of those things at once.
 */
export function ApprovalPills({ approvers }: { approvers: string[] }) {
  return (
    <>
      {approvers.map((org) => (
        <span
          key={org}
          className={`approval-badge approval-badge--${org
            .toLowerCase()
            .replace(/\s+/g, '-')}`}
        >
          {org}
        </span>
      ))}
    </>
  )
}

/* -------------------------------------------------------------- PasswordField */

/**
 * A password input with a reveal toggle.
 *
 * Typing a password you cannot see is how typos become "wrong password" for
 * an account that was never wrong — worse here than on most sites, because an
 * admin sets a colleague's initial password and has to read it back to them.
 *
 * The toggle is never `disabled`, even while a form is submitting. A disabled
 * button leaves the tab order, which blurs it if it has focus and drops the
 * keyboard user to the top of the page; and revealing text you already typed
 * is harmless mid-request. It is `aria-pressed` with a fixed label rather
 * than a label that flips, so a screen reader announces one stable control
 * changing state instead of two controls trading places.
 */
export function PasswordField({
  id,
  value,
  onChange,
  autoComplete,
  minLength,
  required = false,
  disabled = false,
  className = 'input',
}: {
  id: string
  value: string
  onChange: (next: string) => void
  autoComplete: string
  minLength?: number
  required?: boolean
  disabled?: boolean
  /** The page's own input class — `input` and `field` differ in padding and
   *  width, and each screen already commits to one. */
  className?: string
}) {
  const [shown, setShown] = useState(false)
  return (
    <div className="pw">
      <input
        id={id}
        className={`${className} pw-input`}
        type={shown ? 'text' : 'password'}
        autoComplete={autoComplete}
        minLength={minLength}
        required={required}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="pw-eye"
        aria-label="Show password"
        aria-pressed={shown}
        onClick={() => setShown((s) => !s)}
        title={shown ? 'Hide password' : 'Show password'}
      >
        <EyeIcon open={shown} />
      </button>
    </div>
  )
}

function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      <path d="M1.8 12S5.4 5.4 12 5.4 22.2 12 22.2 12 18.6 18.6 12 18.6 1.8 12 1.8 12Z" />
      <circle cx="12" cy="12" r="3.2" />
      {/* The slash means "hidden", so it shows when the password is masked. */}
      {!open && <path d="M4 20 20 4" />}
    </svg>
  )
}

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

/* -------------------------------------------------------------- ExportLinks */

/**
 * Download controls for a page's document.
 *
 * Plain anchors, not fetch-to-blob: `/api` is same-origin under the container
 * and proxied by Vite in dev, so the browser performs the download itself, the
 * server's `Content-Disposition` names the file, and the workbook is never
 * buffered in JS memory. The cost is that a server error lands as a browser
 * error page rather than an in-app `ErrorState` — worth it for these two,
 * which read from a store the page has already loaded successfully.
 */
export function ExportLinks({
  base,
  csvTitle,
}: {
  base: string
  csvTitle?: string
}) {
  return (
    <div style={{ display: 'flex', gap: '0.5rem' }}>
      <a className="btn btn-sm" href={`${base}?format=xlsx`} download>
        <span aria-hidden>⤓</span> Excel
      </a>
      <a className="btn btn-sm" href={`${base}?format=csv`} download title={csvTitle}>
        <span aria-hidden>⤓</span> CSV
      </a>
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
  // A `<section>` is only a landmark once it has an accessible name, so the
  // heading is wired to it rather than merely sitting inside it. Without this
  // every card is an anonymous div to a screen reader — and untargetable by a
  // test that wants to assert *within* one card rather than the whole page.
  const headingId = useId()
  return (
    <section className="card" aria-labelledby={title ? headingId : undefined}>
      {(title || actions) && (
        <header className="card-head">
          {title ? <h2 id={headingId}>{title}</h2> : <span />}
          {actions}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  )
}

/* --------------------------------------------------------------- Breadcrumb */

/**
 * The trail back out of a drill-down.
 *
 * A single "Back to project" button sitting in the header's action row was the
 * whole of this before, and it read as a third action next to Edit and Delete
 * rather than as a way out — reported as "I am not able to come back". A trail
 * says two things a button cannot: where you are in the hierarchy, and that
 * every level above you is reachable in one click. From an item that means the
 * project *and* the roster, so switching to a different item is two clicks
 * rather than a rebuild of the route.
 *
 * `<nav>` + `aria-label` because this is landmark navigation, and the last
 * crumb is plain text with `aria-current="page"` — it is where you already are,
 * so it must not be a control.
 */
export function Breadcrumb({
  trail,
}: {
  /** Ancestors first, current page last. Only the last may omit `onClick`. */
  trail: Array<{ label: string; onClick?: () => void }>
}) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <ol>
        {trail.map((crumb, i) => {
          const last = i === trail.length - 1
          return (
            <li key={`${crumb.label}-${i}`}>
              {crumb.onClick && !last ? (
                <button type="button" className="crumb-link" onClick={crumb.onClick}>
                  {crumb.label}
                </button>
              ) : (
                <span aria-current={last ? 'page' : undefined}>{crumb.label}</span>
              )}
              {!last && (
                <span className="crumb-sep" aria-hidden>
                  /
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
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
