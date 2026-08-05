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

describe('priced-cell note (BUG-005, Task 6 fix round 1)', () => {
  // Normalised (and final_value, vat) rows are kind: 'priced', rendered by
  // PricedRow/PricedCells — a different path from AttributeRow, which is
  // the only place that used to read `cell.note`. A vendor whose bid could
  // not be converted gets total: null and a note naming the missing
  // currency; the note must reach the screen, not just the API response.
  it('renders a priced cell note when the total is blank', async () => {
    await renderStatement(
      {
        project: 'p',
        currency: 'USD',
        generation: 1,
        vendors: ['EUROVEND'],
        revisions: { EUROVEND: '1' },
        currencies: { EUROVEND: 'EUR' },
        statuses: { EUROVEND: 'ok' },
        rows: [
          {
            key: 'normalised',
            label: 'Normalised (ex-VAT, ex-options, USD)',
            kind: 'priced',
            cells: {
              EUROVEND: {
                qty: null,
                unit_price: null,
                total: null,
                text: null,
                note: 'no FX rate set for EUR',
              },
            },
          },
        ],
      },
      'done',
    )

    expect(screen.getByText('no FX rate set for EUR')).toBeInTheDocument()
  })
})

describe('failed-run banner (I2, final-review report)', () => {
  // See the matching describe block in ComplianceMatrix.test.tsx: this
  // screen only mounts once `has_results` is true, so `status: 'failed'`
  // here means the latest run failed outright but an earlier run's
  // extraction is still in the store.
  const failedBannerText = /most recent ingestion run failed to extract anything/i

  it('is shown on the populated render for a failed run with prior results', async () => {
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
      'failed',
    )

    expect(screen.getByText(failedBannerText)).toBeInTheDocument()
    expect(screen.queryByText(bannerText)).not.toBeInTheDocument()
  })
})
