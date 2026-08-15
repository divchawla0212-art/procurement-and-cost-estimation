import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { RfqWorkflow } from './RfqWorkflow'
import type { RfqRoster } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchRfqRoster: vi.fn(),
  }
})

import { fetchRfqRoster } from '../api'

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

function roster(overrides: Partial<RfqRoster> = {}): RfqRoster {
  return {
    rfqs: [],
    stages: STAGES,
    stage_counts: Object.fromEntries(STAGES.map((s) => [s, 0])),
    stage_codes: Object.fromEntries(STAGES.map((s, i) => [s, `RFQ-${String(i).padStart(2, '0')}`])),
    ...overrides,
  }
}

const RFQ = {
  id: 'rfq_abc',
  reference: 'ADP-RFQ-2026-014',
  project_id: 'prj_1',
  item_ids: ['itm_1'],
  package: 'Wellhead & CGF tie-in materials',
  discipline: 'Mechanical / piping',
  value_estimate_aed: 46200000,
  stage: 'Issued',
  history: [
    {
      from_stage: null,
      to_stage: 'Scoping',
      at: '2026-08-12T09:00:00Z',
      by: 'system',
      reason: 'RFQ created',
    },
    {
      from_stage: 'Evaluation',
      to_stage: 'Issued',
      at: '2026-08-12T11:00:00Z',
      by: 'amal@example.com',
      reason: 'retender — all bids over estimate',
    },
  ],
}

/** The roster navigates rather than mounting a detail view in place, so it
 *  needs a router around it and a way to read where a click sent us. */
function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

const at = () => screen.getByTestId('location').textContent

async function renderRoster(data: RfqRoster) {
  vi.mocked(fetchRfqRoster).mockResolvedValue(data)
  render(
    <MemoryRouter initialEntries={['/rfqs']}>
      <LocationProbe />
      <RfqWorkflow />
    </MemoryRouter>,
  )
  await waitFor(() => {
    expect(screen.queryByText(/Loading RFQ workflow/i)).not.toBeInTheDocument()
  })
}

describe('RfqWorkflow', () => {
  it('renders the strip and an empty state when no RFQ has been raised', async () => {
    await renderRoster(roster())
    expect(screen.getByRole('list', { name: /RFQ stages/i })).toBeInTheDocument()
    expect(screen.getByText(/No RFQs yet/i)).toBeInTheDocument()
  })

  it('lists RFQs and marks nothing current until one is selected', async () => {
    await renderRoster(
      roster({ rfqs: [RFQ], stage_counts: { ...roster().stage_counts, Issued: 1 } }),
    )
    expect(screen.getByText('ADP-RFQ-2026-014')).toBeInTheDocument()
    expect(screen.queryAllByRole('listitem', { current: 'step' })).toHaveLength(0)
  })

  // Which of the two detail screens an RFQ opens in is now the route's
  // decision, not this component's — all the roster owes is the address.
  it('navigates to the RFQ when its reference is clicked', async () => {
    await renderRoster(
      roster({ rfqs: [RFQ], stage_counts: { ...roster().stage_counts, Issued: 1 } }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'ADP-RFQ-2026-014' }))

    await waitFor(() => expect(at()).toBe(`/rfqs/${RFQ.id}`))
  })

  it('reaches the RFQ by keyboard, not only by pointer', async () => {
    // The reference is a real button rather than a clickable row precisely so
    // this works; a row with an onClick has no accessible name and no focus.
    await renderRoster(roster({ rfqs: [RFQ] }))
    const link = screen.getByRole('button', { name: 'ADP-RFQ-2026-014' })
    link.focus()
    expect(link).toHaveFocus()
  })
})
