import { Fragment, useState } from 'react'
import type { JSX } from 'react'
import type { GroupKey, MatrixRow } from '../types'
import { fetchComplianceMatrix } from '../api'
import { useAsync } from '../useAsync'
import { GROUP_LABEL, VERDICT_LABEL, VERDICTS, bound, pct } from '../constants'
import {
  Card,
  CoverageInstrument,
  EmptyState,
  ErrorState,
  LoadingState,
  Metric,
  PageHeader,
  VerdictTag,
} from '../components/primitives'

export interface ComplianceMatrixProps {
  slug: string
  projectName: string
}

const GROUP_ORDER: GroupKey[] = ['not_matched', 'needs_human', 'matched']

/**
 * A row is kept when it matches the free-text search AND (when any verdict is
 * selected) at least one cell — restricted to the chosen vendor if a vendor
 * filter is set — carries a selected verdict.
 */
function rowMatches(
  row: MatrixRow,
  query: string,
  verdicts: Set<string>,
  vendor: string,
): boolean {
  if (query) {
    const q = query.toLowerCase()
    const haystack: string[] = [row.clause_ref, row.text, bound(row)]
    for (const cell of Object.values(row.cells)) {
      haystack.push(cell.verdict, cell.rationale)
    }
    if (!haystack.some((piece) => (piece ?? '').toLowerCase().includes(q))) {
      return false
    }
  }
  if (verdicts.size > 0) {
    const cells = vendor
      ? row.cells[vendor]
        ? [row.cells[vendor]]
        : []
      : Object.values(row.cells)
    if (!cells.some((cell) => verdicts.has(cell.verdict))) return false
  }
  return true
}

/** Per-vendor verdict + verbatim rationale, shared by both views. */
function CellList({ row, vendors }: { row: MatrixRow; vendors: string[] }) {
  return (
    <ul className="wl-cells">
      {vendors.map((vendor) => {
        const cell = row.cells[vendor]
        if (!cell) return null
        return (
          <li key={vendor} className={`wl-cell ${cell.verdict}`}>
            <span className="vendor">{vendor}</span>
            <VerdictTag verdict={cell.verdict} />
            {cell.rationale && <p className="rationale">{cell.rationale}</p>}
          </li>
        )
      })}
    </ul>
  )
}

function WorklistRow({ row, vendors }: { row: MatrixRow; vendors: string[] }) {
  return (
    <div className="wl-row">
      <div className="head">
        <span className="clause">{row.clause_ref}</span>
        <span className="bound">{bound(row)}</span>
      </div>
      {(row.checkability === 'auto' || row.checkability === 'stated') && (
        <p className="rationale">{row.text}</p>
      )}
      <CellList row={row} vendors={vendors} />
    </div>
  )
}

