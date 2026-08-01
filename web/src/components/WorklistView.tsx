import { useState } from 'react'
import type { MatrixRow } from '../types'
import { GROUP_LABELS, bound } from '../matrixUtils'

interface Props {
  groups: Record<string, MatrixRow[]>
  vendors: string[]
}

function RowDetail({ row, vendors }: { row: MatrixRow; vendors: string[] }) {
  return (
    <article className="worklist-row">
      <header>
        <span className="clause">{row.clause_ref}</span>
        <span className="bound">{bound(row)}</span>
      </header>
      {row.checkability === 'auto' && <p className="row-text">{row.text}</p>}
      <ul className="cell-list">
        {vendors.map((vendor) => {
          const cell = row.cells[vendor]
          if (!cell) return null
          return (
            <li key={vendor} className={`cell-item verdict-${cell.verdict}`}>
              <span className="vendor-name">{vendor}</span>
              <span className={`verdict-tag verdict-${cell.verdict}`}>
                {cell.verdict}
              </span>
              {cell.rationale && (
                <p className="rationale">{cell.rationale}</p>
              )}
            </li>
          )
        })}
      </ul>
    </article>
  )
}

export function WorklistView({ groups, vendors }: Props) {
  const [matchedOpen, setMatchedOpen] = useState(false)
  const order = ['not_matched', 'needs_human', 'matched'] as const

  return (
    <div className="worklist">
      {order.map((key) => {
        const rows = groups[key] ?? []
        if (key === 'matched') {
          return (
            <section key={key} className="worklist-section">
              <button
                type="button"
                className="section-toggle"
                onClick={() => setMatchedOpen((o) => !o)}
                aria-expanded={matchedOpen}
              >
                {GROUP_LABELS[key]} ({rows.length})
                <span>{matchedOpen ? '▾' : '▸'}</span>
              </button>
              {matchedOpen &&
                (rows.length === 0 ? (
                  <p className="muted">Nothing here.</p>
                ) : (
                  rows.map((row) => (
                    <RowDetail key={row.req_id} row={row} vendors={vendors} />
                  ))
                ))}
            </section>
          )
        }
        return (
          <section key={key} className="worklist-section">
            <h3>
              {GROUP_LABELS[key]} ({rows.length})
            </h3>
            {rows.length === 0 ? (
              <p className="muted">Nothing here.</p>
            ) : (
              rows.map((row) => (
                <RowDetail key={row.req_id} row={row} vendors={vendors} />
              ))
            )}
          </section>
        )
      })}
    </div>
  )
}
