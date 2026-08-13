import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ClarificationsStep } from './ClarificationsStep'
import type { ClarificationQuery, RfqDetail } from '../../types'

vi.mock('../../api', () => ({
  raiseQuery: vi.fn().mockResolvedValue({}),
  answerQuery: vi.fn().mockResolvedValue({}),
  withdrawQuery: vi.fn().mockResolvedValue({}),
  draftAddendum: vi.fn().mockResolvedValue({}),
  updateAddendum: vi.fn().mockResolvedValue({}),
  issueAddendum: vi.fn().mockResolvedValue({}),
  deleteAddendum: vi.fn().mockResolvedValue({}),
}))

import { answerQuery, raiseQuery, withdrawQuery } from '../../api'

const BASE: RfqDetail = {
  rfq: {
    id: 'rfq_1',
    reference: 'RUU-RFQ-2026-006',
    project_id: 'prj_1',
    item_ids: [],
    package: 'Field transmitters',
    discipline: 'Instrumentation',
    value_estimate_aed: 4_750_000,
    stage: 'Clarifications',
    history: [],
  },
  gate: { passed: true, reason: null },
  technical_package: {
    rfq_id: 'rfq_1',
    revision: 'Rev. A',
    basis_of_design: 'Battery-limit field transmitters.',
    attachments: [{ doc_code: 'IO-411', title: 'IO list', revision: 'Rev. A' }],
    frozen_at: '2026-08-01T00:00:00Z',
    frozen_by: 'lead@example.com',
  },
  shortlist: [
    {
      id: 'sle_1',
      rfq_id: 'rfq_1',
      vendor_id: 'bdr_1',
      vendor_name: 'Al Munara Switchgear LLC',
      prequal_status: 'Approved',
      scope_code_fit: true,
      included: true,
      override_by: null,
      override_reason: null,
    },
  ],
  shortlist_approved: true,
  tbe_template: null,
  vdrl: [],
  bids: [],
  bid_selection: null,
  queries: [],
  addenda: [],
  bid_due_date: null,
}

const OPEN_QUERY: ClarificationQuery = {
  id: 'clq_1',
  rfq_id: 'rfq_1',
  number: 'TQ-001',
  raised_by_entry_id: 'sle_1',
  raised_by_name: 'Al Munara Switchgear LLC',
  raised_on: '2026-08-13',
  category: 'Technical',
  question: 'Which IO list revision governs for pricing?',
  answer: null,
  answered_by: null,
  answered_at: null,
  restricted_reason: null,
  withdrawn_reason: null,
  withdrawn_by: null,
  withdrawn_at: null,
  state: 'Open',
  circulated: true,
}

const noop = async (action: () => Promise<unknown>) => {
  await action()
}

beforeEach(() => vi.clearAllMocks())

