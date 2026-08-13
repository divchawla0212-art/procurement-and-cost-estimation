import { useState } from 'react'
import type { JSX } from 'react'
import { fetchRfqRoster } from '../api'
import { useAsync } from '../useAsync'
import { StageStrip } from '../components/StageStrip'
import { RfqDetail } from './RfqDetail'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

/**
 * The RFQ workflow roster, and the way into one RFQ.
 *
 * Read-only on purpose. Advancing an RFQ is a gated act that the server
 * attributes to the session, and the artifact editors that would satisfy a
 * gate are their own screens; what these two views owe the reader is a clear
 * account of where every RFQ stands and what each one is waiting on.
 */
export function RfqWorkflow(): JSX.Element {
  const { data, error, loading } = useAsync(() => fetchRfqRoster(), [])
  const [openRfqId, setOpenRfqId] = useState<string | null>(null)

  if (loading) return <LoadingState label="Loading RFQ workflow…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No workflow data was returned." />

  if (openRfqId) {
    return (
      <RfqDetail
        rfqId={openRfqId}
        stages={data.stages}
        onBack={() => setOpenRfqId(null)}
      />
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="01 · RFQ process"
        title="RFQ workflow"
        sub="Where each RFQ stands across the eight process stages."
      />

      {/* No `current` on the roster: the strip is a tally of every RFQ here,
          and marking one of nine stages would misread as "the" stage. */}
      <StageStrip stages={data.stages} counts={data.stage_counts} />

      {data.rfqs.length === 0 ? (
        <EmptyState title="No RFQs yet">
          An RFQ appears here once it is raised against a project item.
        </EmptyState>
      ) : (
        <Card title="RFQs">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Reference</th>
                <th scope="col">Package</th>
                <th scope="col">Discipline</th>
                <th scope="col">Stage</th>
              </tr>
            </thead>
            <tbody>
              {data.rfqs.map((rfq) => (
                <tr key={rfq.id}>
                  <td className="mono">
                    {/* A button, not a clickable row: a row with an onClick is
                        unreachable by keyboard and announces nothing. */}
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => setOpenRfqId(rfq.id)}
                    >
                      {rfq.reference}
                    </button>
                  </td>
                  <td>{rfq.package}</td>
                  <td>{rfq.discipline}</td>
                  <td>{rfq.stage}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

    </>
  )
}
