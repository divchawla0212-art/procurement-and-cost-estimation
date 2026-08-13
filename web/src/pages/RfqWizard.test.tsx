import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RfqWizard } from './RfqWizard'
import type { RfqDetail } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchRfq: vi.fn(),
    transitionRfq: vi.fn(),
    freezeTechnicalPackage: vi.fn(),
    addShortlistEntry: vi.fn(),
    removeShortlistEntry: vi.fn(),
    approveShortlist: vi.fn(),
    setTbeTemplate: vi.fn(),
    addVdrlLine: vi.fn(),
    removeVdrlLine: vi.fn(),
  }
})

import {
  addShortlistEntry,
  approveShortlist,
  fetchRfq,
  freezeTechnicalPackage,
  removeShortlistEntry,
  transitionRfq,
} from '../api'

const STAGES = [
  'Scoping',
  'Shortlisting',
  'Issued',
  'Clarifications',
  'Bids Received',
  'Evaluation',
  'Negotiation',
  'Awarded',
  'PO Issued',
]

function detail(over: Partial<RfqDetail> = {}): RfqDetail {
  return {
    rfq: {
      id: 'rfq_abc',
      reference: 'ADP-RFQ-2026-014',
      project_id: 'prj_1',
      item_ids: ['itm_1'],
      package: 'Wellhead tie-in materials',
      discipline: 'Mechanical',
      value_estimate_aed: 46200000,
      stage: 'Scoping',
      history: [
        { from_stage: null, to_stage: 'Scoping', at: '2026-08-13T09:00:00Z', by: 'system', reason: 'RFQ created' },
      ],
    },
    gate: { passed: false, reason: 'The technical package must be frozen before shortlisting can begin.' },
    technical_package: null,
    shortlist: [],
    shortlist_approved: false,
    tbe_template: null,
    vdrl: [],
    bids: [],
    bid_selection: null,
    ...over,
  }
}

async function show(data: RfqDetail) {
  vi.mocked(fetchRfq).mockResolvedValue(data)
  render(<RfqWizard rfqId="rfq_abc" stages={STAGES} onBack={() => {}} />)
  await waitFor(() => {
    expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument()
  })
}

