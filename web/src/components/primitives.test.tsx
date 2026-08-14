import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ApprovalPills } from './primitives'

describe('ApprovalPills', () => {
  it('marks each approver with its own class', () => {
    render(<ApprovalPills approvers={['ADNOC', 'Astra']} />)

    expect(screen.getByText('ADNOC')).toHaveClass('approval-badge--adnoc')
    expect(screen.getByText('Astra')).toHaveClass('approval-badge--astra')
  })

  it('renders an unknown approver in the base style rather than dropping it', () => {
    // A second client's list has to show up uncoloured, never vanish. That is
    // why the class is a slug and not a lookup: the cascade gives the honest
    // answer for a name the CSS does not know.
    render(<ApprovalPills approvers={['Borouge']} />)

    const pill = screen.getByText('Borouge')
    expect(pill).toHaveClass('approval-badge')
    expect(pill.className).toContain('approval-badge--borouge')
  })

  it('renders nothing for an empty list', () => {
    const { container } = render(<ApprovalPills approvers={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
