// M1 (final-review report): `Setup.test.tsx` asserts that
// `runIngestion('p', 'mock', true)` is called against a *mocked* `../api`
// module, and `tests/test_api_setup.py` asserts on a `{"force": true}` body
// arriving at the server. Nothing tested that `runIngestion`'s third
// argument actually becomes `body.force` in between — if the field were
// misnamed (or the boolean silently dropped) in `api.ts` itself, both
// suites would stay green. This mocks `fetch` directly and asserts on the
// request `api.ts` actually sends.
import { describe, expect, it, vi, beforeEach } from 'vitest'
import {
  deleteWorkflowItem,
  fetchWorkflowProjects,
  runIngestion,
  updateWorkflowItem,
  updateWorkflowProject,
} from './api'
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


// The workflow project and item fetchers. Same reasoning as the block above:
// the screens mock `../api` wholesale, so nothing else asserts that these send
// the request they claim to — a misnamed field or a lost method would leave
// every other suite green.
describe('workflow project and item requests', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  function mockJson(body: unknown, status = 200) {
    return vi.fn(async () => ({
      ok: status < 400,
      status,
      statusText: 'x',
      json: async () => body,
    })) as unknown as typeof fetch
  }

  function callOf(fetchMock: typeof fetch, index = 0) {
    return (fetchMock as unknown as ReturnType<typeof vi.fn>).mock.calls[index]
  }

  it('unwraps the roster envelope into a plain array', async () => {
    const fetchMock = mockJson({ projects: [{ id: 'prj_1' }] })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchWorkflowProjects()).resolves.toEqual([{ id: 'prj_1' }])
    expect(callOf(fetchMock)[0]).toBe('/api/workflow/projects')
  })

  it('patches a project with only the fields it was given', async () => {
    const fetchMock = mockJson({ id: 'prj_1' })
    vi.stubGlobal('fetch', fetchMock)

    await updateWorkflowProject('prj_1', { status: 'On Hold' })

    const [url, init] = callOf(fetchMock)
    expect(url).toBe('/api/workflow/projects/prj_1')
    expect(init.method).toBe('PATCH')
    // Only the changed field: the server reads the body with `exclude_unset`,
    // so anything sent here is something the user asked to change.
    expect(JSON.parse(init.body as string)).toEqual({ status: 'On Hold' })
  })

  it('patches an item under its own project path', async () => {
    const fetchMock = mockJson({ id: 'itm_1', live_period_warning: null })
    vi.stubGlobal('fetch', fetchMock)

    await updateWorkflowItem('prj_1', 'itm_1', { qty: 3 })

    const [url, init] = callOf(fetchMock)
    expect(url).toBe('/api/workflow/projects/prj_1/items/itm_1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({ qty: 3 })
  })

  it('deletes an item and expects no body back', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 204,
      json: async () => {
        throw new Error('204 has no body — parsing one would throw')
      },
    })) as unknown as typeof fetch
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteWorkflowItem('prj_1', 'itm_1')).resolves.toBeUndefined()
    expect(callOf(fetchMock)[0]).toBe('/api/workflow/projects/prj_1/items/itm_1')
    expect(callOf(fetchMock)[1].method).toBe('DELETE')
  })

  it("raises the server's own sentence when a delete is refused", async () => {
    vi.stubGlobal(
      'fetch',
      mockJson(
        {
          detail:
            'This item cannot be deleted: it is covered by ADP-RFQ-2026-014. Amend or retender first.',
        },
        409,
      ),
    )

    // Verbatim, because it names the RFQ that blocks the delete — the only
    // actionable thing on the screen at that moment.
    await expect(deleteWorkflowItem('prj_1', 'itm_1')).rejects.toThrow(
      /covered by ADP-RFQ-2026-014/,
    )
  })

  it('percent-encodes ids rather than pasting them into the path', async () => {
    const fetchMock = mockJson({ id: 'prj_1' })
    vi.stubGlobal('fetch', fetchMock)

    await updateWorkflowProject('prj a/b', { name: 'X' })

    expect(callOf(fetchMock)[0]).toBe('/api/workflow/projects/prj%20a%2Fb')
  })
})
