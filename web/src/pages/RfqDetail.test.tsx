import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { RfqDetail } from './RfqDetail'
import type { RfqDetail as RfqDetailData } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, fetchRfq: vi.fn() }
})

import { fetchRfq } from '../api'

const STAGES = [
  'Shortlisting',
  'Issued',
  'Clarifications',
  'Bids Received',
  'Evaluation',
  'Negotiation',
  'Awarded',
  'PO Issued',
]

function detail(over: Partial<RfqDetailData> = {}): RfqDetailData {
  return {
    rfq: {
      id: 'rfq_abc',
      reference: 'ADP-RFQ-2026-014',
      project_id: 'prj_1',
      item_ids: ['itm_1'],
      package: 'Wellhead & CGF tie-in materials',
      discipline: 'Mechanical / piping',
      value_estimate_aed: 46200000,
      // The read-only screen is what an RFQ past the wizard's range opens in,
      // so it is shown at a stage that actually lands here.
      stage: 'Bids Received',
      history: [
        {
          from_stage: null,
          to_stage: 'Shortlisting',
          at: '2026-08-12T09:00:00Z',
          by: 'system',
          reason: 'RFQ created',
        },
      ],
    },
    gate: {
      passed: false,
      reason: 'Bids must be selected for evaluation before evaluation can begin.',
    },
    technical_package: null,
    shortlist: [],
    shortlist_approved: false,
    client_approver: 'ADNOC',
    tbe_template: null,
    vdrl: [],
    bids: [],
    bid_selection: null,
    queries: [],
    addenda: [],
    bid_due_date: null,
    ...over,
  }
}

async function show(data: RfqDetailData) {
  vi.mocked(fetchRfq).mockResolvedValue(data)
  render(<RfqDetail rfqId="rfq_abc" stages={STAGES} onBack={() => {}} />)
  await waitFor(() => {
    expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument()
  })
}

