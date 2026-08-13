# Bidder registry and shortlisting — design

**Date:** 2026-08-13
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-13-rfq-platform-phases-2-5-readiness.md`](../plans/2026-08-13-rfq-platform-phases-2-5-readiness.md)

---

## The problem

Today a vendor first exists at the moment somebody types their name into the
RFQ wizard's Shortlisting step. `ShortlistEntry.vendor_name` is free text and
`prequal_status` is a free-text field the client supplies. Three consequences:

1. **There is no bidder before there is an RFQ.** Prequalification is an
   organisation-level fact with a validity period, and it is being re-asserted
   per RFQ, by hand, from memory.
2. **The prequal signal is client-supplied.** Nothing stops a caller posting
   `prequal_status: "Qualified"` for a vendor procurement suspended last month.
   `ShortlistEntry`'s own docstring says including a vendor against the prequal
   signal should be "a positive, attributed act"; nothing enforces it.
3. **The same company is retyped for every RFQ**, so no question that spans
   RFQs — who have we invited, who keeps declining, whose approval lapses next
   month — can be answered at all.

The readiness note names "vendor database" as one of phase 2's rows and lists
it among the pieces that were *not* built because they were thought to need
infrastructure. They do not: the registry is a collection in the document
`workflow/persistence.py` already writes.

## What this builds

A **bidder registry** that exists independently of any RFQ, and a **candidate
view** that evaluates that registry against one specific RFQ. The existing
`ShortlistEntry` stops being where vendors are first typed in and becomes what
it is named: the record of a decision to invite one.

The eight stages, `TRANSITIONS`, and every existing gate are untouched.

---

## 1. `Bidder` — `workflow/models/bidder.py`

```python
PrequalStatus = Literal["Approved", "Under review", "Suspended", "Not qualified"]

class Bidder(BaseModel):
    id: str                                 # bdr_xxxxxxxx
    name: str
    country: str
    currency: str = "AED"
    trade_categories: list[str] = []        # matched against an RFQ's discipline/package
    prequal_status: PrequalStatus = "Under review"
    prequal_expires_on: date | None = None
    on_hold: bool = False
    hold_reason: str | None = None
    turnover_band: str | None = None        # e.g. "AED 50–100m"
    performance_rating: float | None = None # 0–5
    past_awards: int = 0
    notes: str | None = None
```

### `Expired` is not a stored status

There is no `"Expired"` member of `PrequalStatus`. Expiry is derived from
`prequal_expires_on` against an `as_of` date the caller passes in. A stored
`"Expired"` is wrong the day after it is written, and a stored `"Approved"` is
wrong the day after *that* — the value would have to be swept by a job nobody
has written, and until that job runs the screen shows a bidder as approved when
they are not. This is the same reasoning that keeps the live-period warning
computed in `_item_payload` rather than persisted on the item.

`as_of` is a parameter rather than a `date.today()` call inside the function,
so the boundary cases are testable without freezing the clock.

### `past_awards` is pre-platform history

It records awards that happened before this system existed and is deliberately
**not** conflated with activity inside it. In-system participation is derived:
the roster reports `invited_count`, computed by scanning shortlist entries that
reference the bidder. One number is remembered, the other is counted, and
mixing them would produce a figure that is neither.

---

## 2. Suitability — `workflow/bidders.py`

A pure module with no store dependency and no I/O:

```python
class Suitability(BaseModel):
    eligible: bool
    scope_fit: bool
    effective_prequal: str      # "Approved" | … | "Expired"
    blockers: list[str]
    cautions: list[str]

