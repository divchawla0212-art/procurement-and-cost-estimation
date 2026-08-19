import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import App from '../App'

const auth = vi.hoisted(() => ({
  value: {
    user: null as null,
    ready: true,
    login: async () => ({ ok: true }),
    signup: async () => ({ ok: true }),
    logout: async () => {},
  },
}))

vi.mock('../auth/context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/context')>()
  return { ...actual, useAuth: () => auth.value }
})

vi.mock('../api', () => ({
  fetchProjects: vi.fn(async () => []),
}))

describe('Landing page', () => {
  it('renders the BKS marketing hero when signed out at /', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getAllByText('BKS AI Procurement').length).toBeGreaterThan(0)
    expect(
      screen.getByRole('heading', { level: 1, name: /Procurement & cost estimation/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Enter workspace/i })).toBeInTheDocument()
    expect(screen.getByText(/Built for enterprises like/i)).toBeInTheDocument()
  })
})