describe('ClarificationsStep', () => {
  it('raises a query against a bidder chosen from the shortlist', async () => {
    render(<ClarificationsStep data={BASE} run={noop} busy={false} tick={0} />)

    fireEvent.change(screen.getByLabelText(/raised by/i), { target: { value: 'sle_1' } })
    fireEvent.change(screen.getByLabelText(/^question$/i), {
      target: { value: 'Which IO list revision?' },
    })
    fireEvent.click(screen.getByRole('button', { name: /record query/i }))

    expect(raiseQuery).toHaveBeenCalledWith(
      'rfq_1',
      expect.objectContaining({
        raised_by_entry_id: 'sle_1',
        question: 'Which IO list revision?',
        category: 'Technical',
      }),
    )
  })

  it('offers only included bidders as the raiser', () => {
    const data: RfqDetail = {
      ...BASE,
      shortlist: [
        ...BASE.shortlist,
        {
          ...BASE.shortlist[0],
          id: 'sle_2',
          vendor_name: 'Excluded Co',
          included: false,
        },
      ],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toContain('Al Munara Switchgear LLC')
    expect(options).not.toContain('Excluded Co')
  })

  it('sends no restricted_reason unless the answer is restricted', async () => {
    render(
      <ClarificationsStep
        data={{ ...BASE, queries: [OPEN_QUERY] }}
        run={noop}
        busy={false}
        tick={0}
      />,
    )

    fireEvent.change(screen.getByLabelText(/answer to TQ-001/i), {
      target: { value: 'Rev. A is the issued revision.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^answer TQ-001$/i }))

    expect(answerQuery).toHaveBeenCalledWith('rfq_1', 'clq_1', {
      answer: 'Rev. A is the issued revision.',
    })
  })

  it('reveals a required reason input only when the answer is restricted', () => {
    render(
      <ClarificationsStep
        data={{ ...BASE, queries: [OPEN_QUERY] }}
        run={noop}
        busy={false}
        tick={0}
      />,
    )

    expect(screen.queryByLabelText(/why this answer is not circulated/i)).toBeNull()
    fireEvent.click(screen.getByLabelText(/do not circulate/i))
    expect(screen.getByLabelText(/why this answer is not circulated/i)).toBeTruthy()
  })

  it('sends the restriction reason once one is given', async () => {
    render(
      <ClarificationsStep
        data={{ ...BASE, queries: [OPEN_QUERY] }}
        run={noop}
        busy={false}
        tick={0}
      />,
    )

    fireEvent.change(screen.getByLabelText(/answer to TQ-001/i), {
      target: { value: 'Yes.' },
    })
    fireEvent.click(screen.getByLabelText(/do not circulate/i))
    fireEvent.change(screen.getByLabelText(/why this answer is not circulated/i), {
      target: { value: "Reveals the bidder's own layout." },
    })
    fireEvent.click(screen.getByRole('button', { name: /^answer TQ-001$/i }))

    expect(answerQuery).toHaveBeenCalledWith('rfq_1', 'clq_1', {
      answer: 'Yes.',
      restricted_reason: "Reveals the bidder's own layout.",
    })
  })

  it('will not offer to withdraw without a reason', () => {
    render(
      <ClarificationsStep
        data={{ ...BASE, queries: [OPEN_QUERY] }}
        run={noop}
        busy={false}
        tick={0}
      />,
    )

    const button = screen.getByRole('button', { name: /^withdraw TQ-001$/i })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText(/reason for withdrawing TQ-001/i), {
      target: { value: 'Duplicate of TQ-001.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^withdraw TQ-001$/i }))
    expect(withdrawQuery).toHaveBeenCalledWith('rfq_1', 'clq_1', 'Duplicate of TQ-001.')
  })

  it('states whether an answer was circulated or withheld, and why', () => {
    const data: RfqDetail = {
      ...BASE,
      queries: [
        {
          ...OPEN_QUERY,
          id: 'clq_2',
          number: 'TQ-002',
          answer: 'Frame 6.',
          answered_by: 'buyer@example.com',
          answered_at: '2026-08-14T00:00:00Z',
          restricted_reason: "Reveals the bidder's own layout.",
          state: 'Answered',
          circulated: false,
        },
      ],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    expect(screen.getByText(/not circulated/i)).toBeTruthy()
    expect(screen.getByText(/reveals the bidder's own layout/i)).toBeTruthy()
  })

  it('leads with what is still open', () => {
    const answered: ClarificationQuery = {
      ...OPEN_QUERY,
      id: 'clq_0',
      number: 'CQ-001',
      answer: 'Yes.',
      state: 'Answered',
    }
    render(
      <ClarificationsStep
        data={{ ...BASE, queries: [answered, OPEN_QUERY] }}
        run={noop}
        busy={false}
        tick={0}
      />,
    )

    const numbers = screen.getAllByText(/^(TQ|CQ)-\d{3}$/).map((n) => n.textContent)
    expect(numbers[0]).toBe('TQ-001')
  })

  it('shows an issued addendum as read-only and a draft with its controls', () => {
    const common = {
      rfq_id: 'rfq_1',
      supersedes_revision: 'Rev. A',
      attachments: [],
      arising_from_query_ids: [],
      bid_due_date: null,
    }
    const data: RfqDetail = {
      ...BASE,
      addenda: [
        {
          ...common,
          id: 'add_1',
          number: 'ADD-01',
          revision: 'Rev. B',
          summary: 'IO list corrected.',
          issued_at: '2026-08-16T00:00:00Z',
          issued_by: 'buyer@example.com',
          draft: false,
        },
        {
          ...common,
          id: 'add_2',
          number: 'ADD-02',
          revision: 'Rev. C',
          summary: 'Due date extended.',
          issued_at: null,
          issued_by: null,
          draft: true,
        },
      ],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    expect(screen.getByRole('button', { name: /issue ADD-02/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /issue ADD-01/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /delete ADD-01/i })).toBeNull()
    expect(screen.getByText(/issued by buyer@example.com/)).toBeTruthy()
  })

  it('states the operative bid due date when an addendum has moved it', () => {
    render(
      <ClarificationsStep
        data={{ ...BASE, bid_due_date: '2026-10-15' }}
        run={noop}
        busy={false}
        tick={0}
      />,
    )
    expect(screen.getByText('2026-10-15')).toBeTruthy()
  })

  it('says why an addendum cannot be drafted against an unfrozen package', () => {
    const data: RfqDetail = {
      ...BASE,
      technical_package: { ...BASE.technical_package!, frozen_at: null, frozen_by: null },
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    expect(screen.getByText(/not frozen/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /save draft addendum/i })).toBeNull()
  })

  it('counts what is blocking the gate', () => {
    const data: RfqDetail = {
      ...BASE,
      queries: [OPEN_QUERY],
      addenda: [
        {
          id: 'add_2',
          rfq_id: 'rfq_1',
          number: 'ADD-01',
          supersedes_revision: 'Rev. A',
          revision: 'Rev. B',
          summary: 's',
          attachments: [],
          arising_from_query_ids: [],
          bid_due_date: null,
          issued_at: null,
          issued_by: null,
          draft: true,
        },
      ],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    expect(screen.getByText(/1 open query, 1 draft addendum/i)).toBeTruthy()
  })
})
