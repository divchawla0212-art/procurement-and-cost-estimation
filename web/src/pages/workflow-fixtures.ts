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
  DraftShortlistEntry,
  ItemVendorEntry,
  Rfq,
  RfqDetail,
  ShortlistEntry,
  SuggestedVendor,
  VendorListSource,
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
  return {
    project: HALIBA_PROJECT,
    items: [GENERATOR],
    rfqs: [],
    item_vendor_lists: {},
    draft_shortlists: {},
    ...over,
  }
}

/** One vendor picked against an item, before any RFQ covers it.
 *
 *  Defaults to a registry pick, because that is the one carrying a `vendor_id`
 *  and therefore the one where getting the shape wrong matters — a curated pick
 *  has `vendor_id: null` and a test wanting one says so. */
export function draftPick(
  over: Partial<DraftShortlistEntry> = {},
): DraftShortlistEntry {
  return {
    id: 'dse_1',
    item_id: 'itm_1',
    vendor_id: 'bdr_almunara',
    vendor_name: 'Al Munara Switchgear LLC',
    source: 'ADNOC',
    added_by: 'buyer@example.com',
    added_at: '2026-08-15T09:00:00Z',
    ...over,
  }
}

/** An item's four lists, with the ones a test does not care about empty.
 *
 *  Spelled out rather than left `Partial`, because the server always sends all
 *  four keys — a fixture that omitted one would let a screen read `undefined`
 *  in a test and a real array in the browser. */
export function vendorLists(
  over: Partial<Record<VendorListSource, ItemVendorEntry[]>> = {},
): Record<VendorListSource, ItemVendorEntry[]> {
  return { Client: [], Astra: [], Manual: [], Suggested: [], ...over }
}

/** One company a model named. No approvals, no registry link — see
 *  `SuggestedVendor`. */
export function suggestion(
  over: Partial<SuggestedVendor> = {},
): SuggestedVendor {
  return {
    name: 'DUCAB HV CABLE',
    country: 'United Arab Emirates',
    supplies: 'HV and MV power cable',
    basis: 'A long-established cable maker in the UAE.',
    ...over,
  }
}

/** One row of an item's vendor list. `vendor_id: null` is the "the registry
 *  does not hold them" state, distinct from unapproved — and it is what every
 *  curated row carries, since the server never looks a typed name up. */
export function vendorEntry(
  over: Partial<ItemVendorEntry> = {},
): ItemVendorEntry {
  return {
    id: 'ive_1',
    item_id: 'itm_1',
    source: 'Client',
    vendor_id: 'bdr_almunara',
    vendor_name: 'AL MUNARA SWITCHGEAR LLC',
    trade_categories: ['CABLES - LV POWER DISTRIBUTION'],
    uploaded_by: 'buyer@example.com',
    uploaded_at: '2026-08-14T09:00:00Z',
    source_document: 'avl.xlsx',
    ...over,
  }
}

/* ------------------------------------------------------------ RFQ detail */

/** One shortlist row. `approved_by` and `client_approved` are both derived
 *  server-side from the same registry row, so a fixture that sets one should
 *  set the other to match — an ADNOC pill beside "not on the ADNOC list" is a
 *  state the server cannot produce. */
export function shortlistEntry(
  over: Partial<ShortlistEntry> = {},
): ShortlistEntry {
  return {
    id: 'sle_1',
    rfq_id: 'rfq_1',
    vendor_id: 'bdr_almunara',
    vendor_name: 'Al Munara Switchgear LLC',
    prequal_status: 'Approved',
    scope_code_fit: true,
    included: true,
    client_approved: true,
    approved_by: ['ADNOC', 'Astra'],
    override_by: null,
    override_reason: null,
    ...over,
  }
}

/** The RFQ payload as the item screen reads it — only `shortlist` and
 *  `client_approver` matter there, but the whole shape is required. */
export const RFQ_DETAIL: RfqDetail = {
  rfq: rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
  gate: { passed: true, reason: null },
  technical_package: null,
  shortlist: [],
  shortlist_approved: false,
  client_approver: 'ADNOC',
  tbe_template: null,
  vdrl: [],
  bids: [],
  bid_selection: null,
  queries: [],
  addenda: [],
  bid_due_date: null,
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
  // On the client's list, so there is no gap to report. The two fixtures that
  // spread this one also carry ADNOC, so they inherit the right value.
  approval_caution: null,
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
