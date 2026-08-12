// M1 (final-review report): `Setup.test.tsx` asserts that
// `runIngestion('p', 'mock', true)` is called against a *mocked* `../api`
// module, and `tests/test_api_setup.py` asserts on a `{"force": true}` body
// arriving at the server. Nothing tested that `runIngestion`'s third
// argument actually becomes `body.force` in between — if the field were
// misnamed (or the boolean silently dropped) in `api.ts` itself, both
// suites would stay green. This mocks `fetch` directly and asserts on the
// request `api.ts` actually sends.
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { runIngestion } from './api'
import type { ProjectSetup } from './types'

const setup: ProjectSetup = {
  slug: 'p',
  name: 'P',
  target_currency: 'USD',
  requirements_file: null,
  vendors: [],
  fx_rates: {},
  status: 'done',
  generation: 1,
  has_results: true,
  provider: { provider: 'mock', needs_key: null, ready: true, catalog: [] },
}

function mockFetchOk() {
  return vi.fn(async () => ({
    ok: true,
    json: async () => setup,
  })) as unknown as typeof fetch
}

describe('runIngestion request body', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('sends force: true when the caller passes force=true', async () => {
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)

    await runIngestion('p', 'mock', true)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/projects/p/ingest')
    expect(JSON.parse(init.body as string)).toEqual({
      provider: 'mock',
      force: true,
    })

    vi.unstubAllGlobals()
  })

  it('omits force entirely when the caller passes force=false', async () => {
    // Design spec §1.2, guard 1: the field defaults to `false` server-side,
    // so a plain run must send a request an older client would also send —
    // no `force` key at all, not `force: false`.
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)

    await runIngestion('p', 'mock', false)

    const [, init] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body as string)).toEqual({ provider: 'mock' })

    vi.unstubAllGlobals()
  })

  it('omits force entirely when the caller omits the argument', async () => {
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)

    await runIngestion('p', 'mock')

    const [, init] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body as string)).toEqual({ provider: 'mock' })

    vi.unstubAllGlobals()
  })

  it('omits provider when the caller omits it, independent of force', async () => {
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)

    await runIngestion('p', undefined, true)

    const [, init] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body as string)).toEqual({ force: true })

    vi.unstubAllGlobals()
  })
})
