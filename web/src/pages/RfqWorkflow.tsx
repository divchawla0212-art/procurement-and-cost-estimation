import { useState } from 'react'
import type { JSX } from 'react'
import { fetchRfqRoster } from '../api'
import type { Rfq } from '../types'
import { useAsync } from '../useAsync'
import { StageStrip } from '../components/StageStrip'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

/**
 * The RFQ workflow roster.
 *
 * Read-only on purpose. Advancing an RFQ is a gated act that needs the person
 * doing it on the record (`by` on every `StageTransition`), and Phase 1 has no
 * identity to bind that to beyond the signed-in session — so this screen shows
 * where every RFQ stands and what each one is waiting on, and leaves the
 * transition controls to the phase that can attribute them properly.
 */
export function RfqWorkflow(): JSX.Element {
  const { data, error, loading } = useAsync(() => fetchRfqRoster(), [])
  const [selected, setSelected] = useState<string | null>(null)

  if (loading) return <LoadingState label="Loading RFQ workflow…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No workflow data was returned." />

  const current = data.rfqs.find((r) => r.id === selected) ?? null

  return (
    <>
      <PageHeader
        eyebrow="05 · Workflow"
        title="RFQ workflow"
        sub="Where each RFQ stands across the eight process stages."
      />

      <StageStrip
        stages={data.stages}
        counts={data.stage_counts}
        current={current?.stage ?? null}
      />

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
                <tr
                  key={rfq.id}
                  onClick={() => setSelected(rfq.id === selected ? null : rfq.id)}
                  aria-selected={rfq.id === selected}
                >
                  <td className="mono">{rfq.reference}</td>
                  <td>{rfq.package}</td>
                  <td>{rfq.discipline}</td>
                  <td>{rfq.stage}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {current ? <RfqHistory rfq={current} /> : null}
    </>
  )
}

function RfqHistory({ rfq }: { rfq: Rfq }): JSX.Element {
  return (
    <Card title={`${rfq.reference} — stage history`}>
      <ol className="historylist">
        {rfq.history.map((entry, i) => (
          // The index is part of the key on purpose: the same edge can be
          // walked twice (a retender returns to Issued), so `to_stage` alone
          // is not unique within one RFQ's history.
          <li key={`${entry.to_stage}-${i}`}>
            <span className="mono">{entry.at.slice(0, 10)}</span>{' '}
            <b>
              {entry.from_stage ? `${entry.from_stage} → ` : ''}
              {entry.to_stage}
            </b>{' '}
            <span className="muted">by {entry.by}</span>
            {entry.reason ? <div className="muted">{entry.reason}</div> : null}
          </li>
        ))}
      </ol>
    </Card>
  )
}
