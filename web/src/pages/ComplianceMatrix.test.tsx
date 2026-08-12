// Task 4 review leftover: the `done_with_failures` banner ("some documents
// failed extraction...") used to render inside the structural empty-state
// branches too, stacking a partial-run warning on top of an EmptyState
// explaining there is simply nothing there yet. Two unrelated reasons for
// emptiness, mixed. The banner belongs only on the populated render, where
// the user is looking at real (possibly incomplete) data.
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ComplianceMatrix } from './ComplianceMatrix'
import type { ComplianceMatrix as ComplianceMatrixData, MatrixRow } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchComplianceMatrix: vi.fn(),
  }
})

import { fetchComplianceMatrix } from '../api'

const bannerText = /some documents failed extraction/i

const baseCoverage = {
  auto_cells: 1,
  stated_cells: 0,
  by_verdict: { pass: 1 },
  unanswered_silent: 0,
  unanswered_refused: 0,
}

const oneRow: MatrixRow = {
  req_id: 'r1',
  clause_ref: '1.1',
  text: 'Widget must be blue',
  checkability: 'auto',
  parameter: 'colour',
  operator: '=',
  value: 'blue',
  unit: null,
  cells: {
    ACME: {
      vendor: 'ACME',
      verdict: 'pass',
      group: 'matched',
      rationale: 'stated blue',
      fact_id: 'f1',
      doc_id: 'd1',
      candidate_fact_ids: [],
      doc_name: 'quote.pdf',
      candidate_doc_names: [],
    },
  },
}

async function renderMatrix(data: ComplianceMatrixData, status: string) {
  vi.mocked(fetchComplianceMatrix).mockResolvedValue(data)
  render(
    <ComplianceMatrix slug="p" projectName="P" status={status} />,
  )
  await waitFor(() => {
    expect(screen.queryByText(/Building compliance matrix/i)).not.toBeInTheDocument()
  })
}

describe('done_with_failures banner placement (Task 4 review)', () => {
  it('is not shown on the no-rows empty state', async () => {
    await renderMatrix(
      {
        vendors: ['ACME'],
        rows: [],
        coverage: baseCoverage,
        groups: { not_matched: [], needs_human: [], matched: [] },
      },
      'done_with_failures',
    )

    expect(screen.getByText('No matrix yet')).toBeInTheDocument()
    expect(screen.queryByText(bannerText)).not.toBeInTheDocument()
  })

  it('is not shown on the no-vendors empty state', async () => {
    await renderMatrix(
      {
        vendors: [],
        rows: [oneRow],
        coverage: baseCoverage,
        groups: { not_matched: [], needs_human: [], matched: [oneRow] },
      },
      'done_with_failures',
    )

    expect(screen.getByText('No vendors')).toBeInTheDocument()
    expect(screen.queryByText(bannerText)).not.toBeInTheDocument()
  })

  it('is shown on the populated render', async () => {
    await renderMatrix(
      {
        vendors: ['ACME'],
        rows: [oneRow],
        coverage: baseCoverage,
        groups: { not_matched: [], needs_human: [], matched: [oneRow] },
      },
      'done_with_failures',
    )

    expect(screen.getByText(bannerText)).toBeInTheDocument()
  })
})

describe('failed-run banner (I2, final-review report)', () => {
  // This screen only mounts once `has_results` is true (`nav.ts`), so a
  // `status: 'failed'` render here means the latest run failed outright but
  // an earlier run's extraction is still in the store (CLAUDE.md: "a failed
  // extraction never blanks previously-good stored data"). That is a
  // different situation from `done_with_failures` (some documents in *this*
  // run failed), so it gets its own banner text, not the partial-run one.
  const failedBannerText = /most recent ingestion run failed to extract anything/i

  it('is shown on the populated render for a failed run with prior results', async () => {
    await renderMatrix(
      {
        vendors: ['ACME'],
        rows: [oneRow],
        coverage: baseCoverage,
        groups: { not_matched: [], needs_human: [], matched: [oneRow] },
      },
      'failed',
    )

    expect(screen.getByText(failedBannerText)).toBeInTheDocument()
    expect(screen.queryByText(bannerText)).not.toBeInTheDocument()
  })

  it('is not shown for a clean run', async () => {
    await renderMatrix(
      {
        vendors: ['ACME'],
        rows: [oneRow],
        coverage: baseCoverage,
        groups: { not_matched: [], needs_human: [], matched: [oneRow] },
      },
      'done',
    )

    expect(screen.queryByText(failedBannerText)).not.toBeInTheDocument()
  })
})
