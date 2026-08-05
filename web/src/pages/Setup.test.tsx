// BUG-002 (BUGS_TRACKER.md): a forced re-extraction is reachable from the UI
// only as a separate, secondary action, shown solely once there are results
// to force over, and confirmed before it fires (design spec §1.2). This also
// covers Task 1's leftover: Setup.tsx must render a null `provider` (BUG-004,
// no LLM_PROVIDER configured) as "no provider configured" rather than
// crashing or rendering a blank/"null" label.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Setup } from './Setup'
import type { ProjectSetup } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchSetup: vi.fn(),
    runIngestion: vi.fn(),
    saveFxRates: vi.fn(),
  }
})

import { fetchSetup, runIngestion, saveFxRates } from '../api'

// No `clearMocks` in vitest.config.ts, so without this, `runIngestion`'s call
// history (and any lingering mockResolvedValue) carries over between tests in
// this file: a `.not.toHaveBeenCalled()` assertion would pass only by dint of
// running first, and a `.toHaveBeenCalledWith(...)` assertion would match the
// *accumulated* history rather than the click this test made.
beforeEach(() => {
  vi.clearAllMocks()
})

const baseSetup: ProjectSetup = {
  slug: 'p',
  name: 'P',
  target_currency: 'USD',
  requirements_file: 'spec.pdf',
  vendors: [{ name: 'ACME', file_count: 1, quote: 'quote.txt' }],
  fx_rates: {},
  status: 'done',
  generation: 1,
  has_results: true,
  provider: {
    provider: 'mock',
    needs_key: null,
    ready: true,
    catalog: [
      { id: 'anthropic', needs_key: 'ANTHROPIC_API_KEY', ready: false },
      { id: 'mock', needs_key: null, ready: true },
    ],
  },
}

const noopProps = {
  onNew: () => {},
  reload: () => {},
  onOpen: () => {},
}

async function renderSetup(setup: ProjectSetup) {
  vi.mocked(fetchSetup).mockResolvedValue(setup)
  render(<Setup slug="p" onCreated={() => {}} {...noopProps} />)
  await waitFor(() => {
    expect(
      screen.getByRole('button', { name: 'Run ingestion' }),
    ).toBeInTheDocument()
  })
}

describe('null provider render (BUG-004 leftover)', () => {
  it('renders "No provider configured" instead of blank or crashing', async () => {
    await renderSetup({
      ...baseSetup,
      has_results: false,
      provider: { ...baseSetup.provider, provider: null, ready: false },
    })

    expect(
      screen.getByText('No provider configured. Choose one below.'),
    ).toBeInTheDocument()
  })
})

describe('the force bypass button (BUG-002)', () => {
  it('is not shown before the first run has produced results', async () => {
    await renderSetup({ ...baseSetup, has_results: false, status: 'new' })

    expect(
      screen.queryByRole('button', { name: /Force full re-extraction/i }),
    ).not.toBeInTheDocument()
  })

  it('is shown, separate from Run ingestion, once results exist', async () => {
    await renderSetup(baseSetup)

    const runButton = screen.getByRole('button', { name: 'Run ingestion' })
    const forceButton = screen.getByRole(
      'button',
      { name: /Force full re-extraction/i },
    )
    expect(runButton).toBeInTheDocument()
    expect(forceButton).toBeInTheDocument()
    expect(forceButton).not.toBe(runButton)
  })

  it('confirms before firing, and does not call the API if the user declines', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderSetup(baseSetup)

    screen.getByRole('button', { name: /Force full re-extraction/i }).click()

    expect(confirmSpy).toHaveBeenCalled()
    expect(runIngestion).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('calls runIngestion with force=true once the user confirms', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(runIngestion).mockResolvedValue(baseSetup)
    await renderSetup(baseSetup)

    screen.getByRole('button', { name: /Force full re-extraction/i }).click()

    await waitFor(() => {
      expect(runIngestion).toHaveBeenCalledWith('p', 'mock', true)
    })
    confirmSpy.mockRestore()
  })

  it('the plain Run ingestion button never passes force', async () => {
    vi.mocked(runIngestion).mockResolvedValue(baseSetup)
    await renderSetup(baseSetup)

    screen.getByRole('button', { name: 'Run ingestion' }).click()

    await waitFor(() => {
      expect(runIngestion).toHaveBeenCalledWith('p', 'mock')
    })
  })
})

describe('FX rate suggestion (BUG-005 §1.4)', () => {
  // The FX card's prefill is set by FxStep's own useEffect, a render pass
  // that happens after the "Run ingestion" button (renderSetup's readiness
  // signal) is already on screen. So these assertions use `find*` queries
  // (which retry) rather than `get*`/`query*`, to avoid a race against that
  // second render.
  it('offers EUR 1.08 when no rates are set and the target is USD', async () => {
    await renderSetup({ ...baseSetup, fx_rates: {}, target_currency: 'USD' })

    expect(await screen.findByDisplayValue('EUR')).toBeInTheDocument()
    expect(await screen.findByDisplayValue('1.08')).toBeInTheDocument()
    expect(
      await screen.findByText(
        /Suggested: 1\.08 as of 2026-08-05 — check before saving\./,
      ),
    ).toBeInTheDocument()
  })

  it('does not offer it when the target is not USD', async () => {
    await renderSetup({ ...baseSetup, fx_rates: {}, target_currency: 'GBP' })

    // Nothing ever populates a row here, so there is no later state to race
    // against — an immediate absence check is safe.
    expect(screen.queryByDisplayValue('1.08')).not.toBeInTheDocument()
  })

  it('does not offer it when rates already exist', async () => {
    await renderSetup({
      ...baseSetup,
      fx_rates: { EUR: 1.12 },
      target_currency: 'USD',
    })

    expect(await screen.findByDisplayValue('1.12')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('1.08')).not.toBeInTheDocument()
  })

  it('does not save the suggestion on its own', async () => {
    await renderSetup({ ...baseSetup, fx_rates: {}, target_currency: 'USD' })

    await screen.findByDisplayValue('1.08')
    expect(saveFxRates).not.toHaveBeenCalled()
  })
})
