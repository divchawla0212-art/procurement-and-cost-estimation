import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { BackForwardControls, NavHistoryProvider } from './NavHistory'

/**
 * A harness with somewhere to go. The buttons under test are the real ones; the
 * links are stand-ins for whatever a screen would have offered.
 */
function Harness() {
  const navigate = useNavigate()
  const loc = useLocation()
  return (
    <>
      <BackForwardControls />
      <div data-testid="location">{loc.pathname}</div>
      {['/a', '/b', '/c'].map((to) => (
        <button key={to} type="button" onClick={() => navigate(to)}>
          go {to}
        </button>
      ))}
      <button type="button" onClick={() => navigate('/replaced', { replace: true })}>
        replace
      </button>
    </>
  )
}

function renderHarness() {
  return render(
    <MemoryRouter initialEntries={['/start']}>
      <NavHistoryProvider>
        <Harness />
      </NavHistoryProvider>
    </MemoryRouter>,
  )
}

const back = () => screen.getByRole('button', { name: 'Go back' })
const forward = () => screen.getByRole('button', { name: 'Go forward' })
const go = (to: string) => fireEvent.click(screen.getByRole('button', { name: `go ${to}` }))
const at = () => screen.getByTestId('location').textContent

/** `[canBack, canForward]`, read off the buttons rather than the model, so the
 *  assertion covers the wiring as well as the rule. */
const state = () => [!back().hasAttribute('disabled'), !forward().hasAttribute('disabled')]

describe('BackForwardControls', () => {
  it('offers neither direction on a fresh load', () => {
    renderHarness()
    expect(state()).toEqual([false, false])
  })

  it('opens back, and only back, after one navigation', () => {
    renderHarness()
    go('/a')
    expect(state()).toEqual([true, false])
  })

  it('goes back, and then offers forward', () => {
    renderHarness()
    go('/a')
    fireEvent.click(back())
    expect(at()).toBe('/start')
    expect(state()).toEqual([false, true])
  })

  it('goes forward again', () => {
    renderHarness()
    go('/a')
    fireEvent.click(back())
    fireEvent.click(forward())
    expect(at()).toBe('/a')
    expect(state()).toEqual([true, false])
  })

  it('walks a longer sequence in both directions', () => {
    renderHarness()
    go('/a')
    go('/b')
    go('/c')
    expect(state()).toEqual([true, false])

    fireEvent.click(back())
    expect(at()).toBe('/b')
    expect(state()).toEqual([true, true])

    fireEvent.click(back())
    fireEvent.click(back())
    expect(at()).toBe('/start')
    expect(state()).toEqual([false, true])
  })

  // The invariant `history-stack.ts` exists to hold: navigating somewhere new
  // after going back abandons the old forward branch, so Forward must close.
  it('closes forward when a new navigation replaces the branch', () => {
    renderHarness()
    go('/a')
    go('/b')
    fireEvent.click(back())
    expect(state()).toEqual([true, true])

    go('/c')
    expect(at()).toBe('/c')
    expect(state()).toEqual([true, false])
  })

  // A replace stands in for a guard redirect: it must not lengthen the history,
  // so Back still reaches what was behind it rather than the URL it replaced.
  it('does not let a replace lengthen the history', () => {
    renderHarness()
    go('/a')
    fireEvent.click(screen.getByRole('button', { name: 'replace' }))
    expect(at()).toBe('/replaced')
    expect(state()).toEqual([true, false])

    fireEvent.click(back())
    expect(at()).toBe('/start')
    expect(state()).toEqual([false, true])
  })

  it('names both controls for a screen reader', () => {
    renderHarness()
    expect(back()).toHaveAccessibleName('Go back')
    expect(forward()).toHaveAccessibleName('Go forward')
  })
})
