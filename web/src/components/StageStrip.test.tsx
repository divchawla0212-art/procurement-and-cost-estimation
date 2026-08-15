import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StageStrip } from './StageStrip'

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

const NO_COUNTS: Record<string, number> = {}

// Real codes, not a position: RFQ-04A has no ordinal home, and Negotiation and
// Awarded deliberately share RFQ-06 — the case a positional strip gets wrong.
const CODES: Record<string, string> = {
  Scoping: 'RFQ-01',
  Shortlisting: 'RFQ-02',
  Issued: 'RFQ-03',
  Clarifications: 'RFQ-04',
  'Bids Received': 'RFQ-04A',
  Evaluation: 'RFQ-05',
  Negotiation: 'RFQ-06',
  Awarded: 'RFQ-06',
  'PO Issued': 'RFQ-07',
}

describe('StageStrip', () => {
  it('renders every stage the server sent, with the code the server sent for it', () => {
    render(<StageStrip stages={STAGES} counts={NO_COUNTS} codes={CODES} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(9)
    expect(items.map((li) => li.textContent)).toEqual(
      STAGES.map((s) => `${CODES[s]}${s}0`),
    )
  })

  it('renders the same code for two stages that share one process step', () => {
    // Negotiation and Awarded are two states of one process step, RFQ-06 —
    // a strip computing a code from position would give them different ones.
    render(<StageStrip stages={STAGES} counts={NO_COUNTS} codes={CODES} />)
    const items = screen.getAllByRole('listitem')
    expect(items[6]).toHaveTextContent('RFQ-06') // Negotiation
    expect(items[7]).toHaveTextContent('RFQ-06') // Awarded
  })

  it('marks only the current stage, and marks earlier ones done', () => {
    const { container } = render(
      <StageStrip stages={STAGES} counts={NO_COUNTS} codes={CODES} current="Clarifications" />,
    )
    expect(screen.getAllByRole('listitem', { current: 'step' })).toHaveLength(1)
    expect(screen.getByRole('listitem', { current: 'step' })).toHaveTextContent(
      'Clarifications',
    )
    // Scoping, Shortlisting, Issued — everything before the current stage
    expect(container.querySelectorAll('.stagestep.is-done')).toHaveLength(3)
  })

  it('marks nothing current on a roster view, where no RFQ is in focus', () => {
    const { container } = render(<StageStrip stages={STAGES} counts={NO_COUNTS} codes={CODES} />)
    expect(screen.queryAllByRole('listitem', { current: 'step' })).toHaveLength(0)
    expect(container.querySelectorAll('.stagestep.is-done')).toHaveLength(0)
  })

  it('shows a zero for a stage the counts omit rather than blank or NaN', () => {
    render(
      <StageStrip
        stages={['Scoping', 'Issued']}
        counts={{ Scoping: 4 }}
        codes={{ Scoping: 'RFQ-01', Issued: 'RFQ-03' }}
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('4')
    expect(items[1]).toHaveTextContent('0')
  })

  it('renders whatever stage vocabulary the server sends, not a hard-coded nine', () => {
    render(
      <StageStrip
        stages={['Only Stage']}
        counts={{ 'Only Stage': 1 }}
        codes={{ 'Only Stage': 'RFQ-01' }}
      />,
    )
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Only Stage')).toBeInTheDocument()
  })
})
