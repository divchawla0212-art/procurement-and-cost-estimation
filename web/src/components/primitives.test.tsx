import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ApprovalPills, Card } from './primitives'

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

describe('Card', () => {
  it('offers no collapse control unless it is asked for', () => {
    // Every card on every screen renders through this component, so the
    // control has to be opt-in: a Hide button on the item detail metrics or
    // the edit form would be a change nobody asked for.
    render(
      <Card title="Client list">
        <p>a row</p>
      </Card>,
    )

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('hides the body and brings it back', () => {
    render(
      <Card title="Client list" collapsible>
        <button type="button">a row</button>
      </Card>,
    )

    const toggle = screen.getByRole('button', { name: /hide/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(toggle)

    // The heading stays: a collapsed card still has to say what it is, or
    // there is nothing left to click to get the list back.
    expect(screen.getByRole('heading', { name: /client list/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'a row' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /show/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )

    fireEvent.click(screen.getByRole('button', { name: /show/i }))

    expect(screen.getByRole('button', { name: 'a row' })).toBeInTheDocument()
  })

  it('names the toggle after the card it collapses', () => {
    // Four collapsible cards sit on the item screen, so a bare "Hide" is four
    // identical controls to anyone reading by accessible name. The heading is
    // referenced rather than duplicated, so the two cannot drift.
    render(
      <Card title="Astra list" collapsible>
        <p>a row</p>
      </Card>,
    )

    expect(
      screen.getByRole('button', { name: /hide astra list/i }),
    ).toBeInTheDocument()
  })

  it('keeps the body mounted, so collapsing loses no half-finished work', () => {
    // The load-bearing one. Rendering the children only while expanded is the
    // obvious implementation and it throws away whatever the reader had typed
    // into the card — on the item screen that is the add-a-vendor form and a
    // list of suggestions that cost a provider call to fetch. Collapsing is a
    // way to get the long table out of the way, not a reset.
    render(
      <Card title="Added by hand" collapsible>
        <input aria-label="Vendor name" />
      </Card>,
    )

    fireEvent.change(screen.getByLabelText('Vendor name'), {
      target: { value: 'HALF TYPED CO' },
    })
    fireEvent.click(screen.getByRole('button', { name: /hide/i }))
    fireEvent.click(screen.getByRole('button', { name: /show/i }))

    expect(screen.getByLabelText('Vendor name')).toHaveValue('HALF TYPED CO')
  })

  it('starts collapsed when it is told to', () => {
    render(
      <Card title="Client list" collapsible defaultCollapsed>
        <button type="button">a row</button>
      </Card>,
    )

    expect(screen.queryByRole('button', { name: 'a row' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /show/i })).toBeInTheDocument()
  })
})
