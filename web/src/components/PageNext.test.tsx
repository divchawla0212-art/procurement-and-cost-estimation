import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { PageNext } from './PageNext'

const navigate = vi.fn()
vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>('react-router')
  return { ...actual, useNavigate: () => navigate }
})

function draw(next: { to: string; label: string } | null) {
  return render(
    <MemoryRouter>
      <PageNext next={next} />
    </MemoryRouter>,
  )
}

describe('PageNext', () => {
  it('names the destination rather than saying "next"', () => {
    draw({ to: '/bid-sets/x/overview', label: 'Overview' })

    // The chevron is decoration: the accessible name is the destination, so a
    // screen reader announces where the button goes and not a glyph.
    expect(screen.getByRole('button', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Next/ })).toBeNull()
  })

  it('goes where it says', () => {
    draw({ to: '/bid-sets/x/overview', label: 'Overview' })

    fireEvent.click(screen.getByRole('button', { name: 'Overview' }))

    expect(navigate).toHaveBeenCalledWith('/bid-sets/x/overview')
  })

  // The load-bearing one. `nextPage` answers null for a screen that cannot be
  // reached yet, and the only correct rendering of that is nothing at all --
  // a disabled primary at the foot of the page is the `Send to 0 vendors`
  // control again.
  it('renders nothing at all when there is nowhere to go', () => {
    const { container } = draw(null)

    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('is a solid primary, and large', () => {
    draw({ to: '/admin', label: 'Users and access' })

    const button = screen.getByRole('button', { name: 'Users and access' })
    expect(button).toHaveClass('btn-primary')
    expect(button).toHaveClass('btn-lg')
  })
})