def evaluate(bidder: Bidder, rfq: RfqRecord, as_of: date) -> Suitability
```

| condition | classification |
|---|---|
| `on_hold` | blocker, naming `hold_reason` |
| `prequal_status` is not `Approved` | blocker, naming the status |
| `Approved` but `prequal_expires_on < as_of` | blocker, naming the date |
| no `trade_categories` entry matching the RFQ's discipline or package | **caution** — sets `scope_fit=False` |
| `Approved` and expiring within 30 days of `as_of` | caution, naming the date |

**Scope mismatch is a caution, not a blocker.** Inviting a bidder who is moving
into a discipline is a legitimate procurement decision, and `scope_code_fit`
already exists on `ShortlistEntry` precisely to record that the invitation was
made with the mismatch known. Prequalification and holds are different in kind:
they are somebody's explicit refusal, and overriding one has to be a deliberate
act.

**Every blocker names the whole criterion**, not the nearer half of it — the
rule `gates.py` states and the readiness note records as a defect it had to fix
in the phase 1 plan. "Not approved" is not a blocker sentence; "prequalification
lapsed on 2026-04-30 and must be renewed before this bidder can be invited" is.

A matching is case-insensitive and exact on the whole category string. Substring
matching is deliberately *not* used: it is how `classify._RULES` shipped false
matches, per the plan template's Rule 3 table.

---

## 3. Store — `WorkflowStore`

New collection `self._bidders: dict[str, Bidder]`, and:

- `create_bidder(...) -> Bidder`
- `get_bidder(bidder_id) -> Bidder | None`
- `list_bidders() -> list[Bidder]`
- `update_bidder(bidder_id, changes) -> Bidder` — partial and validating, the
  same shape as `update_item`: rebuilt through the model rather than
  `model_copy(update=...)`, which skips validation. `_BIDDER_IMMUTABLE = {"id"}`.
- `delete_bidder(bidder_id) -> None`
- `rfqs_inviting(bidder_id) -> list[str]` — the RFQ references that name it.

### Store invariants this owns

**A. `_bidders` contains exactly the bidders nobody has deleted, and no bidder
is deleted out from under a live reference.** `delete_bidder` is refused while
any `ShortlistEntry` carries its `vendor_id`, and the refusal names the RFQ
references so the reader knows what would unblock it. This is the third place
this repository has needed the rule — `delete_item`, `delete_project`, and now
this — and the reason is the same each time: `persistence.save` replaces the
document wholesale, so a shortlist entry pointing at a deleted bidder survives
the restart as a dangling reference.

**B. A registry-linked shortlist entry's prequal snapshot comes from the
registry, never from the request body.** When `add_shortlist_entry` is given a
`vendor_id`, the entry's `vendor_name`, `prequal_status` and `scope_code_fit`
are derived from the registry at the moment of adding. The corresponding fields
in the request are ignored rather than trusted. Without this the registry is
decoration: a caller could link an entry to a suspended bidder and label it
`Qualified`, and the shortlist would read as approved.

**C. Inviting an ineligible registry bidder requires an override reason.**
`add_shortlist_entry` refuses a `vendor_id` whose `evaluate` returns blockers
unless `override_reason` is supplied; the route attributes it to the session
user, as it already does. This enforces what `ShortlistEntry`'s docstring
already claims.

### Free-text entries are unchanged

An `add_shortlist_entry` call with no `vendor_id` behaves exactly as it does
today — same fields, same client-supplied `prequal_status`, no eligibility
check. Eligibility can only be judged where there is a registry record to judge
against, so the new rules attach to the link, not to the call. Nothing about an
existing stored document, an existing RFQ, or an existing test changes meaning.

`ShortlistEntry` gains one field: `vendor_id: str | None = None`.

---

## 4. Persistence

`to_document` gains a `"bidders"` list; `from_document` gains the matching
rebuild. CLAUDE.md names this file's exact failure mode — a field added to
`WorkflowStore.__init__` without both lines silently fails to survive a restart
— so the round-trip is asserted directly and the mutation matrix gains rows for
the new collection.

No version bump. `VERSION` stays 1: a document written before this change has
no `"bidders"` key, `from_document` reads it as an empty registry, and that is
the correct interpretation rather than a migration.

---

## 5. API — `api/workflow_routes.py`

| Method | Path | Behaviour |
|---|---|---|
| GET | `/api/workflow/bidders` | roster: each bidder plus `effective_prequal` and `invited_count` |
| POST | `/api/workflow/bidders` | 201 |
| GET | `/api/workflow/bidders/{bidder_id}` | the bidder plus the RFQ references inviting it |
| PATCH | `/api/workflow/bidders/{bidder_id}` | partial, `model_dump(exclude_unset=True)` |
| DELETE | `/api/workflow/bidders/{bidder_id}` | 204; **409** when referenced, reason names the RFQs |
| GET | `/api/workflow/rfqs/{rfq_id}/candidates` | every bidder evaluated against this RFQ: `suitability`, and `shortlisted` |
| POST | `/api/workflow/rfqs/{rfq_id}/shortlist` | gains optional `vendor_id`; **409** when an ineligible bidder is invited with no override reason |

Consistent with the module's existing mapping: 404 for an unknown id, 409 for
"it exists but the workflow says no", 422 for a self-inconsistent request.

Every mutating route runs inside `persistence.locked_update`, **and so does
every read that gates one** — the eligibility check that authorises a shortlist
write happens inside the block, in the store, not in the route. This is the
rule the auth store and the workflow store have each already paid for twice.

`GET /candidates` computes `as_of` as today's date at the route boundary and
passes it down, so the pure function stays pure.

`{bidder_id}` is added to `test_auth_middleware.py`'s probe substitution list.
That test is designed to fail when a route adds a path parameter; the addition
is its documented extension point. No new path joins `PUBLIC_PATHS`, and no
route reads or writes a role.

---

## 6. Web

### `web/src/pages/Bidders.tsx` — the registry

A new nav entry in the **RFQ process** group, placed between `Projects & items`
and `RFQ workflow`, because that is the order the work happens in: you have
projects and items, you have a bidder list, and only then do you raise an RFQ
against both. The indices below it shift by one.

The screen lists bidders with a prequal chip that renders the **derived**
status, so an expired approval reads as `Expired` rather than `Approved`; plus
trade categories, country, rating, and how many RFQs have invited them. Add and
edit are a form; delete surfaces the server's refusal sentence verbatim rather
than pre-disabling the control, the same choice `RfqWizard` makes for the gate.

### `RfqWizard.tsx` — Shortlisting becomes a picker

The free-text row is replaced by the candidate list for this RFQ: each bidder
with their blockers and cautions inline, and an Invite control. Inviting a
bidder with blockers requires an override reason to be typed first — the
control is enabled and the server's refusal is what teaches the rule, matching
how the step already treats the frozen package.

"Add an unregistered vendor" remains, behind a disclosure, for the genuine
one-off. It posts exactly what it posts today.

---

## 7. Mock data — `python -m workflow.seed_demo`

A module with a `main(argv)` so it is callable from a test without a subprocess.

```
python -m workflow.seed_demo [--root .] [--as-of YYYY-MM-DD] [--force]
```

Writes a complete demo `workflow.json` through `persistence.save`, so the seed
cannot produce a document the loader would reject. **It refuses to overwrite a
store that holds any entity unless `--force`** — a demo loader that silently
replaces real work is a data-loss bug wearing a friendly name.

Contents: about twelve invented bidders with Gulf-region flavour (Al Munara
Switchgear LLC, Northwind Valve Works, Meridian Instrument Co., …), spanning
every prequal state, including one already expired and one expiring inside
thirty days; three projects; about a dozen items; five RFQs spread from Scoping
to Evaluation, with frozen technical packages, approved shortlists linked to
registry bidders, TBE templates, VDRL lines, and a few bids with receipts.

No real company name appears. The names are invented for demonstration.

**Ids are fixed; dates are relative to `--as-of`.** Fixed ids make a demo
stable — a link into an RFQ keeps working across reseeds, and a screenshot
keeps matching. Dates have to move, or the "expiring in three weeks" bidder is
expiring in the past by next month and the screen demonstrates nothing.

### The seed may not fabricate a state the gates would refuse

A test walks every seeded RFQ, replays the forward transitions its history
records, and asserts each gate would have passed. An `Issued` RFQ whose
shortlist was never approved is the product lying to the client in the demo,
which is worse than the demo being smaller.

---

## 8. Testing

New files:

| file | covers |
|---|---|
| `tests/test_bidder_registry.py` | store CRUD, partial update, `id` immutability, the delete guard and its reason |
| `tests/test_bidder_suitability.py` | every row of the table in §2, both sides of the expiry and 30-day boundaries |
| `tests/test_bidder_endpoints.py` | the seven routes, their status codes, the candidates payload, the override refusal |
| `tests/test_seed_demo.py` | gate consistency, the non-empty refusal, id stability, `as_of`-relative dates |
| `web/src/pages/Bidders.test.tsx` | roster render, derived Expired chip, delete refusal surfaced |

Extended: `tests/test_workflow_persistence.py` (bidder round-trip plus new
mutation-matrix rows), `tests/test_auth_middleware.py` (probe substitution),
`web/src/pages/RfqWizard.test.tsx` (the picker, the override).

Both CLAUDE.md baseline rows are re-measured afterwards: the workstation row by
running the suite, the CI row derived from it by the documented subtraction.
The web suite count moves too.

---

## Out of scope

Bid-window timers, a returnables checklist, per-project bidder approval, bidder
self-service or a supplier portal, document attachments on a bidder record, and
any change to the stage set or `TRANSITIONS`. Unifying the workflow store's
`Project` with the ingestion store's project `slug` remains phase 2's problem.

---

## Addendum, same day — the registry is a real ADNOC AVL

The design above assumed a registry typed in by hand. It is now also
importable from a real client export, which changes three things in the model
and one thing in what the demo may claim.

### `workflow/avl_import.py`

`parse_avl(path, astra_subset=False) -> list[Bidder]` folds an ADNOC Approved
Vendor List export — one row per (product group, vendor, manufacturer), about
18 000 of them — into about 1 300 bidders. `bdr_<vendor number>` is the id, so
re-importing a later export updates the same bidder rather than creating a
near-duplicate under a slightly different spelling of the name.

**Columns are located by header, not by position.** A re-export with a column
inserted would otherwise load manufacturer names into `vendor_name`, and the
registry would look entirely plausible while being wrong about every company
in it. A missing required header raises, naming itself.

### Three fields on `Bidder`

| field | why |
|---|---|
| `country: str \| None` | widened. The export's only country is the *manufacturer's*; copying it across would record a UAE supplier as Indian because their principal is. |
| `approved_by: list[str]` | which organisations have approved this bidder — `["ADNOC"]`, or `["ADNOC", "Astra"]`. A list, because the same company is commonly on several lists and which one matters depends on whose project the RFQ is for. |
| `represented_manufacturers: list[str]` | the OEMs a vendor is listed against. Often the real difference between two suppliers of one product group, and the thing a buyer actually searches by. |

### What the import must not do

**Nothing the export does not say is invented.** There is no prequalification
expiry in it, no hold, no turnover band and no performance rating, so those
stay empty on every imported bidder. These are real, named companies:
synthesising a suspension, a lapse or a 3.2-out-of-5 against one of them
manufactures a record about a real business, and it would be indistinguishable
from a real one on the screen. A demo with fewer columns filled in is by a wide
margin the cheaper problem.

The blocker mechanism still demonstrates honestly on imported data, because
scope fit is computed from the vendor's *real* product groups against the
RFQ's, and any bidder can be suspended live in the UI during a demo.

**The Astra subset is invented, and says so** — in `astra_approves`'s
docstring, in the module header, and in the seed's output line. There is no
Astra approval in the ADNOC export and no real Astra list has been supplied.
It exists so a demo can show a client's AVL and an internal subset of it side
by side. It is stable (derived from the vendor number, so a reseed does not
reshuffle it) and explicable (weighted towards vendors listed against more
product groups). On the real export it selects 297 of 1 346.

### Consequences for the two screens

A registry of 1 346 cannot be rendered as 1 346 cards, and a common product
group has over a hundred approved vendors. Both screens therefore filter
first and cap second, **and say that they have capped** — a screen that
silently shows the first sixty of thirteen hundred misrepresents the registry.

- **Bidders**: a search across name, trade category and represented
  manufacturer; an approved-by filter built from the data rather than
  hardcoded; a count, and a cap of 60.
- **Shortlisting candidates**: the same search, plus an "only those registered
  for this discipline" filter that is **on by default** — the registry is the
  client's whole approved list, and the question in front of the reader is
  almost always "who of ours can do this". Cap of 25.

### The demo RFQs use real product group descriptions

`SWITCHGEARS - LV -415V`, `VALVES - BALL - API 6D - UP TO 12"` and six others,
quoted exactly. A made-up discipline like "Electrical" matches nothing in the
export, and every candidate would read as a scope mismatch — the feature would
appear broken rather than strict. The seed's shortlists are then drawn
dynamically: Astra-approved first, then alphabetically, which is both a
defensible procurement habit and the only way a rehearsed demo picks the same
companies twice out of a hundred.

`_invite` skips a blocked bidder that has no curated override text rather than
generating one. A generated justification for an exception is exactly the
record this whole feature exists to make deliberate.
