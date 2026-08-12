import { Fragment } from 'react'
import type { JSX } from 'react'
import type { Statement, StatementRow } from '../types'
import { fetchStatement, statementExportUrl } from '../api'
import { useAsync } from '../useAsync'
import { formatMoney } from '../constants'
import {
  Card,
  EmptyState,
  ErrorState,
  ExportLinks,
  LoadingState,
  PageHeader,
} from '../components/primitives'

export interface ComparativeStatementProps {
  slug: string
  projectName: string
  status: string
}

/** Rows carrying the awarded totals — bolded and shaded as the eye's anchor. */
const EMPHASISED = new Set(['final_value', 'normalised'])

function StatusTag({ status }: { status: string }): JSX.Element {
  if (status === 'ok') return <span className="vtag vtag--pass">ok</span>
  if (status === 'failed') return <span className="vtag vtag--fail">failed</span>
  if (status === 'missing')
    return <span className="vtag vtag--unanswered">missing</span>
  return <span className="vtag vtag--unanswered">{status}</span>
}

export function ComparativeStatement(
  props: ComparativeStatementProps,
): JSX.Element {
  const { data, error, loading } = useAsync<Statement>(
    () => fetchStatement(props.slug),
    [props.slug],
  )

  if (loading) return <LoadingState label="Assembling comparative statement…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No statement returned." />

  // BUG-001 (BUGS_TRACKER.md), design spec §1.1: `done_with_failures` admits
  // the user rather than blocking the screen, but says so — it does not name
  // the failed documents, since that means fetching `/extraction-status` from
  // a screen that does not otherwise need it. `05 Extraction status` is one
  // click away and already reports exactly which documents failed.
  //
  // `status === 'failed'` is reachable here too (I2, final-review report) —
  // see the matching comment in `ComplianceMatrix.tsx` for why it needs its
  // own wording rather than reusing the partial-run banner's text.
  const partialRunBanner =
    props.status === 'done_with_failures' ? (
      <div className="banner banner--warn" style={{ marginBottom: '1.2rem' }}>
        Some documents failed extraction, so this statement may be
        incomplete. See <b>05 Extraction status</b> for which ones.
      </div>
    ) : props.status === 'failed' ? (
      <div className="banner banner--warn" style={{ marginBottom: '1.2rem' }}>
        The most recent ingestion run failed to extract anything. You are
        viewing results from an earlier successful run. See{' '}
        <b>05 Extraction status</b> for what happened.
      </div>
    ) : null

  if (data.vendors.length === 0) {
    return (
      <EmptyState title="Nothing to compare yet">
        Add a vendor and run ingestion from <b>01 Set up &amp; ingest</b> to
        build the statement.
      </EmptyState>
    )
  }

  const { vendors, currency } = data
  const pricedRows = data.rows.filter((row) => row.kind === 'priced')
  const attributeRows = data.rows.filter(
    (row) => row.kind === 'value' || row.kind === 'text',
  )

  return (
    <>
      <PageHeader
        eyebrow="Commercial"
        title={props.projectName}
        sub="The priced offer and commercial terms per vendor, read straight from the store. Blanks are blanks — never zeros."
        actions={
          <ExportLinks
            base={statementExportUrl(props.slug)}
            csvTitle="Qty/Unit/Total collapse to the total in CSV"
          />
        }
      />

      {partialRunBanner}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: '1rem',
          marginBottom: '1.4rem',
        }}
      >
        {vendors.map((vendor) => (
          <section className="card" key={vendor}>
            <div className="card-body" style={{ display: 'grid', gap: '0.5rem' }}>
              <div className="title mono">{vendor}</div>
              <div className="meta">
                <span>rev {data.revisions[vendor] ?? '—'}</span>
                <span>{data.currencies[vendor] ?? '—'}</span>
              </div>
              <div>
                <StatusTag status={data.statuses[vendor] ?? 'missing'} />
              </div>
            </div>
          </section>
        ))}
      </div>

      <Card title="Priced scope">
        {pricedRows.length === 0 ? (
          <p className="muted mono" style={{ margin: 0 }}>
            No priced lines in this statement.
          </p>
        ) : (
          <div className="tbl-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th className="sticky-col" />
                  {vendors.map((vendor) => (
                    <th key={vendor} colSpan={3}>
                      {vendor}
                    </th>
                  ))}
                </tr>
                <tr>
                  <th className="sticky-col">Line item</th>
                  {vendors.map((vendor) => (
                    <Fragment key={vendor}>
                      <th>Qty</th>
                      <th className="num">Unit price</th>
                      <th className="num">Total</th>
                    </Fragment>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pricedRows.map((row) => (
                  <PricedRow
                    key={row.key}
                    row={row}
                    vendors={vendors}
                    currency={currency}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Terms & attributes">
        {attributeRows.length === 0 ? (
          <p className="muted mono" style={{ margin: 0 }}>
            No commercial terms recorded yet.
          </p>
        ) : (
          <div className="tbl-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th className="sticky-col">Attribute</th>
                  {vendors.map((vendor) => (
                    <th key={vendor}>{vendor}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {attributeRows.map((row) => (
                  <AttributeRow key={row.key} row={row} vendors={vendors} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

function PricedRow({
  row,
  vendors,
  currency,
}: {
  row: StatementRow
  vendors: string[]
  currency: string
}): JSX.Element {
  const emphasise = EMPHASISED.has(row.key)
  const cellStyle = emphasise
    ? { fontWeight: 700, background: 'var(--surface-2)' }
    : undefined

  return (
    <tr>
      <td className="sticky-col" style={cellStyle}>
        {row.label}
      </td>
      {vendors.map((vendor) => {
        const cell = row.cells[vendor]
        return (
          <PricedCells
            key={vendor}
            qty={cell?.qty ?? null}
            unitPrice={cell?.unit_price ?? null}
            total={cell?.total ?? null}
            note={cell?.note ?? null}
            currency={currency}
            cellStyle={cellStyle}
          />
        )
      })}
    </tr>
  )
}

function PricedCells({
  qty,
  unitPrice,
  total,
  note,
  currency,
  cellStyle,
}: {
  qty: number | null
  unitPrice: number | null
  total: number | null
  note: string | null
  currency: string
  cellStyle: { fontWeight: number; background: string } | undefined
}): JSX.Element {
  return (
    <>
      <td className="num" style={cellStyle}>
        {qty ?? '—'}
      </td>
      <td className="num" style={cellStyle}>
        {unitPrice != null ? formatMoney(unitPrice, currency) : '—'}
      </td>
      <td className="num" style={cellStyle}>
        {total != null ? formatMoney(total, currency) : '—'}
        {note && <p className="rationale">{note}</p>}
      </td>
    </>
  )
}

function AttributeRow({
  row,
  vendors,
}: {
  row: StatementRow
  vendors: string[]
}): JSX.Element {
  return (
    <tr>
      <td className="sticky-col">{row.label}</td>
      {vendors.map((vendor) => {
        const cell = row.cells[vendor]
        return (
          <td key={vendor}>
            <div style={{ whiteSpace: 'pre-wrap' }}>{cell?.text ?? '—'}</div>
            {cell?.note && <p className="rationale">{cell.note}</p>}
          </td>
        )
      })}
    </tr>
  )
}
