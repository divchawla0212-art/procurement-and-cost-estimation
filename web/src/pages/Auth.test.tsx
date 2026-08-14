// The sign-in screen's admin-account shortcut. It fills the email field and
// nothing else: there is no passwordless sign-in behind it, and these tests
// are what say so.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Auth } from './Auth'

// Built once outside the factory so `useAuth()` returns a stable identity —
// the same rule as `src/App.test.tsx`'s mock, for the same reason.
const auth = vi.hoisted(() => ({
  value: {
    user: null,
    ready: true,
    login: vi.fn(async () => ({ ok: true })),
    signup: vi.fn(async () => ({ ok: true })),
    logout: vi.fn(async () => {}),
  },
}))

vi.mock('../auth/context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/context')>()
  return { ...actual, useAuth: () => auth.value }
})

describe('Auth', () => {
  beforeEach(() => {
    auth.value.login.mockClear()
    auth.value.signup.mockClear()
  })

  it('fills both credentials when its chip is clicked', () => {
    render(<Auth />)

    fireEvent.click(screen.getByRole('button', { name: 'admin@gmail.com' }))

    expect(screen.getByLabelText('Email')).toHaveValue('admin@gmail.com')
    expect(screen.getByLabelText('Password')).toHaveValue('Admin@1234')
  })

  it('fills the form but does not submit it', () => {
    // The chip stops at filling the fields: signing in stays a second,
    // deliberate press of the button. A `<button>` inside a `<form>` submits by
    // default, so this fails the moment somebody drops the explicit
    // type="button" — and it would fail silently, because the form is filled
    // and the sign-in would succeed.
    render(<Auth />)

    fireEvent.click(screen.getByRole('button', { name: 'admin@gmail.com' }))

    expect(auth.value.login).not.toHaveBeenCalled()
  })

  it('leaves the sign-in button focused, with nothing left to type', () => {
    render(<Auth />)

    fireEvent.click(screen.getByRole('button', { name: 'admin@gmail.com' }))

    expect(screen.getByRole('button', { name: 'Sign in' })).toHaveFocus()
  })

  it('does not offer the chip when creating an account', () => {
    // That address is already registered, so signing up with it would 409 —
    // a shortcut that can only fail.
    render(<Auth />)

    fireEvent.click(screen.getByRole('button', { name: /Need an account/ }))

    expect(screen.queryByRole('button', { name: 'admin@gmail.com' })).toBeNull()
  })

  it('renders the address as typed rather than capitalised', () => {
    // `.chip` carries `text-transform: capitalize` (theme.css), which renders
    // this address as "Admin@Gmail.Com". jsdom applies no stylesheet, so the
    // opt-out class is as much of that defect as a test can see — the render
    // itself is only ever caught by running the app.
    render(<Auth />)

    expect(screen.getByRole('button', { name: 'admin@gmail.com' })).toHaveClass('chip--asis')
  })
})
