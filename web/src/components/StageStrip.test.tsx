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

describe('StageStrip', () => {
  it('renders every stage the server sent, in the order it sent them', () => {
    render(<StageStrip stages={STAGES} counts={NO_COUNTS} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(9)
    expect(items.map((li) => li.textContent)).toEqual(
      STAGES.map((s, i) => `RFQ-${String(i + 1).padStart(2, '0')}${s}0`),
    )
  })

  it('marks only the current stage, and marks earlier ones done', () => {
    const { container } = render(
      <StageStrip stages={STAGES} counts={NO_COUNTS} current="Clarifications" />,
    )
    expect(screen.getAllByRole('listitem', { current: 'step' })).toHaveLength(1)
    expect(screen.getByRole('listitem', { current: 'step' })).toHaveTextContent(
      'Clarifications',
    )
    // Scoping, Shortlisting, Issued — everything before the current stage
    expect(container.querySelectorAll('.stagestep.is-done')).toHaveLength(3)
  })

  it('marks nothing current on a roster view, where no RFQ is in focus', () => {
    const { container } = render(<StageStrip stages={STAGES} counts={NO_COUNTS} />)
    expect(screen.queryAllByRole('listitem', { current: 'step' })).toHaveLength(0)
    expect(container.querySelectorAll('.stagestep.is-done')).toHaveLength(0)
  })

  it('shows a zero for a stage the counts omit rather than blank or NaN', () => {
    render(<StageStrip stages={['Scoping', 'Issued']} counts={{ Scoping: 4 }} />)
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('4')
    expect(items[1]).toHaveTextContent('0')
  })

  it('renders whatever stage vocabulary the server sends, not a hard-coded nine', () => {
    render(<StageStrip stages={['Only Stage']} counts={{ 'Only Stage': 1 }} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Only Stage')).toBeInTheDocument()
  })
})
