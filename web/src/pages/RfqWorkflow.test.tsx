import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RfqWorkflow } from './RfqWorkflow'
import type { RfqRoster } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, fetchRfqRoster: vi.fn() }
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

async function renderRoster(data: RfqRoster) {
  vi.mocked(fetchRfqRoster).mockResolvedValue(data)
  render(<RfqWorkflow />)
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

  it('shows the selected RFQ stage on the strip and its history below', async () => {
    await renderRoster(
      roster({ rfqs: [RFQ], stage_counts: { ...roster().stage_counts, Issued: 1 } }),
    )
    fireEvent.click(screen.getByText('ADP-RFQ-2026-014'))

    expect(screen.getByRole('listitem', { current: 'step' })).toHaveTextContent('Issued')
    expect(screen.getByText(/stage history/i)).toBeInTheDocument()
    expect(screen.getByText(/retender — all bids over estimate/)).toBeInTheDocument()
  })

  it('keeps both passes through a stage in the history', async () => {
    // A retender walks the same edge twice; neither entry may overwrite the
    // other, so the list must show two rows, not one.
    await renderRoster(roster({ rfqs: [RFQ] }))
    fireEvent.click(screen.getByText('ADP-RFQ-2026-014'))
    expect(screen.getByText(/RFQ created/)).toBeInTheDocument()
    expect(screen.getByText(/retender/)).toBeInTheDocument()
  })
})