export function ComplianceMatrix(props: ComplianceMatrixProps): JSX.Element {
  const { data, error, loading } = useAsync(
    () => fetchComplianceMatrix(props.slug),
    [props.slug],
  )
  const [view, setView] = useState<'worklist' | 'grid'>('worklist')
  const [query, setQuery] = useState('')
  const [verdicts, setVerdicts] = useState<Set<string>>(new Set())
  const [vendor, setVendor] = useState('')
  const [matchedOpen, setMatchedOpen] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  if (loading) return <LoadingState label="Building compliance matrix…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No compliance matrix was returned." />

  const { vendors, rows, coverage, groups } = data

  if (rows.length === 0) {
    return (
      <EmptyState glyph="⟲" title="No matrix yet">
        There are no stored requirements to compare. Run ingestion in the
        Streamlit portal to build the compliance matrix; this screen is
        read-only.
      </EmptyState>
    )
  }
  if (vendors.length === 0) {
    return (
      <EmptyState title="No vendors">
        The project has no vendors, so there is nothing to compare.
      </EmptyState>
    )
  }

  function toggleVerdict(value: string) {
    setVerdicts((prev) => {
      const next = new Set(prev)
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return next
    })
  }

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const predicate = (row: MatrixRow) => rowMatches(row, query, verdicts, vendor)
  const filteredRows = rows.filter(predicate)

  const checked = coverage.auto_cells + coverage.stated_cells
  const passCount = coverage.by_verdict['pass'] ?? 0
  const failCount = coverage.by_verdict['fail'] ?? 0
  const unansweredCount = coverage.by_verdict['unanswered'] ?? 0
  // `unanswered_silent`/`unanswered_refused` only tally `auto` cells; a
  // `stated` cell is `unanswered` for exactly the "silent" reason (no vendor
  // document named the parameter), so the remainder is that stated-tier count.
  const statedUnanswered =
    unansweredCount - coverage.unanswered_silent - coverage.unanswered_refused
  const hasUnansweredNote =
    coverage.unanswered_silent > 0 ||
    coverage.unanswered_refused > 0 ||
    statedUnanswered > 0

  return (
    <>
      <PageHeader
        eyebrow="Compliance"
        title={props.projectName}
        sub="Every stored verdict, grouped by the action it needs. Rationale is shown verbatim."
      />

      <Card title="Coverage">
        <CoverageInstrument coverage={coverage} />

        {checked > 0 && (
          <div className="metrics" style={{ marginTop: '1rem' }}>
            <Metric k="Checked cells" v={checked} />
            <Metric k="Pass" v={passCount} sub={pct(passCount, checked)} />
            <Metric k="Fail" v={failCount} sub={pct(failCount, checked)} />
            <Metric
              k="Unanswered"
              v={unansweredCount}
              sub={pct(unansweredCount, checked)}
            />
          </div>
        )}

        {hasUnansweredNote && (
          <p
            className="muted"
            style={{ marginTop: '0.85rem', fontSize: '0.83rem' }}
          >
            Of the unanswered cells, <b>{coverage.unanswered_silent}</b> are
            silent because no vendor document stated the parameter — the real
            coverage gap — and <b>{coverage.unanswered_refused}</b> because the
            fact was found but the comparison could not be made, which measures
            our reach, not the vendor's answer.
            {statedUnanswered > 0 && (
              <>
                {' '}
                Another <b>{statedUnanswered}</b> are on a stated requirement
                where no vendor document named the parameter at all.
              </>
            )}
          </p>
        )}
      </Card>

      <div className="toolbar" style={{ marginTop: '1.4rem' }}>
        <div className="seg" role="group" aria-label="View mode">
          <button
            type="button"
            className={view === 'worklist' ? 'on' : ''}
            aria-pressed={view === 'worklist'}
            onClick={() => setView('worklist')}
          >
            Worklist
          </button>
          <button
            type="button"
            className={view === 'grid' ? 'on' : ''}
            aria-pressed={view === 'grid'}
            onClick={() => setView('grid')}
          >
            Full grid
          </button>
        </div>

        <input
          className="field"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search clause, requirement, rationale…"
          aria-label="Search clause, requirement, or rationale"
          spellCheck={false}
        />

        {VERDICTS.map((v) => {
          const on = verdicts.has(v)
          return (
            <button
              key={v}
              type="button"
              className={'chip' + (on ? ' on' : '')}
              aria-pressed={on}
              onClick={() => toggleVerdict(v)}
            >
              {VERDICT_LABEL[v]}
            </button>
          )
        })}

        <span className="spacer" />

        <select
          className="field"
          value={vendor}
          onChange={(e) => setVendor(e.target.value)}
          aria-label="Filter by vendor"
        >
          <option value="">All vendors</option>
          {vendors.map((vn) => (
            <option key={vn} value={vn}>
              {vn}
            </option>
          ))}
        </select>
      </div>

      {view === 'worklist' ? (
        <div className="worklist">
          {GROUP_ORDER.map((g) => {
            const groupRows = groups[g].filter(predicate)
            const heading = `${GROUP_LABEL[g]} (${groupRows.length})`

            if (g === 'matched') {
              return (
                <Card
                  key={g}
                  title={
                    <button
                      type="button"
                      aria-expanded={matchedOpen}
                      onClick={() => setMatchedOpen((open) => !open)}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '0.45rem',
                        background: 'transparent',
                        border: 0,
                        padding: 0,
                        font: 'inherit',
                        color: 'inherit',
                        cursor: 'pointer',
                      }}
                    >
                      <span aria-hidden>{matchedOpen ? '▾' : '▸'}</span>
                      {heading}
                    </button>
                  }
                >
                  {matchedOpen ? (
                    groupRows.length ? (
                      groupRows.map((row) => (
                        <WorklistRow
                          key={row.req_id}
                          row={row}
                          vendors={vendors}
                        />
                      ))
                    ) : (
                      <p className="muted">Nothing here.</p>
                    )
                  ) : null}
                </Card>
              )
            }

            return (
              <Card key={g} title={heading}>
                {groupRows.length ? (
                  groupRows.map((row) => (
                    <WorklistRow key={row.req_id} row={row} vendors={vendors} />
                  ))
                ) : (
                  <p className="muted">Nothing here.</p>
                )}
              </Card>
            )
          })}
        </div>
      ) : (
        <div className="tbl-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="sticky-col" scope="col">
                  Clause
                </th>
                <th scope="col">Requirement</th>
                {vendors.map((vn) => (
                  <th key={vn} scope="col">
                    {vn}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 ? (
                <tr>
                  <td className="muted" colSpan={vendors.length + 2}>
                    No rows match the current filters.
                  </td>
                </tr>
              ) : (
                filteredRows.map((row) => {
                  const open = expanded.has(row.req_id)
                  return (
                    <Fragment key={row.req_id}>
                      <tr>
                        <td className="sticky-col">
                          <button
                            type="button"
                            className="clause"
                            aria-expanded={open}
                            onClick={() => toggleExpanded(row.req_id)}
                            style={{
                              background: 'transparent',
                              border: 0,
                              padding: 0,
                              cursor: 'pointer',
                              textAlign: 'left',
                            }}
                          >
                            {row.clause_ref}
                          </button>
                        </td>
                        <td title={row.text}>{bound(row)}</td>
                        {vendors.map((vn) => {
                          const cell = row.cells[vn]
                          return (
                            <td
                              key={vn}
                              className={
                                cell
                                  ? `verdict-cell vc-${cell.verdict}`
                                  : 'verdict-cell'
                              }
                            >
                              <span className="mono">
                                {cell
                                  ? VERDICT_LABEL[cell.verdict] ?? cell.verdict
                                  : '—'}
                              </span>
                            </td>
                          )
                        })}
                      </tr>
                      {open && (
                        <tr className="detail-row">
                          <td colSpan={vendors.length + 2}>
                            <CellList row={row} vendors={vendors} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
