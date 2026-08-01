import { Fragment, useState } from 'react'
import type { MatrixRow } from '../types'
import { bound } from '../matrixUtils'

interface Props {
  rows: MatrixRow[]
  vendors: string[]
}

export function GridView({ rows, vendors }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (rows.length === 0) {
    return <p className="muted">No rows match the current filters.</p>
  }

  return (
    <div className="grid-wrap">
      <table className="matrix-grid">
        <thead>
          <tr>
            <th className="sticky-col">Clause</th>
            <th className="req-col">Requirement</th>
            {vendors.map((v) => (
              <th key={v}>{v}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const open = expanded === row.req_id
            return (
              <Fragment key={row.req_id}>
                <tr
                  className={open ? 'expanded' : ''}
                  onClick={() => setExpanded(open ? null : row.req_id)}
                >
                  <td className="sticky-col clause">{row.clause_ref}</td>
                  <td className="req-col" title={row.text}>
                    {bound(row)}
                  </td>
                  {vendors.map((v) => {
                    const cell = row.cells[v]
                    return (
                      <td
                        key={v}
                        className={`verdict-cell verdict-${cell?.verdict ?? 'unanswered'}`}
                      >
                        {cell?.verdict ?? '—'}
                      </td>
                    )
                  })}
                </tr>
                {open && (
                  <tr className="detail-row">
                    <td colSpan={2 + vendors.length}>
                      <p className="row-text">{row.text}</p>
                      <ul className="cell-list">
                        {vendors.map((vendor) => {
                          const cell = row.cells[vendor]
                          if (!cell) return null
                          return (
                            <li
                              key={vendor}
                              className={`cell-item verdict-${cell.verdict}`}
                            >
                              <span className="vendor-name">{vendor}</span>
                              <span
                                className={`verdict-tag verdict-${cell.verdict}`}
                              >
                                {cell.verdict}
                              </span>
                              {cell.rationale && (
                                <p className="rationale">{cell.rationale}</p>
                              )}
                            </li>
                          )
                        })}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
