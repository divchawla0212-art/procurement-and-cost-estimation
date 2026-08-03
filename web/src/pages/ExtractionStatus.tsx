import type { JSX } from 'react'
import { fetchExtractionStatus } from '../api'
import type { DocumentStatus, VendorExtraction } from '../types'
import { useAsync } from '../useAsync'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

export interface ExtractionStatusProps {
  slug: string
  projectName: string
}

const STATUS_MARK: Record<DocumentStatus['status'], string> = {
  ok: '✅',
  failed: '⚠️',
  skipped: '—',
  pending: '·',
}

export function ExtractionStatus(props: ExtractionStatusProps): JSX.Element {
  const { data, error, loading } = useAsync(
    () => fetchExtractionStatus(props.slug),
    [props.slug],
  )

  if (loading) return <LoadingState label="Loading extraction status…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No extraction status was returned." />

  const { vendors, totals } = data

  if (vendors.length === 0) {
    return (
      <EmptyState title="No vendors">
        The project has no vendors, so there is nothing to report.
      </EmptyState>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="Extraction"
        title={props.projectName}
        sub={`${totals.extracted} of ${totals.documents} documents read, ${
          totals.facts
        } technical facts, ${totals.failed} could not be read${
          totals.secondary_failed ? `, ${totals.secondary_failed} second pass failed` : ''
        }.`}
      />
      <div className="worklist">
        {vendors.map((vendor) => (
          <VendorPanel key={vendor.vendor} vendor={vendor} />
        ))}
      </div>
    </>
  )
}

function VendorPanel({ vendor }: { vendor: VendorExtraction }): JSX.Element {
  return (
    <Card
      title={vendor.vendor}
      actions={
        <span className="meta">
          {vendor.documents.length} files &middot; {vendor.extracted} read &middot;{' '}
          {vendor.failed} failed &middot; {vendor.skipped} skipped &middot;{' '}
          {vendor.fact_count} facts &middot; {vendor.unanswered} unanswered
        </span>
      }
    >
      {vendor.fact_count === 0 && (
        // the condition that went unnoticed across seven runs
        <p className="warn">
          No technical facts were extracted for this vendor. Every
          machine-checked requirement will read as unanswered.
        </p>
      )}
      {!vendor.has_commercial && (
        <p className="warn">No commercial terms were extracted for this vendor.</p>
      )}
      {vendor.secondary_failed > 0 && (
        // a document can be read twice; this counts the second read's
        // outcome only. coverage.py increments this unconditionally on the
        // primary status — run_secondary does not depend on it, so the
        // first pass may have failed too (in fact that is the likelier
        // case: both passes hit the same provider on the same text). Do not
        // claim the first pass succeeded — see the row's own Status column
        // and the "second pass" column for what actually happened to it.
        <p className="warn">
          {vendor.secondary_failed} document
          {vendor.secondary_failed === 1 ? '' : 's'} failed a second,
          technical-extraction pass.
        </p>
      )}
      {vendor.unattributed_facts > 0 && (
        // a store breach, not an extraction outcome: these facts are in the
        // vendor's stored collection and belong to no document listed below,
        // so no row accounts for them. They are still counted in `fact_count`
        // — dropping them from the total is what made this invisible.
        <p className="warn">
          {vendor.unattributed_facts} stored fact
          {vendor.unattributed_facts === 1 ? '' : 's'} belong to no document
          listed here, and no row below accounts for them.
        </p>
      )}

      {vendor.documents.length === 0 ? (
        <p className="muted mono">No documents uploaded.</p>
      ) : (
        <div className="tbl-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Classified</th>
                <th scope="col">Read as</th>
                <th scope="col">Status</th>
                <th scope="col">Second pass</th>
                <th scope="col">Facts</th>
              </tr>
            </thead>
            <tbody>
              {vendor.documents.map((doc) => (
                <DocumentRow key={doc.doc_id} doc={doc} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function DocumentRow({ doc }: { doc: DocumentStatus }): JSX.Element {
  const secondaryFailed = doc.secondary_status === 'failed'
  return (
    <tr>
      <td>
        {doc.filename}
        {/* On the Dashboard, adjacent `.vpill`s are spaced by the flex
            `.vendors` row's own `gap` — there is no such container here, so
            this cell needs its own explicit space between the filename text
            node and the pill, or they run together unstyled-looking as
            "...pdfquotation". */}
        {doc.is_quotation && <>{' '}<span className="vpill">quotation</span></>}
        {doc.superseded_by && (
          <p className="muted mono">superseded by {doc.superseded_by}</p>
        )}
        {/* stored notes are displayed verbatim, never re-worded — the same
            rule the compliance screen applies to rationale */}
        {doc.status !== 'ok' && doc.notes && (
          <p className="muted mono">{doc.notes}</p>
        )}
      </td>
      <td className="mono">{doc.doc_class}</td>
      <td className="mono">{doc.route ?? '—'}</td>
      <td>
        {STATUS_MARK[doc.status]} {doc.status}
        {doc.text_source && (
          <span className="muted mono"> {doc.text_source}</span>
        )}
      </td>
      <td className={secondaryFailed ? 'warn' : 'muted mono'}>
        {doc.secondary_route ? (
          <>
            {doc.secondary_route} {secondaryFailed ? 'failed' : doc.secondary_status}
          </>
        ) : (
          '—'
        )}
        {/* the blind spot this screen exists to close: a primary `ok` with a
            silently-failed secondary pass. Rendered verbatim, never re-worded. */}
        {secondaryFailed && doc.secondary_notes && (
          <p className="warn mono">{doc.secondary_notes}</p>
        )}
      </td>
      <td className="mono">{doc.fact_count}</td>
    </tr>
  )
}
