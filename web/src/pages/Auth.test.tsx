import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { Auth } from './Auth'

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

function renderAuth() {
  return render(
    <MemoryRouter>
      <Auth />
    </MemoryRouter>,
  )
}

describe('Auth', () => {
  beforeEach(() => {
    auth.value.login.mockClear()
    auth.value.signup.mockClear()
  })

  it('fills both credentials when the business admin demo is clicked', () => {
    renderAuth()

    fireEvent.click(screen.getByRole('button', { name: /Business Admin/ }))

    expect(screen.getByLabelText('Email')).toHaveValue('admin@gmail.com')
    expect(screen.getByLabelText('Password')).toHaveValue('Admin@1234')
  })

  it('fills the form but does not submit it', () => {
    renderAuth()

    fireEvent.click(screen.getByRole('button', { name: /Business Admin/ }))

    expect(auth.value.login).not.toHaveBeenCalled()
  })

  it('leaves the sign-in button focused, with nothing left to type', () => {
    renderAuth()

    fireEvent.click(screen.getByRole('button', { name: /Business Admin/ }))

    expect(screen.getByRole('button', { name: 'Sign in' })).toHaveFocus()
  })

  it('does not offer demo accounts when creating an account', () => {
    renderAuth()

    fireEvent.click(screen.getByRole('button', { name: 'Sign up' }))

    expect(screen.queryByText('Demo Accounts')).toBeNull()
  })

  it('fills only the email for demo roles without published passwords', () => {
    renderAuth()

    fireEvent.click(screen.getByRole('button', { name: /Project Lead/ }))

    expect(screen.getByLabelText('Email')).toHaveValue('lead@bks.ai')
    expect(screen.getByLabelText('Password')).toHaveValue('')
  })
})
