/**
 * Shared fixtures for the project → item screens' tests.
 *
 * In their own module rather than exported from one of the `.test.tsx` files:
 * importing a test file from a test file re-runs its `describe` blocks in the
 * importing suite, and its `vi.mock('../api')` factory — which mocks a
 * different set of functions — is hoisted and wins over the importer's. That
 * produces "mockResolvedValue is not a function" on whichever fetcher the
 * other file did not happen to mock.
 *
 * Nothing in `src/` imports this at runtime, so it never reaches the bundle.
 */
import type {
  BidderSummary,
  Rfq,
  WorkflowItem,
  WorkflowProject,
  WorkflowProjectDetail,
  WorkflowProjectSummary,
} from '../types'

/** `WorkflowProjectDetail.project` is a `WorkflowProject` — the roster's counts
 *  are not on it, because that response carries the actual lists. */
export const HALIBA_PROJECT: WorkflowProject = {
  id: 'prj_1',
  name: 'Haliba Field Development',
  code: 'HAL',
  client: 'Al Dhafra Petroleum',
  location: 'Haliba field, UAE',
  live_period_start: '2026-01-01',
  live_period_end: '2029-12-31',
  currency: 'AED',
  status: 'Active',
}

export const HALIBA_ROW: WorkflowProjectSummary = {
  ...HALIBA_PROJECT,
  item_count: 4,
  rfq_count: 1,
}

export const GENERATOR: WorkflowItem = {
  id: 'itm_1',
  project_id: 'prj_1',
  item_type: 'Gas generator',
  description: '2 x 5 MW containerised',
  qty: 2,
  uom: 'no',
  discipline: 'Electrical',
  estimated_value_aed: 18_000_000,
  required_on_site: '2027-06-01',
  is_long_lead: true,
}

export const CABLE: WorkflowItem = {
  ...GENERATOR,
  id: 'itm_2',
  item_type: 'HV cable',
  description: '11 kV, 3-core',
  qty: 1200,
  uom: 'm',
  estimated_value_aed: 900_000,
  is_long_lead: false,
}

export function rfq(id: string, reference: string, itemIds: string[]): Rfq {
  return {
    id,
    reference,
    project_id: 'prj_1',
    item_ids: itemIds,
    package: 'Power generation',
    discipline: 'Electrical',
    value_estimate_aed: 18_000_000,
    stage: 'Scoping',
    history: [
      {
        from_stage: null,
        to_stage: 'Scoping',
        at: '2026-08-12T09:00:00Z',
        by: 'system',
        reason: 'RFQ created',
      },
    ],
  }
}

export function detail(
  over: Partial<WorkflowProjectDetail> = {},
): WorkflowProjectDetail {
  return { project: HALIBA_PROJECT, items: [GENERATOR], rfqs: [], ...over }
}

/* --------------------------------------------------------------- bidders */

export const APPROVED_BIDDER: BidderSummary = {
  id: 'bdr_almunara',
  name: 'Al Munara Switchgear LLC',
  country: 'United Arab Emirates',
  currency: 'AED',
  approved_by: ['ADNOC', 'Astra'],
  trade_categories: ['SWITCHGEARS - LV -415V'],
  prequal_status: 'Approved',
  prequal_expires_on: '2028-03-31',
  on_hold: false,
  hold_reason: null,
  turnover_band: 'AED 50–100m',
  performance_rating: 4.4,
  past_awards: 7,
  represented_manufacturers: ['SCHNEIDER ELECTRIC'],
  notes: null,
  effective_prequal: 'Approved',
  invited_count: 0,
}

/** Stored as `Approved`; the server's derived value says otherwise. The gap
 *  between those two fields is the thing the roster has to render correctly. */
export const EXPIRED_BIDDER: BidderSummary = {
  ...APPROVED_BIDDER,
  id: 'bdr_sandstone',
  name: 'Sandstone Piping Industries',
  approved_by: ['ADNOC'],
  trade_categories: ['FLANGES FOR PIPES - CS/AS/SS'],
  // Its own, not the spread's: the search test needs a manufacturer that
  // matches this bidder and not the other one.
  represented_manufacturers: ['METALFAR SPA'],
  prequal_expires_on: '2026-05-09',
  effective_prequal: 'Expired',
}

export const SUSPENDED_BIDDER: BidderSummary = {
  ...APPROVED_BIDDER,
  id: 'bdr_gulfcrescent',
  name: 'Gulf Crescent Fabricators',
  approved_by: ['ADNOC'],
  trade_categories: ['STEEL STRUCTURE FABRICATED'],
  prequal_status: 'Suspended',
  effective_prequal: 'Suspended',
}