describe('RfqWizard', () => {
  it('shows the four pre-bid steps and opens the RFQ’s current one', async () => {
    await show(detail())
    const steps = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(steps.some((t) => t?.includes('Scoping'))).toBe(true)
    expect(steps.some((t) => t?.includes('Clarifications'))).toBe(true)
    // The Scoping editor is the one on screen
    expect(screen.getByLabelText('Package revision')).toBeInTheDocument()
  })

  it('says what is blocking the step, in the gate’s own words', async () => {
    await show(detail())
    expect(
      screen.getByText(/The technical package must be frozen/),
    ).toBeInTheDocument()
  })

  it('ticks a step off by transitioning to the next stage', async () => {
    vi.mocked(transitionRfq).mockResolvedValue(detail().rfq)
    await show(detail({ gate: { passed: true, reason: null } }))

    fireEvent.click(screen.getByRole('button', { name: /Mark Scoping complete/ }))

    await waitFor(() => {
      expect(transitionRfq).toHaveBeenCalledWith('rfq_abc', 'Shortlisting')
    })
  })

  it('surfaces the server’s refusal when a closed gate rejects the step', async () => {
    // The button stays enabled on a closed gate on purpose: a disabled control
    // explains nothing, and the gate's sentence is the explanation.
    vi.mocked(transitionRfq).mockRejectedValue(
      new Error('The shortlist must be approved by procurement before issuance.'),
    )
    await show(detail())

    fireEvent.click(screen.getByRole('button', { name: /Mark Scoping complete/ }))

    await waitFor(() => {
      expect(
        screen.getByText(/The shortlist must be approved by procurement/),
      ).toBeInTheDocument()
    })
  })

  it('lets you open an earlier step and keep editing it', async () => {
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Issued' },
        technical_package: {
          rfq_id: 'rfq_abc',
          revision: 'Rev. B',
          basis_of_design: 'basis',
          attachments: [],
          frozen_at: '2026-08-13T10:00:00Z',
          frozen_by: 'lead@adp.ae',
        },
        shortlist: [
          {
            id: 'sle_1',
            rfq_id: 'rfq_abc',
            vendor_name: 'Galfar',
            prequal_status: 'Qualified',
            scope_code_fit: true,
            included: true,
            override_by: null,
            override_reason: null,
          },
        ],
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: /Shortlisting/ }))

    expect(screen.getByText(/This step is complete\. You can still edit it/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
  })

  it('adds and removes a shortlist vendor', async () => {
    vi.mocked(addShortlistEntry).mockResolvedValue({
      id: 'sle_2', rfq_id: 'rfq_abc', vendor_name: 'Petrofac',
      prequal_status: 'Qualified', scope_code_fit: true, included: true,
      override_by: null, override_reason: null,
    })
    vi.mocked(removeShortlistEntry).mockResolvedValue(undefined)
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Shortlisting' },
        shortlist: [
          {
            id: 'sle_1', rfq_id: 'rfq_abc', vendor_name: 'Galfar',
            prequal_status: 'Qualified', scope_code_fit: true, included: true,
            override_by: null, override_reason: null,
          },
        ],
      }),
    )

    fireEvent.change(screen.getByLabelText('Vendor name'), { target: { value: 'Petrofac' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add vendor' }))
    await waitFor(() => {
      expect(addShortlistEntry).toHaveBeenCalledWith(
        'rfq_abc',
        expect.objectContaining({ vendor_name: 'Petrofac', included: true }),
      )
    })

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => {
      expect(removeShortlistEntry).toHaveBeenCalledWith('rfq_abc', 'sle_1')
    })
  })

  it('warns that changing vendors re-opens approval', async () => {
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }))
    expect(
      screen.getByText(/Any change to the vendors re-opens approval/),
    ).toBeInTheDocument()
  })

  it('approves the shortlist', async () => {
    vi.mocked(approveShortlist).mockResolvedValue({ approved_by: 'admin@gmail.com' })
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }))

    fireEvent.click(screen.getByRole('button', { name: 'Approve shortlist' }))

    await waitFor(() => expect(approveShortlist).toHaveBeenCalledWith('rfq_abc'))
  })

  it('freezes the package', async () => {
    vi.mocked(freezeTechnicalPackage).mockResolvedValue({
      rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'b',
      attachments: [], frozen_at: '2026-08-13T10:00:00Z', frozen_by: 'admin@gmail.com',
    })
    await show(
      detail({
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [], frozen_at: null, frozen_by: null,
        },
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Freeze package' }))

    await waitFor(() => expect(freezeTechnicalPackage).toHaveBeenCalledWith('rfq_abc'))
  })

  it('replaces the scoping editor with a read-only view once frozen', async () => {
    await show(
      detail({
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: 'Rev. C' }],
          frozen_at: '2026-08-13T10:00:00Z', frozen_by: 'lead@adp.ae',
        },
      }),
    )
    expect(screen.queryByLabelText('Package revision')).not.toBeInTheDocument()
    expect(screen.getByText(/frozen by lead@adp\.ae/)).toBeInTheDocument()
    expect(screen.getByText(/cannot be edited/)).toBeInTheDocument()
  })

  it('flags an attachment with no definite revision, since that blocks the freeze', async () => {
    await show(
      detail({
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: null }],
          frozen_at: null, frozen_by: null,
        },
      }),
    )
    expect(screen.getByText('none')).toBeInTheDocument()
  })

  it('keeps the whole stage history visible while stepping', async () => {
    await show(detail())
    expect(screen.getByText(/RFQ created/)).toBeInTheDocument()
  })
})