describe('RfqDetail', () => {
  it('says what is blocking the next stage, in the gate’s own words', async () => {
    await show(detail())
    expect(
      screen.getByText(/Blocked at Bids Received: Bids must be selected for evaluation/),
    ).toBeInTheDocument()
  })

  it('marks the RFQ’s stage on the strip', async () => {
    await show(detail())
    expect(screen.getByRole('listitem', { current: 'step' })).toHaveTextContent(
      'Bids Received',
    )
  })

  it('reports a passing gate rather than a reason', async () => {
    await show(detail({ gate: { passed: true, reason: null } }))
    expect(screen.getByText(/Ready to leave Bids Received/)).toBeInTheDocument()
  })

  it('calls out an attachment with no definite revision, since that blocks the freeze', async () => {
    await show(
      detail({
        technical_package: {
          rfq_id: 'rfq_abc',
          revision: 'Rev. B',
          basis_of_design: 'basis',
          attachments: [
            { doc_code: 'HAL-PID-001', title: 'P&ID', revision: null },
            { doc_code: 'HAL-PID-002', title: 'P&ID 2', revision: 'Rev. C' },
          ],
          frozen_at: null,
          frozen_by: null,
        },
      }),
    )
    expect(screen.getByText('none')).toBeInTheDocument()
    expect(screen.getByText('not frozen')).toBeInTheDocument()
  })

  it('shows who froze a frozen package', async () => {
    await show(
      detail({
        technical_package: {
          rfq_id: 'rfq_abc',
          revision: 'Rev. B',
          basis_of_design: 'basis',
          attachments: [],
          frozen_at: '2026-08-12T10:00:00Z',
          frozen_by: 'lead.engineer@adp.ae',
        },
      }),
    )
    expect(screen.getByText(/frozen by lead\.engineer@adp\.ae/)).toBeInTheDocument()
  })

  it('shows each bid’s document tally and names what is missing', async () => {
    await show(
      detail({
        bids: [
          {
            id: 'bid_1',
            rfq_id: 'rfq_abc',
            vendor_name: 'Petrofac',
            received_at: '2026-08-12T10:00:00Z',
            headline_price_aed: 51400000,
            currency: 'AED',
            vdrl_received: 1,
            vdrl_required: 2,
            vdrl_missing: ['DS-001'],
            selected: false,
          },
        ],
      }),
    )
    expect(screen.getByText('1/2')).toBeInTheDocument()
    expect(screen.getByText(/missing DS-001/)).toBeInTheDocument()
  })

  it('says evaluation is waiting when no bids have been selected', async () => {
    await show(
      detail({
        bids: [
          {
            id: 'bid_1',
            rfq_id: 'rfq_abc',
            vendor_name: 'Petrofac',
            received_at: '2026-08-12T10:00:00Z',
            headline_price_aed: 51400000,
            currency: 'AED',
            vdrl_received: 2,
            vdrl_required: 2,
            vdrl_missing: [],
            selected: false,
          },
        ],
      }),
    )
    expect(screen.getByText(/No bids selected yet/)).toBeInTheDocument()
  })

  it('names who selected the bids and why', async () => {
    await show(
      detail({
        bids: [],
        bid_selection: {
          rfq_id: 'rfq_abc',
          selected_bid_ids: ['bid_1'],
          selected_by: 'client@adp.ae',
          rationale: 'Lowest compliant bid',
          at: '2026-08-12T12:00:00Z',
        },
      }),
    )
    // no bids in the register, so the card shows its empty state instead
    expect(screen.getByText(/No bids received yet/)).toBeInTheDocument()
  })

  it('keeps both passes through a stage in the history', async () => {
    const d = detail()
    d.rfq.history = [
      ...d.rfq.history,
      { from_stage: 'Scoping', to_stage: 'Issued', at: '2026-08-12T10:00:00Z', by: 'a@b.c', reason: null },
      { from_stage: 'Evaluation', to_stage: 'Issued', at: '2026-08-12T11:00:00Z', by: 'a@b.c', reason: 'retender' },
    ]
    await show(d)
    expect(screen.getByText(/RFQ created/)).toBeInTheDocument()
    expect(screen.getByText(/retender/)).toBeInTheDocument()
  })

  it('explains an absent TBE template rather than showing an empty card', async () => {
    await show(detail())
    expect(screen.getByText(/cannot be issued without one/)).toBeInTheDocument()
  })

  it('shows the clarification register read-only, with circulation stated', async () => {
    const base = {
      rfq_id: 'rfq_abc',
      raised_by_entry_id: 'sle_1',
      raised_by_name: 'Al Munara Switchgear LLC',
      raised_on: '2026-08-13',
      category: 'Technical' as const,
      answered_by: 'buyer@adp.ae',
      answered_at: '2026-08-14T00:00:00Z',
      withdrawn_reason: null,
      withdrawn_by: null,
      withdrawn_at: null,
      state: 'Answered' as const,
    }
    await show(
      detail({
        queries: [
          {
            ...base,
            id: 'clq_1',
            number: 'TQ-001',
            question: 'Which IO list revision governs?',
            answer: 'Rev. A.',
            restricted_reason: null,
            circulated: true,
          },
          {
            ...base,
            id: 'clq_2',
            number: 'TQ-002',
            question: 'Confirm the frame size.',
            answer: 'Frame 6.',
            restricted_reason: "Reveals the bidder's own layout.",
            circulated: false,
          },
        ],
      }),
    )

    expect(screen.getByText('TQ-001')).toBeInTheDocument()
    expect(screen.getByText('circulated')).toBeInTheDocument()
    expect(
      screen.getByText(/not circulated: Reveals the bidder's own layout/),
    ).toBeInTheDocument()
    // Read-only: none of the wizard's controls appear on this screen.
    expect(screen.queryByRole('button', { name: /^answer TQ-001$/i })).toBeNull()
  })

  it('shows an addendum as the package’s revision trail', async () => {
    await show(
      detail({
        addenda: [
          {
            id: 'add_1',
            rfq_id: 'rfq_abc',
            number: 'ADD-01',
            supersedes_revision: 'Rev. A',
            revision: 'Rev. B',
            summary: 'IO list corrected.',
            attachments: [],
            arising_from_query_ids: [],
            bid_due_date: '2026-10-15',
            issued_at: '2026-08-16T00:00:00Z',
            issued_by: 'buyer@adp.ae',
            draft: false,
          },
        ],
        bid_due_date: '2026-10-15',
      }),
    )

    expect(screen.getByText('ADD-01')).toBeInTheDocument()
    expect(screen.getByText(/Rev\. A → Rev\. B/)).toBeInTheDocument()
    expect(screen.getAllByText(/2026-10-15/).length).toBeGreaterThan(0)
  })

  it('says plainly when the round produced nothing', async () => {
    await show(detail())
    expect(
      screen.getByText(/No clarifications were raised and no addenda were issued/),
    ).toBeInTheDocument()
  })
})
