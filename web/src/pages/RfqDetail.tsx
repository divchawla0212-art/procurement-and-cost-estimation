import type { JSX } from 'react'
import { fetchRfq } from '../api'
import type { RfqDetail as RfqDetailData } from '../types'
import { useAsync } from '../useAsync'
import { StageStrip } from '../components/StageStrip'
import { Card, ErrorState, LoadingState } from '../components/primitives'

/**
 * One RFQ, walked end to end: where it stands, what is blocking the next
 * stage, and every artifact the gates read to decide that.
 *
 * Read-only, deliberately. Advancing a stage is an attributed act — the server
 * takes `by` from the session precisely so it cannot be claimed — and the
 * artifact editors that would satisfy a gate are their own screens. What this
 * has to do is make a blocked RFQ explain itself.
 */
export function RfqDetail({
  rfqId,
  stages,
  onBack,
}: {
  rfqId: string
  stages: string[]
  onBack: () => void
}): JSX.Element {
  const { data, error, loading } = useAsync(() => fetchRfq(rfqId), [rfqId])

  if (loading) return <LoadingState label="Loading RFQ…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No RFQ was returned." />

  const { rfq, gate } = data

  return (
    <>
      <header className="page-head">
        <p className="eyebrow">
          <button type="button" className="linkback" onClick={onBack}>
            ← All RFQs
          </button>
        </p>
        <h1>{rfq.reference}</h1>
        <p className="sub">
          {rfq.package} · {rfq.discipline} · est.{' '}
          {rfq.value_estimate_aed.toLocaleString()} AED
        </p>
      </header>

      {/* counts are meaningless for a single RFQ — the strip is showing
          position here, not a roster tally */}
      <StageStrip stages={stages} counts={{}} current={rfq.stage} />

      <div className={`banner ${gate.passed ? 'banner--ok' : 'banner--warn'}`}>
        {gate.passed
          ? `Ready to leave ${rfq.stage}.`
          : `Blocked at ${rfq.stage}: ${gate.reason}`}
      </div>

      <TechnicalPackageCard data={data} />
      <ShortlistCard data={data} />
      <TbeCard data={data} />
      <VdrlCard data={data} />
      <BidsCard data={data} />
      <HistoryCard data={data} />
    </>
  )
}

function Empty({ children }: { children: string }) {
  return <p className="muted">{children}</p>
}

function TechnicalPackageCard({ data }: { data: RfqDetailData }) {
  const pkg = data.technical_package
  return (
    <Card title="Technical package">
      {!pkg ? (
        <Empty>No technical package attached yet.</Empty>
      ) : (
        <>
          <p>
            <b>{pkg.revision}</b>{' '}
            {pkg.frozen_at ? (
              <span className="muted">frozen by {pkg.frozen_by}</span>
            ) : (
              <span className="warn">not frozen</span>
            )}
          </p>
          <p className="muted">{pkg.basis_of_design}</p>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Title</th>
                <th scope="col">Revision</th>
              </tr>
            </thead>
            <tbody>
              {pkg.attachments.map((a) => (
                <tr key={a.doc_code}>
                  <td className="mono">{a.doc_code}</td>
                  <td>{a.title}</td>
                  {/* an attachment with no definite revision is exactly what
                      blocks the freeze, so it is called out rather than blank */}
                  <td>{a.revision ?? <span className="warn">none</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  )
}

function ShortlistCard({ data }: { data: RfqDetailData }) {
  return (
    <Card
      title={`Shortlist${data.shortlist_approved ? ' — approved' : ''}`}
    >
      {data.shortlist.length === 0 ? (
        <Empty>No vendors shortlisted yet.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Vendor</th>
              <th scope="col">Prequalification</th>
              <th scope="col">Scope fit</th>
              <th scope="col">Included</th>
            </tr>
          </thead>
          <tbody>
            {data.shortlist.map((e) => (
              <tr key={e.vendor_name}>
                <td>{e.vendor_name}</td>
                <td>{e.prequal_status}</td>
                <td>{e.scope_code_fit ? 'yes' : 'no'}</td>
                <td>
                  {e.included ? 'yes' : 'no'}
                  {e.override_reason ? (
                    <div className="muted">
                      override by {e.override_by}: {e.override_reason}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function TbeCard({ data }: { data: RfqDetailData }) {
  const tbe = data.tbe_template
  return (
    <Card title="TBE template">
      {!tbe ? (
        <Empty>No TBE template attached — the RFQ cannot be issued without one.</Empty>
      ) : (
        <>
          <ul>
            {tbe.criteria.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          {tbe.source_rfq_reference ? (
            <p className="muted">Pulled from {tbe.source_rfq_reference}.</p>
          ) : null}
        </>
      )}
    </Card>
  )
}

function VdrlCard({ data }: { data: RfqDetailData }) {
  return (
    <Card title="Vendor document requirements">
      {data.vdrl.length === 0 ? (
        <Empty>No VDRL lines defined.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Code</th>
              <th scope="col">Title</th>
              <th scope="col">Type</th>
              <th scope="col">Mandatory</th>
            </tr>
          </thead>
          <tbody>
            {data.vdrl.map((line) => (
              <tr key={line.doc_code}>
                <td className="mono">{line.doc_code}</td>
                <td>{line.title}</td>
                <td>{line.doc_type}</td>
                <td>{line.mandatory ? 'yes' : 'no'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function BidsCard({ data }: { data: RfqDetailData }) {
  const selection = data.bid_selection
  return (
    <Card title="Bids">
      {data.bids.length === 0 ? (
        <Empty>No bids received yet.</Empty>
      ) : (
        <>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Vendor</th>
                <th scope="col">Headline price</th>
                <th scope="col">Documents</th>
                <th scope="col">Selected</th>
              </tr>
            </thead>
            <tbody>
              {data.bids.map((bid) => (
                <tr key={bid.id}>
                  <td>{bid.vendor_name}</td>
                  <td className="mono">
                    {bid.headline_price_aed.toLocaleString()} {bid.currency}
                  </td>
                  <td>
                    {bid.vdrl_received}/{bid.vdrl_required}
                    {bid.vdrl_missing.length > 0 ? (
                      <div className="warn">missing {bid.vdrl_missing.join(', ')}</div>
                    ) : null}
                  </td>
                  <td>{bid.selected ? 'yes' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selection ? (
            <p className="muted">
              Selected by {selection.selected_by}: {selection.rationale}
            </p>
          ) : (
            <p className="muted">
              No bids selected yet — evaluation cannot begin until they are.
            </p>
          )}
        </>
      )}
    </Card>
  )
}

function HistoryCard({ data }: { data: RfqDetailData }) {
  return (
    <Card title="Stage history">
      <ol className="historylist">
        {data.rfq.history.map((entry, i) => (
          // Index is part of the key on purpose: a retender walks the same
          // edge twice, so `to_stage` alone is not unique within one history.
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
