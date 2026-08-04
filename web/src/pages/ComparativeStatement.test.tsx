// Task 4 review leftover: the `done_with_failures` banner ("some documents
// failed extraction...") used to render inside the no-vendors structural
// empty-state branch too, stacking a partial-run warning on top of an
// EmptyState explaining there is simply nothing there yet. The banner
// belongs only on the populated render.
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ComparativeStatement } from './ComparativeStatement'
import type { Statement } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchStatement: vi.fn(),
  }
})

import { fetchStatement } from '../api'

const bannerText = /some documents failed extraction/i

async function renderStatement(data: Statement, status: string) {
  vi.mocked(fetchStatement).mockResolvedValue(data)
  render(<ComparativeStatement slug="p" projectName="P" status={status} />)
  await waitFor(() => {
    expect(
      screen.queryByText(/Assembling comparative statement/i),
    ).not.toBeInTheDocument()
  })
}

describe('done_with_failures banner placement (Task 4 review)', () => {
  it('is not shown on the no-vendors empty state', async () => {
    await renderStatement(
      {
        project: 'p',
        currency: 'USD',
        generation: 1,
        vendors: [],
        revisions: {},
        currencies: {},
        statuses: {},
        rows: [],
      },
      'done_with_failures',
    )

    expect(screen.getByText('Nothing to compare yet')).toBeInTheDocument()
    expect(screen.queryByText(bannerText)).not.toBeInTheDocument()
  })

  it('is shown on the populated render', async () => {
    await renderStatement(
      {
        project: 'p',
        currency: 'USD',
        generation: 1,
        vendors: ['ACME'],
        revisions: { ACME: '1' },
        currencies: { ACME: 'USD' },
        statuses: { ACME: 'ok' },
        rows: [],
      },
      'done_with_failures',
    )

    expect(screen.getByText(bannerText)).toBeInTheDocument()
  })
})
