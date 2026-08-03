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
        sub={`${totals.extracted ?? 0} of ${totals.documents ?? 0} documents read, ${
          totals.facts ?? 0
        } technical facts, ${totals.failed ?? 0} could not be read${
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
        // a document can be read twice; this is the case a status of `ok`
        // alone would hide — see the "second pass" column below for which
        // file and its stored reason.
        <p className="warn">
          {vendor.secondary_failed} document
          {vendor.secondary_failed === 1 ? '' : 's'} read successfully on the
          first pass but failed the second, technical-extraction pass.
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
        {doc.is_quotation && <span className="vpill">quotation</span>}
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
