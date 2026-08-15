# CLAUDE.md

Two Python packages over one shared LLM layer:

- **`procurement/`** — the tender comparison pipeline. Ingests a ZIP of vendor
  folders, classifies each document, resolves revision lineage, routes each
  document to a per-class extractor, and stores the result per vendor.
- **`cost_estimation/`** — the older costing-sheet ingestion CLI (`cost-est`).
- **`shared/llm/`** — provider clients behind one `classify_structure` interface,
  plus the versioned prompt files in `shared/llm/prompts/`.
- **`api/main.py` + `web/`** — FastAPI and the React SPA it serves: the single
  front end, covering setup, ingestion and review.

## Running things

```bash
python -m pytest
```

Run from the repo root. Tests are key-free — they use `shared/llm/mock_client.py`,
and no test may require `ANTHROPIC_API_KEY`.

There are **two** baselines, and both are correct — they differ only in which
untracked fixture directories are present, never in pass/fail:

| where | baseline |
|---|---|
| a developer workstation, `data/` and an ingested multi-vendor `projects/` present, `pdftotext` on PATH | **1735 passed, 3 skipped, 0 failed** |
| CI, and any clean checkout | **1715 passed, 23 skipped, 0 failed** |

Anything else is a real regression.

CI being green is not luck. With no `projects/` all four
`test_real_corpus_coverage.py` tests skip on their module-level guard, and the
three tests guarded on the untracked `data/` sample directory skip too. The
three skips a workstation already shows are credential guards
(`test_anthropic_client.py`, `test_bedrock_client.py`,
`test_procurement_real_data.py`) and skip in both places.

There is a **third** environmental gate, and it splits the two rows further
apart than the sentence above describes. Two tests in
`test_datasheet_row_recall.py` are guarded on the `pdftotext` CLI, which comes
from poppler-utils; `.github/workflows/tests.yml` does not install it, so they
pass on a workstation that has it and skip in CI.

And now a **fourth**, the largest of them: **eleven** tests guarded on
`data/bidders_details/ADNOC Approved Vendor List as of 10.12.2025(client).xlsx`
— two in `test_avl_import.py`, seven in `test_seed_demo.py` and two in
`test_disciplines.py`, all carrying the `needs_real_avl` marker.

**The `(client)` in that filename is load-bearing, and all three files once
named it without.** When the Astra subset arrived beside it the export was
renamed to say which of the two it is, and the three `os.path.exists` guards
were not. The failure is silent and it is the worst kind this table has: eleven
tests reported as *skipped for the documented reason* on a workstation that
holds the file, so the baseline stayed green while the gate measured nothing.
A skip guard whose path no longer resolves is indistinguishable from CI. If a
workstation run ever shows 14 skips rather than 3, check these three paths
before looking anywhere else. That export is untracked and will not be
committed;
it is a real client document, and the point of the import is that it is not
reproducible from anything in this repository. The other twenty-one tests in
`test_avl_import.py` build a small workbook in memory with `openpyxl`, so the
parser itself is covered in CI — only the tests that assert against the *real*
1 346-vendor export skip.

So the CI row is the workstation row with the four corpus-coverage passes, the
three `data/` passes, the two `pdftotext` passes and the eleven AVL passes
turned into skips — `1715 = 1735 - 4 - 3 - 2 - 11`, `23 = 3 + 4 + 3 + 2 + 11`;
1738 tests either way. When the counts move, measure the workstation row and derive
the CI row from it; editing the two rows independently is how they drift
apart.

**The workstation row is measured, not derived**: **1735 passed, 3 skipped**,
taken on 2026-08-15 on the `rfq-platform-phase-1` branch, in an environment
with `pdftotext`, `data/` (including the ADNOC export) and an ingested
multi-vendor `projects/` all present. The **eleven**-skip figure that the
fourth gate contributes is measured too — by moving `data/bidders_details/`
aside and re-running the affected files, not by counting decorators. It has
been measured that way every time a change touched those files: nine when the
client-approval gap added tests to two of them, eleven when
`test_disciplines.py` became a third, and eleven again when the filename above
was corrected in all three — which is the one case where the number came back
unchanged and the measurement was still the whole point, because before it the
gate was open and nobody could tell. Measure it again rather than adjusting it
arithmetically — the paragraphs below record which phase contributed what, and
they are a history, not a running total.
That matters, because a row this file once carried was not. While the auth
branch was in flight the workstation figure was *derived backwards* — measured
on a checkout that had neither fixture directory, then extrapolated upward —
and was flagged here as an assumption rather than a result. Both rows since
have been real measurements, and the caveat that used to sit here is deleted
rather than reworded. The CI row is still derived from the workstation row by
the subtraction above; derive it that way again when the counts move.

The jump from 1097 is the RFQ workflow: 83 tests across
`test_workflow_stages.py`, `test_workflow_store.py`,
`test_workflow_endpoints.py` and `test_workflow_persistence.py`. None of them
touches a fixture directory or a provider key, so every one of them lands in
both rows.

The further 40 on top of that are the project → item hierarchy, in the same
four files: the store's updates and delete guards, the five project/item
routes, and the eight-row mutation matrix in `test_workflow_persistence.py`.
Same story — no fixture directory, no provider key, both rows.

The 128 after *that* are the bidder registry: `test_bidder_suitability.py`,
`test_bidder_registry.py`, `test_shortlist_linking.py`,
`test_bidder_endpoints.py`, `test_avl_import.py`, `test_seed_demo.py`, and nine
more rows on the mutation matrix. All but nine of them land in both rows; the
nine are the AVL gate above.

The **79** after that are the clarification round: 42 in
`test_clarifications.py`, 12 in `test_clarification_endpoints.py`, 7 gate cases
in `test_workflow_stages.py`, 13 in `test_workflow_persistence.py` (five
round-trip, eight new mutation-matrix rows) and 5 in `test_seed_demo.py`. The
five seeded ones are deliberately **not** behind `needs_real_avl` — they assert
against whichever registry the seed built, so the AVL gate stays at nine, which
was re-measured by moving `data/bidders_details/` aside rather than assumed.

The **24** after that are the client-approval gap: 10 in
`test_bidder_suitability.py`, 5 in `test_bidder_endpoints.py`, 6 mutation-matrix
rows in `test_workflow_persistence.py`, 1 in `test_avl_import.py` and 2 in
`test_seed_demo.py`. The last three are the reason the AVL gate was measured
again rather than carried forward: they sit in the two gated files but are not
gated themselves — the importer one builds its workbook in memory, and both
seeded ones read the invented cast — so all 24 land in both rows and the gate
is still nine. Only four of `PLAN-TEMPLATE.md`'s nine matrix rows have an
analogue in a subsystem that stores nothing and calls no model; the other five
are deliberately absent rather than fabricated, and `test_workflow_persistence.py`
says so above the rows.

The **13** after that are the approved vendor list: 7 in
`test_bidder_suitability.py` for `client_approved` and 6 in
`test_bidder_endpoints.py` for `/bidders/approved`. Neither file's new tests
touch a fixture directory, so all 13 land in both rows.

The **14** after *that* are the item-discipline vocabulary: 11 in
`test_disciplines.py` and 3 more in `test_bidder_endpoints.py`. **This is the
change that moved the AVL gate off nine**, where it had sat since the registry
shipped: two of the eleven read the real export, because the failure worth
catching is a product group name in `workflow/disciplines.py` that the sheet
does not actually contain — a typo there silently shrinks a discipline and
nothing else notices, since the family still expands, just to a label no vendor
carries. The gate was re-measured at **eleven** by moving
`data/bidders_details/` aside and re-running the three files, not by counting
decorators.

The **5** after that are client approval on the shortlist: all in
`test_bidder_endpoints.py`, none touching a fixture directory, so the gate is
still eleven and all 5 land in both rows. Four of them are the three states the
column can show — approved, off the list, and *not checked* for a vendor typed
in by hand — plus the one that matters most: patch a shortlisted bidder's
`approved_by` and re-read the RFQ, and the answer flips with nothing having
rewritten the shortlist entry. That test is the invariant below stated as an
assertion, and it is the one that fails if anyone ever adds the field to
`ShortlistEntry`.

The **29** after that are the registry's move into SQLite: 16 in
`test_bidder_db.py`, 4 in `test_workflow_persistence.py` (the document no
longer carries a `bidders` key, no derived value reaches a column, an empty
database reads as an empty registry, and a pre-move document still migrates),
2 in `test_disciplines.py` for the shared label folding, and the rest spread
across the suites that already covered the store. The gate was re-measured at
**eleven** again: nothing new reads the export.

The **7** after that are the available list — both approvals at once: 6 in
`test_bidder_db.py` for `approved_by_all` and `add_approval`, and the rest
folded into `test_bidder_endpoints.py`'s rewritten `/bidders/available` cases.
None reads the export; the gate is still eleven.

The **7** after *that* are reading an enquiry document into the raise-an-RFQ
form, all in `test_rfq_extractor.py`. The gate is **still eleven and was not
re-measured**, because none of this change touched the three files that carry
the marker — which is the condition the paragraph above sets for measuring it
again, not a general licence to derive it.

The **29** after that are the mock clarification-round fixtures and the one
decision behind `--resume`, all in `test_mock_round_fixtures.py`. `tools/mock_clarification_round.py` itself is an
infrastructure check played over HTTP against a running app and is deliberately
not unit tested; its fixtures are JSON on disk and answerable for free. The
load-bearing ones are two. One asserts that a fixture vendor's
`trade_categories` cover the RFQ's own discipline through
`disciplines.covering` — whole-string, so a plausible near-miss shortlists
vendors who do not do the work and looks identical on screen to a list that
matches. The other refuses `CLIENT_APPROVER` in an invented vendor's
`approved_by`, and was written after that defect shipped; it was verified by
reinstating it, not by reading it. Same gate, same reason: none of these files
reads the export. The last ten cover `_already_done`, the one judgement in
`--resume`, imported rather than reimplemented because a copy in the test would
agree with a wrong original. Its defect is the third verified by reinstatement:
the check read state `"Answered"`, but the query this fixture answers *and then
withdraws* reads **Withdrawn**, so a resume re-sent an answer the server
refuses. That is the withdrawal-beats-an-answer invariant surfacing in a
caller — the kind of thing only a second run finds.

The **4** after that are the approver filter on `/bidders/available`, all in
`test_bidder_endpoints.py`: the default, the narrowing, and the two refusals.
None reads the export, so the gate is still eleven and was not re-measured.

The **42** after that are vendors added by hand and suggested by a model: 6 in
`test_item_vendor_lists.py` for the two curated sources and their mirrored
refusals, 2 in `test_workflow_persistence.py`, 13 in the new
`test_vendor_suggestions.py`, and 21 more in `test_item_vendor_endpoints.py`.
The gate **was** re-measured this time, because the filename correction above
touched all three marked files — it came back eleven.

Three of those 42 are the ones to keep. `test_the_model_is_never_asked_for_a_verdict`
reads the prompt file for the words that would make it ask for one; the item's
own discipline and description therefore ride in `context_text` rather than
being appended to the prompt, so a company legitimately trading as *Best
Cables* arriving in `exclude` cannot fail it. `test_the_suggestion_route_stores_nothing`
compares `workflow.json` byte for byte across the call, which is the only
assertion that catches a convenience write nobody meant to add.
`test_a_hand_added_vendor_never_links_to_the_registry` seeds a registry row
with the *exact* name being typed in and asserts `vendor_id` stays `None` — a
lookup added later would pass every other test in the file.

The **5** before that are both approvals on a shortlist row: four in
`test_bidder_endpoints.py` for `approved_by` and one in
`test_workflow_persistence.py`. The gate is **still eleven and was not
re-measured** — none of this change touched the three files carrying the
marker, which is the condition set above.

That single persistence test is the only one in this file that asserts an
**absence**, so it passed the moment it was written. It was verified the way
the reinstatement cases above were: by adding `approved_by` to `ShortlistEntry`
and watching it go red. An absence-assertion nobody has watched fail is not
known to be wired to anything, and this one guards the rule that
`client_approved` and `approved_by` are derived on read and stored nowhere —
the assertion that fails if either is ever "optimised" into the document.

The last **41** are Bid Desk's BD-2, the draft shortlist an **item** owns: 24 in
`test_draft_shortlist.py`, 12 in `test_draft_shortlist_endpoints.py` and 5 in
`test_workflow_persistence.py`. None reads a fixture directory or calls a
provider, so all 41 land in both rows and the AVL gate is still eleven — none of
this change touched the three files carrying the marker, which is the condition
set above for re-measuring it.

Three of those are the ones to keep.

`test_the_duplicate_guard_still_holds_after_a_reload` is the only two-run one.
The guard reads the list it is appending to, and on run 2 that list came off
disk rather than out of the call that built it — a regrouping that lost
`item_id` leaves the draft empty and the duplicate lands as a second row, which
no single-run test sees.

`test_a_registry_row_and_a_hand_typed_one_of_the_same_name_are_two_rows` is the
no-dedupe rule stated inside the store rather than in the view. The key is the
registry id where there is one and the name where there is not, and the two
spaces are tagged apart rather than merged — collapsing them attaches a real
company's approvals to a string somebody typed, which is the defect this file
records twice. Matching is exact and never folded: case-insensitive company-name
matching does not become safe because the scope narrowed to one item.

`test_no_draft_entry_stores_the_approvals_it_reports` asserts an **absence**, so
it passed the moment it was written. It was verified by adding `approved_by` to
`DraftShortlistEntry` and watching it — and its sibling in
`test_draft_shortlist.py` — go red. Second instance of that discipline after
`test_no_shortlist_entry_stores_the_approvals_it_reports`, and for the same
reason: an absence-assertion nobody has watched fail is not known to be wired to
anything.

`test_there_is_no_adoption_yet` asserts `WorkflowStore` has no
`adopt_draft_shortlist`. That is deliberate scope, not an oversight — turning a
draft into invitations is an attributed act with per-vendor guards, and it
belongs to the task where the RFQ-raising screen needs it.

The web suite is separate and not part of either row above — both rows are
`python -m pytest` counts. Run it with `npm test` under `web/` (vitest,
non-watching, exits non-zero on failure); `npm run build` also type-checks the
test files, since `web/tsconfig.app.json` includes `src`. CI runs both, in the
`web` job of the same workflow. It stands at **313 passed** across 22 files.

The last **3** are `table-scroll.test.ts`, and it is the odd one in this suite:
it reads the **source tree off disk** rather than rendering anything. That is
deliberate. jsdom applies no stylesheet and does no layout, so a table
overrunning its card measures identically to one that fits — the seven-column
vendor list shipped 787px wide inside a 614px card, scrolling the whole document
sideways, with every render test green. What a file *can* see is the missing
wrapper that causes it, so the assertion is structural: every
`<table className="table">` is preceded by `<div className="table-scroll">`, and
`.table-scroll` still carries `overflow-x: auto`. A third test asserts the
scanner finds tables at all — without it, renaming the class would leave the
file asserting nothing and still passing.

Because `web/tsconfig.app.json` has `include: ["src"]`, that file is
type-checked by `npm run build`, and naming `types` at all disables automatic
`@types` inclusion — so `@types/node` being in `devDependencies` was not enough
and `"node"` had to be listed there explicitly. The cost is that node globals
are now visible to app source too; `process.env` in a browser module is a
mistake this no longer catches.

The **6** before those are Bid Desk's BD-2 on the item screen: the batch control
now fills the **item's** shortlist draft rather than inviting into a covering
RFQ, and a `Shortlist draft` card renders what it filled. Net of two tests
deleted rather than skipped — `has no bulk control when no RFQ covers the item`
asserted a rule BD-2 inverts, and `keeps a refused vendor ticked and invites the
rest` was superseded exactly by its draft-path twin.

Two of the new ones are worth keeping. `shortlists the selected vendors with no
RFQ covering the item` is the requirement in one assertion — the old card
withheld the control entirely until an RFQ existed. `keeps the draft on screen
after a shortlisting reloads it` stubs the second project read on a
**macrotask**; resolved immediately, the loading state and the resolution batch
into one commit, the card never unmounts, and it would pass against the defect.
Third instance of that trap on this screen.

The last **14** are Bid Desk's BD-1, the four-source vendor pool on the item
screen — see [`feature-request.md`](feature-request.md). The card was registry
rows only, filtered by the two approval chips; it is now a union over four
sources with four chips, and **the two halves take different operators on
purpose**. `ADNOC` and `Astra` stay ANDed, because one approval is the common
case and precisely what is being excluded. `Added by hand` and `Suggested` are
*additive* — a curated row has no registry row to approve it, and folding them
into `GET /bidders/available` as a fifth approver would be a lie.

Three of those fourteen are the ones to keep.

`asks the server for nothing when both registry chips are unticked` asserts the
fetcher is **not called**. The endpoint 422s on an empty approver list and that
refusal is right, so the browser has to contribute no registry rows rather than
ask for none.

`shows a company held by the registry and by hand twice, once per source`
asserts **two** rows for one name. No deduplication, ever — name matching is a
shipped defect this file records twice, and merging the rows in the view is that
defect wearing a different hat. It reads the two source badges as a *set*, not
positionally, because the ordering is a separate rule with its own test.

`draws a curated row even when the registry fills the cap` is that separate
rule, and it exists because **the defect shipped and a browser found it**. The
table draws 25 rows of a registry list that runs to 79 for one discipline;
appended after that, an item's one or two hand-added companies fall past the cap
and are never drawn, so ticking the chip reads as doing nothing at all. Hence
`pool = [...curatedRows, ...registryRows]`. With the default single-bidder
fixture, curated-last passes — so the test hands jsdom a 40-bidder list, and it
was verified by flipping the order back and watching exactly that one test go
red.

**A fourth rendering defect joins the three below, and it is the same story
again.** `.chip` had no `white-space` rule and sits in `.fxrow`, which is
`display: flex` with no `flex-wrap`, so a multi-word chip beside a text input
was shrunk under its label and wrapped *inside* the pill: "Added by hand" and
"Suggested vendors" measured 47px tall against 29px for "ADNOC" and "Astra", 9px
out of line in the same row. The fix is `white-space: nowrap` **and**
`flex-shrink: 0` on `.chip` — `nowrap` alone still lets flex squeeze the pill
and overflow its own text. jsdom applies no stylesheet and does no layout, so
there is no structural stand-in to assert here at all: this one is caught only by
measuring `getBoundingClientRect`, which is the whole argument for launching the
app.

Known and not fixed by that phase, **since fixed separately**: the
available-vendor table was 787px inside a 614px card at a 940px viewport, so the
document scrolled sideways rather than the table scrolling in its own container.
Its seven columns were byte-identical before and after BD-1, so it predated that
change and was tracked as its own piece of work. Seventeen tables across six
screens now sit in a `.table-scroll` wrapper, guarded by the source-scanning
test described above.

The **5** before those are the sign-in screen's admin-account chip, in the new
`Auth.test.tsx` — that screen had no test file at all.

**That chip fills both credentials, and `ADMIN_PASSWORD` is therefore a
published password.** It is a string literal in `web/src/pages/Auth.tsx`, so it
ships in the bundle and is readable by anyone who loads the page; the demo
administrator is a public account and neither that address nor that password
may be reused anywhere it would matter. This was asked for deliberately, after
the email-only version had shipped and with the exposure stated — it is not an
oversight to be "fixed" by someone reading this later, and it is not a pattern
to copy for a second account.

The load-bearing test is `fills the form but does not submit it`. Filling is as
far as the chip goes: signing in stays a second, deliberate press. A `<button>`
inside a `<form>` submits by default, so dropping the explicit `type="button"`
turns the chip into a one-click admin login — and it would fail **silently**,
because the form is filled and the sign-in would simply succeed. That is the
whole distance between what shipped and the unconditional bypass that was
declined, and it is one attribute wide.

One more asserts the chip carries `chip--asis`, because `.chip`'s
`text-transform: capitalize` renders the address as *Admin@Gmail.Com*. Fourth
instance of that family, and like the other three **jsdom cannot see it** — the
class is the structural stand-in, and the render was checked by measuring
`getComputedStyle` in a real browser.

`ADMIN_ACCOUNT` is hardcoded and nothing verifies either credential still
matches `auth.json`: a rotated password or a store seeded without that address
shows a chip whose sign-in then fails with the server's ordinary "Invalid email
or password", the same answer any wrong credential gets. The chip is offered in
login mode only — that address is already registered, so signing up with it
would 409.

The last **14** are the item screen's four vendor cards, the suggestion card
and the way into a covering RFQ — all in `ItemDetail.test.tsx`, net of the one
that went with the disabled *From the internet* chip.

**Two of those fourteen were written after a browser found the defect, not
before**, and they are the reason to keep launching the app. Rendering a
curated row's `trade_categories` in full put all eleven of the Cables
expansion's product groups in one cell: 595px wide, the vendor name crushed to
67px, every row 170px tall. That is the third instance of this exact family
after the `.field`-on-a-label boxes and the 89px shortlist button, and like
both of them **jsdom cannot see it** — no stylesheet, no layout. What the tests
assert is the structure that caused it (capped at two plus a `+N more`, the
shape the available-vendor card beside it already used); the geometry itself is
only ever caught by running the app. The same pass caught the *Registry* column
reading "Not in the registry" on a hand-typed row, which reports a check nobody
ran — the server never looks a curated name up, so the honest rendering of that
`null` is no column at all, and no "not found" tally under it either.

The **16** before those are the vendor list's filtering and bulk shortlisting: fifteen
in `ItemDetail.test.tsx` and one in `ProjectDetail.test.tsx`. They cover the
search row, the approval chips, selection, the batch invite, and the rendering
fixes below.

**`.field` on a `<label>` is the recurring rendering defect on this screen, and
it has appeared twice.** `.field` is the *input* class — `forms.tsx` puts it on
`<input>` elements — so on a wrapping label it gives the label a border and
padding of its own while the control inside keeps its, and the pair renders as
nested boxes. The vendor search had it; the RFQ chooser beside it was then
written the same way *in the same phase that fixed the search*, and a note here
claimed the search was "the only place" while a second instance was being added
a few hundred lines away. Both are now a plain `<label htmlFor>` beside a
control carrying `.input`. `grep -rn 'label className="field"' web/src` is the
check, and it should only ever match comments.

**Two of the thirteen stub `fetchWorkflowProject` to resolve its second call on
a macrotask, and that is load-bearing.** This screen re-reads the project after
every write; resolved immediately, the loading state and the resolution batch
into a single commit, the vendor card never unmounts, and a test asserting that
state survives a reload passes whether or not the bug is present. Both were
written that way after watching them pass against the defect. They caught two
real ones: a chosen target RFQ resetting so the next vendor was invited
somewhere nobody picked, and a batch invite wiping its own refusals. The fix for
the second is `keepPreviousData` on that `useAsync` **plus** a `loading && !data`
guard — the option keeps `data` across a refresh but `loading` still goes true,
so guarding on it alone unmounts the subtree anyway and undoes the option.

**The search row was a box inside a box, and the measurement is why it was
found.** Its `<label>` carried `className="field"`, so the label took an input's
border and padding while the input inside it carried no class at all and fell
back to the user agent's `1.6px inset`. The test asserts the input carries
`.input` **and** has no wrapping label, because either alone would pass over the
defect. Two more of that family live beside it: `.chip`'s
`text-transform: capitalize` — right for the single-word verdict chips it was
written for — rendered "From the internet" as "From The Internet", hence
`.chip--asis`; and a per-row button whose *visible* label carried the vendor
name wrapped to three lines and made every row 89px tall, so the name moved to
`aria-label` where a screen reader still gets it. **None of the three is
visible to jsdom**, which applies no stylesheet and does no layout: they were
found by measuring `getBoundingClientRect` and `getComputedStyle` in a real
browser. The tests that now guard them assert structure and accessible names,
which is the most jsdom can see — the geometry itself is only ever caught by
running the app.

The last **24** before those, across two new files and three existing ones, are the RFQ
form fixes and the approval pills. Six in `ProjectDetail.test.tsx`: three that
both money fields are `type="text"` and that a budget of `1,800,000` is refused
rather than sent as `NaN`, and three for the enquiry document the form used to
deny holding. Three in the new `primitives.test.tsx` for `ApprovalPills`, four
in the new `ShortlistingStep.test.tsx` — that step had no test file at all —
one in `RfqWizard.test.tsx` for the disabled web-search button, and ten in
`ItemDetail.test.tsx` for the pills, the Shortlist control and the per-RFQ
approval summary.

**One of those ten is worth knowing about before you write another test in that
file.** `keeps the chosen RFQ after an invitation reloads the screen` stubs
`fetchWorkflowProject` to resolve its *second* call on a macrotask, on purpose.
Resolved immediately the loading state and the resolution batch into one
commit, `ItemDetail`'s `if (loading)` branch never renders, the vendor card
never unmounts, and the test passes with the target held in the card — which is
the defect it exists to catch. It was verified by reinstating that card-local
state and watching the second invitation go to the wrong RFQ. The same trap
applies to any test asserting that state survives a reload on this screen.

The 2 before those are the enquiry-document auto-fill, both in
`ProjectDetail.test.tsx` — that the four fields arrive filled in, and that a
refused read shows the server's sentence without blanking what the reader had
already typed.
The 4 before those are the item screen's own door into the RFQ process — 2 in
`ItemDetail.test.tsx` for raising an RFQ that covers this item alone, and 2 in
`RfqWizard.test.tsx` for the shortlist's client-approval column.
It was 224 across 19 until the standalone Bidders screen was removed, taking
`Bidders.test.tsx`'s 16 tests with it. The registry itself is untouched — it is
read by the wizard's Shortlisting step and by nothing else in the browser, so
`api.ts` keeps `fetchBidders` and no longer carries the single-bidder read or
the create/update/delete calls. Their routes in `api/workflow_routes.py` are
still served and still tested by `test_bidder_endpoints.py`; nothing in the
front end calls them. The 5 that came back are `ItemDetail.test.tsx`'s, for the
approved-bidder card on the item screen — which is where the registry is now
read from outside an RFQ.

The RFQ wizard's steps live one-per-file under `web/src/pages/wizard/`;
`RfqWizard.tsx` is only the shell — stepper, banners, stage history, gate card.
They were extracted when the Clarifications step acquired a real editor and the
single file would have passed 1 100 lines. `AttachmentTable` is shared, because
the addendum draft form replaces the package's attachment list wholesale.

`web/src/pages/workflow-fixtures.ts` is test data in a non-test module on
purpose. Importing fixtures from a `.test.tsx` file re-runs that file's
`describe` blocks inside the importing suite, and its hoisted
`vi.mock('../api')` factory wins over the importer's — so a fetcher the first
file did not mock arrives unmocked, as `mockResolvedValue is not a function`.
Nothing in `src/` imports it at runtime, so it never reaches the bundle.

Component tests that render `App` or `Setup` must mock `auth/context`'s
`useAuth`, and must return a **stable** object from it — build the value once
outside the `vi.mock` factory (`vi.hoisted`), never a fresh literal per call.
`App` passes `user` in the dependency list of the `useAsync` that loads the
project roster, so a new identity per render refetches, re-renders and
refetches until the vitest worker dies of heap exhaustion. That failure
arrives as `Worker exited unexpectedly` with a V8 fatal-error stack, not as a
failed assertion, so it is worth recognising on sight.

**There is no longer a workstation-only failure.** Until the Streamlit portal
was removed, `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`
failed for anyone with a populated `.env`, because `portal/app.py` called
`load_dotenv()` and repopulated the key that the test had just deleted. That
test went with the portal, so a populated `.env` no longer changes the count. A
red workstation run is now always a real regression — do not go looking for the
old excuse.

The workstation row also depends on what your untracked `projects/` holds:
`test_real_corpus_coverage.py` asserts against the newest store there with more
than one vendor, so it is a coverage instrument for the live corpus, not a
fixture-backed unit test. **It holds at shipped defaults** — measured on
`projects/phase4c-shipped-defaults`, ingested with no `LLM_MAX_TOKENS` and no
chunk-budget override. Both extractors that read a whole document split their
input on line boundaries (`procurement/chunking.py`, budgeted by
`TECH_CHUNK_CHARS` and `REQUIREMENTS_CHUNK_CHARS`) and merge all-or-nothing, so
a quotation-only vendor's 220k-character proposal no longer overruns the 8192
output ceiling and lands its vendor back at zero facts. If a floor goes red,
chunk further or fix the routing — never lower the floor, and never green it
with an environment override, which makes the instrument assert something
weaker than the sentence it reads as.

CI runs that same command on every pull request into `main`, via
[`.github/workflows/tests.yml`](.github/workflows/tests.yml) — Ubuntu, Python
3.12, no provider secrets. Keep it that way: a test that needs a key belongs
behind a skip guard, not behind a repository secret.

The app runs via [`run.ps1`](run.ps1) at the repo root — one command for both
servers, with preflight checks and a clean Ctrl+C. Use it rather than bare
`uvicorn` / `npm run dev`.

**It makes its ports available rather than just checking them.** A port held by
this repo's own leftovers is reclaimed automatically; a port held by anything
else is reported with its command line and refused, and `-Force` is what takes
those. "Ours" is deliberately asymmetric: the web server has to prove it is this
checkout (`vite` plus the repo path on its command line), while the API is
matched on `api.main:app` alone — a venv's `python.exe` reports its *base*
interpreter, so the observed line is `…anaconda3\python.exe -m uvicorn
api.main:app …` with the repo path nowhere in it. Requiring the path there would
refuse to reclaim the script's own leftover API, which is the one case the
feature exists for. Reclaiming kills the outermost process of the holder's tree,
because `uvicorn --reload` and `npm run dev` are supervisors that respawn a
worker onto the same port otherwise, and it never crosses into this script's own
ancestry — the terminal running `run.ps1` also carries the repo path.

`.claude/launch.json` still defines `procurement-api` and `enterprise-web` for
the preview tooling, and both hardcode their ports. They can no longer both be
up *by accident*: a preview-started vite matches the "ours" test, so `run.ps1`
now reclaims its port instead of refusing to start. Stop the preview server
first if you wanted it.

## Store invariants — violating these corrupts award decisions

There are **two** authoritative stores, with separate rules. For project data:
the snapshots under `projects/<slug>/store/`; `index/store.db` is derived and
disposable. For accounts: `<ROOT>/auth.json` — see the section after this one.

- Every write goes through `procurement/store/snapshots.py`. Never hand-roll a
  snapshot write.
- `generation` bumps **once per write transaction**, never once per file.
- `field_path` addresses list members by **id**, never by index — list order is
  not stable across re-extractions, and `overrides.py` rejects index selectors
  outright.
- **A stored collection contains exactly the records of its currently-live
  sources — no more.** Facts of superseded, reclassified or deleted documents
  must be pruned, not merely skipped on re-extraction. This is the invariant
  phase 2 shipped broken; see `_prune_orphan_facts` in `procurement/pipeline.py`.
- Missing data is never coerced to a passing or zero value. An unfound parameter
  is omitted, not emitted as `0` or `""`.
- **Arithmetic stays in Python.** Extractors capture numbers and units verbatim;
  the model reads, code decides. Never ask an extractor whether a vendor complies.
- A failed extraction never blanks previously-good stored data, and always
  records why in `DocumentRecord.notes`.

## Auth invariants — `<ROOT>/auth.json`

Users, sessions and grants live in one document so a single lock and a single
atomic write keep all three consistent. Written **only** from
`api/auth/store.py`; routes never touch the file.

- Every write is a read-modify-write inside `locked_update`. **And so is every
  decision that gates one.** A check made in the caller and a write made in
  the store are two critical sections, not one: two admins deleting each other
  concurrently each read "two admins, fine" and both writes land on zero
  admins. Both guards this subsystem shipped wrong were *reads* outside the
  lock, not writes — `store.delete_user` and `store.grant` show the shape.
- `grants` holds exactly the grants whose user still exists. `delete_user`
  cascades to grants and sessions in the same write. (A grant whose *project*
  is gone is deliberately tolerated and inert — `list_projects` filters
  against disk.)
- Sessions persist `sha256(token)` only. `password_hash` is never a field on
  `User`, so it cannot reach a response body; `store.password_hash_for` is the
  one reader of the digest.
- **`AUTH_DISABLED=1` is a development bypass, off unless set.** It serves an
  unauthenticated caller as the first administrator in `auth.json` (or
  `DEV_USER_EMAIL`), so the front end opens without signing in. It is a
  separate switch, *not* an edit to `PUBLIC_PATHS` — that set stays pinned at
  three by `test_the_allowlist_is_exactly_these_three_paths`, and turning the
  bypass off restores fail-closed exactly. It never mints a user: with no
  administrator in the store there is nobody to act as and it stays closed. A
  real session still wins, so actions keep their real attribution. Reach it
  with `.
un.ps1 -NoAuth`; never run it anywhere another person can reach the
  port.
- **Startup signs everyone out.** `api.main`'s lifespan calls
  `store.clear_sessions`, so launching the platform always lands on the
  sign-in page rather than dropping whoever signed in last back inside as
  whatever account that was. The session cookie is a browser-session cookie
  (no `max_age`) for the same reason. Both halves matter: the server-side row
  is what `resolve_session` actually checks, so clearing it is what makes a
  still-held cookie inert. The deliberate cost is that restarting the API
  signs out every user, not just the person restarting it.
- Roles are `admin` and `reviewer`, and **no route changes a role** — it is
  set once at creation. Signup always creates a reviewer.
- A project slug is matched against `list_projects` (which reads `os.listdir`),
  never by asking the filesystem whether a path exists. Windows and macOS
  resolve paths case-insensitively; CI does not, so that class of bug cannot
  fail on CI.

## RFQ workflow invariants — `<ROOT>/workflow.json`

The eight-stage RFQ process lives in the top-level `workflow/` package, with
its routes in `api/workflow_routes.py`. It is deliberately **not** part of
`procurement/`: that package's store has the snapshot invariants above
(`generation`, `field_path` by id, orphan pruning) and this one does not share
them, so keeping them apart stops a reader assuming one set covers both.

- **Stage codes are fixed**, and `TRANSITIONS` is **deny-by-default** — an edge
  absent from that table is refused. Every stage needs an entry, including the
  terminal `PO_ISSUED`, so `is_allowed` answers "no" instead of raising.
- **Only forward transitions are gated.** The backward edges are the documented
  recoveries — retender and renegotiate — and a forward gate must never block
  one, or a stuck RFQ has no way out.
- **A gate never returns a bare `False`.** `GateResult` carries a reason, and a
  blocked transition raises with it; the route surfaces that sentence as a 409.
  A reason must name the whole exit criterion, not the nearer half of it.
- **History is append-only.** A backward transition appends; it never rewrites
  or removes an earlier entry. The second pass through a stage is a second
  entry, which is why `to_stage` alone is not a unique key within one history.
- **`workflow.json` holds exactly the entities the store holds.** `save`
  replaces the document wholesale, so an entity removed in memory cannot
  survive on disk. Any field added to `WorkflowStore.__init__` needs a matching
  line in **both** `to_document` and `from_document`, or it silently fails to
  survive a restart.
- **Every write, and every decision that gates one, happens inside
  `persistence.locked_update`.** This is the same rule as the auth store and
  for the same reason: a check in the route and a write in the store are two
  critical sections, so two concurrent transitions could each read "gate open".
  `WorkflowStore.transition` therefore runs inside the block, never against a
  store loaded before it. The write lands only on a clean exit, so a refused
  transition leaves the document untouched.
- **Workflow routes are not on `middleware.PUBLIC_PATHS`** and must not be.
  `test_auth_middleware.py`'s route sweep covers them; a new path parameter
  needs adding to that test's probe substitutions, which is what its assertion
  is there to force.
- **Deleting a project takes its items with it, in the same call.** `save`
  replaces the document wholesale, so an item pruned in memory but not in the
  cascade is an item pointing at a project that is gone — and it survives the
  restart. The cascade materialises its id list *before* the first deletion and
  filters on `project_id`; dropping either half is a shipped orphan.
- **Nothing is deleted out from under a live reference.** An item covered by any
  RFQ, and a project holding any RFQ, both refuse with a reason naming the RFQ.
  Both guards live in the store method — they are reads that gate a write, and
  the route runs the whole method inside `locked_update`. This is the same rule
  as the auth store, and the third place this repository has needed it.
- **An update is partial, and never touches identity or parentage.** `changes`
  carries only what the caller sent (`model_dump(exclude_unset=True)`), so an
  absent field is left alone rather than cleared. `id` and `project_id` are
  refused: moving an item between projects would change which project's RFQs
  may cover it without either RFQ record changing. Updates rebuild through the
  model rather than `model_copy(update=...)`, which skips validation outright.
- **The live-period check is advisory.** An item whose `required_on_site` falls
  outside the project window is stored, with the reason returned alongside it as
  `live_period_warning`. A hard refusal would make the field unusable in exactly
  the cases where a late delivery is the fact being recorded.
- **A frozen technical package is immutable, and shortlist approval does not
  outlive an edit.** Both are guards on going *back* a step: a frozen package
  refuses a later write (vendors bid against that revision), and adding or
  removing a vendor revokes approval, so an RFQ cannot issue with a vendor
  procurement never signed off. Removal addresses `ShortlistEntry` and
  `VdrlLine` by their `id`, never by position.
- **A clarification's state is computed, not stored, and circulation is the
  default.** There is no `status` field on `ClarificationQuery`: Open, Answered
  and Withdrawn are derived in `workflow/clarifications.py` from three
  timestamps, and **withdrawal is checked before an answer** — a query answered
  and then withdrawn is out of the round, and reading those fields the other
  way round would leave the gate counting a dead question as satisfied. A
  blank `restricted_reason` is refused, so withholding an answer from the rest
  of the shortlist is always a recorded, attributed act; there is deliberately
  no `circulate` boolean, which would make a restricted answer
  indistinguishable from an oversight. Numbers are `max + 1`, never `count + 1`
  — the two agree while the sequence is dense, so only a *gapped* register
  tells them apart, and that is the case the tests use.
- **An addendum is the one sanctioned door through the frozen-package rule.**
  `set_technical_package` still refuses a frozen package; `issue_addendum`
  supersedes it at a new revision after four reads that all sit inside the
  store method. The second — `supersedes_revision` must still equal the
  package's current revision — is the one that would actually be lost by moving
  any of them into the route: two drafts cut against Rev. A, each reading
  "current is Rev. A" outside the lock, would both write and the second would
  silently roll the package back. A draft is editable and deletable; an issued
  addendum is neither, because bidders hold it. The bid due date is derived
  from the latest issued addendum rather than stored on `RfqRecord` — the
  original due date belongs to Issued, which that phase did not touch, so a
  field here would be half-owned.
- **A bidder who raised any query cannot be removed from the shortlist**, and
  the refusal names the numbers. Fourth instance of "nothing is deleted out
  from under a live reference", after `delete_item`, `delete_project` and
  `delete_bidder`. Answered and withdrawn queries hold it too, not only open
  ones: a bidder who declines to bid stays on the shortlist as a non-bidder,
  which is the true fact.
- **The bidder registry is organisation-wide, and eligibility is computed, not
  stored.** There is no `Expired` member of `PrequalStatus`: expiry is derived
  from `prequal_expires_on` against an `as_of` the caller passes in, so no
  sweep job is needed to keep the store honest and both boundaries are
  testable without freezing the clock. `workflow/bidders.py` is pure — no
  store, no I/O, no clock — and the routes resolve `as_of` at the boundary.
  **The client-approval gap is derived the same way and for the same reason.**
  `missing_client_approval` builds its sentence from `approved_by` at read
  time; there is no field for it on `Bidder`, none on `ShortlistEntry`, and no
  key for it in `workflow.json`, because a stored copy is wrong the moment
  `approved_by` is edited. It is a **caution and never a blocker** —
  `Suitability.eligible` is unchanged by it and no path it opens demands an
  `override_reason` — and the sentence is built in exactly one function, so
  neither `_bidder_payload` nor the browser reconstructs it. That last part is
  why `ADNOC` / `ASTRA` live in `workflow/models/bidder.py`: naming the client
  approver from the pure module must not drag `openpyxl` in behind it.
  **"Available" is both approvals at once, and it is what the item screen
  shows.** `AVAILABLE_APPROVERS` is `(CLIENT_APPROVER, ASTRA)`: the client's
  list says a vendor may be used on their project, ours says procurement has
  qualified them, and neither alone is enough to put one in front of a buyer.
  The filter is AND, never OR — one approval is the common case and precisely
  what is being excluded, since the client's list runs to 1 346 and ours to
  111. In SQL that is a `GROUP BY … HAVING count(DISTINCT approver_key) = ?`,
  and the `DISTINCT` matters: the product-group join multiplies approval rows,
  so a plain count lets one approval satisfy a two-approval test.
  **`GET /bidders/available` takes a repeatable `approver`, and an unknown one
  is a 422.** Absent means `AVAILABLE_APPROVERS`, so the default stays the
  server's rather than something every caller has to know and send. Narrowing
  to one is how a screen asks for the client's whole register — measured on the
  real export, that is 1 346 against the intersection's 111, and there was no
  route to it before. A typo returns nobody from the `HAVING` query, and an
  empty table reads on screen as "no vendor qualifies", so it is refused with
  the bad name in the message; an empty list is refused too, because a count of
  zero matches nobody. `selectable_approvers` rides alongside so the browser
  builds its filter controls without spelling an approver's name, and
  `approvers` is the *applied* filter so a narrowed caption names the
  narrowing.
- **There is no bulk invitation endpoint, deliberately.** The item screen's
  "Shortlist selected" is N calls to `POST /rfqs/{id}/shortlist`, one per
  vendor and sequential. The per-vendor guards — the `override_reason` a
  blocked bidder demands, the refusal of a duplicate — are what make an
  invitation an attributed act, and a bulk route would have to reimplement or
  bypass them. It is **not** all-or-nothing: one refusal fails alone, renders
  beside its own row and stays selected for a retry, while the vendors that
  succeeded stay invited. There is nothing to roll them back with — those
  writes have already landed through `locked_update`.
  **Astra approval is now real, and `avl_import.astra_approves` is not how it
  gets set.** That function derived the flag from a hash so a demo had
  something to show; a fabricated approval and a recorded one are
  indistinguishable on screen, which is why `python -m workflow.avl_db --astra
  <xlsx>` reads a supplied vendor list instead. It records the approval against
  bidders the registry already holds and **creates nobody** — that file says
  who approved whom, not who exists.
  **There is one approved vendor list, and `client_approved` is it.** It
  filters on `CLIENT_APPROVER` — the same constant `missing_client_approval`
  reads — and returns registry rows, never a `Suitability`: its callers may
  have no RFQ, so nothing there is eligible or blocked. `GET
  /bidders/approved` serves it, and must stay declared **above**
  `/bidders/{bidder_id}` or FastAPI reads "approved" as an id and the route
  404s; `test_the_approved_path_is_not_read_as_a_bidder_id` is what holds that.
  It sends `approver` and `total` alongside, so the browser neither spells
  "ADNOC" itself nor infers the size of the list from a capped table.
  **Its `discipline` filter is optional, and absent means the whole list, never
  none of it.** Narrowing matches whole-string through `_registered_for`, the
  same rule the candidate list uses. That rule is why the filter was inert
  until the vocabulary existed: items carried free text (`Electrical`, `1`)
  while the export's product groups are a controlled vocabulary, so narrowing
  emptied the card for every real item. `workflow/disciplines.py` is the join
  that fixed it, and the item form now picks from it rather than accepting
  prose. **The item screen still falls back to the whole list**, and decides
  that on the narrowed answer being *empty* rather than on how the discipline
  is spelled — the browser does not hold the vocabulary, and testing it there
  would be a second definition of what a discipline is. Items predating the
  picker keep their stored value, so the fallback is a live path, not a
  transitional one; an empty table would report the item as uncoverable when
  the truth is that it is unscoped.
- **Client approval on a shortlist row is derived on read, and has three
  states.** `_shortlist_payload` re-reads the registry for every entry, so
  correcting a vendor's `approved_by` is the only edit needed and nothing
  rewrites the shortlist. There is no `approved_by` on `ShortlistEntry` and no
  key for it in `workflow.json` — the same rule as `approval_caution` on
  `Bidder`, for the same reason. The three snapshot fields on that row
  (`vendor_name`, `prequal_status`, `scope_code_fit`) are frozen at invitation
  on purpose, and living beside a derived one is not an inconsistency: they
  record what was known then, and this records who is approved now. `None` is
  the third state and must stay distinct from `False` — a vendor typed in by
  hand has no registry row, so there is no finding either way, and rendering
  them as "not on the list" would report a check nobody ran. A `vendor_id`
  pointing at a deleted bidder lands there too. `client_approver` rides on the
  RFQ payload so the table does not spell the client's name itself.
  **`approved_by` rides on that row too, and is sent as well as the boolean,
  never instead of it.** The boolean carries the *rule* —
  `missing_client_approval` is its one definition — and the list carries
  *identity*, which the boolean cannot yield: Astra approval is not a function
  of whether ADNOC approved somebody, so a screen wanting both has to be sent
  both. Rebuilding the client-approval predicate in the browser from the list
  would be the second definition this file forbids everywhere else. Both keys
  are derived from the same `bidder is None`, so their three states line up:
  `null`/`null` is no registry row, `[]` with `false` is a row nobody approved.
  Neither is stored, and `test_no_shortlist_entry_stores_the_approvals_it_reports`
  is the assertion that fails if either ever is.
- **A registry-linked shortlist entry's snapshot comes from the registry, not
  the request.** With a `vendor_id`, `add_shortlist_entry` derives
  `vendor_name`, `prequal_status` and `scope_code_fit` itself and ignores what
  the caller sent; without one, the free-text path is exactly what it always
  was. Inviting a blocked bidder requires an `override_reason`, which is the
  positive attributed act `ShortlistEntry`'s docstring always claimed. Both are
  reads that gate a write, so both live in the store method the route runs
  inside `locked_update` — the same rule, for the fourth time.
- **A bidder is never deleted out from under a shortlist**, and the refusal
  names the RFQs. Third instance of that rule after `delete_item` and
  `delete_project`.
- **Nothing imported from a real vendor list is embellished.**
  `workflow/avl_import.py` folds an ADNOC Approved Vendor List export into
  ~1 300 bidders and leaves every field the sheet does not carry empty — no
  expiry, no hold, no turnover, no rating, and no country (the export's only
  country is the *manufacturer's*). These are real, named companies, and a
  synthesised suspension is indistinguishable on screen from a recorded one.
  The Astra subset is the one invented thing and says so in three places. The
  demo seed's RFQ disciplines are real product group descriptions, quoted
  exactly, so scope matching resolves against an imported registry instead of
  never matching.
  **And the converse: an invented vendor never claims the client's approval.**
  A mock may invent a company, and may give it `Astra` — an internal
  qualification of a company that is invented in the same direction. It may not
  give it `ADNOC`, because that is a fact about a row in the client's export,
  and a shortlist reading *approved by ADNOC* for a company ADNOC has never
  heard of is indistinguishable on screen from one that is genuinely on the
  list. This is the same rule as the paragraph above, pointing the other way,
  and it shipped broken: two of `mock_clarification_round_genset.json`'s cast
  carried `["ADNOC", "Astra"]` so that the shortlist would show an approved row
  beside an unapproved one. The fix is `also_invited` — real vendors quoted
  from the export, invited and raising nothing, because a shortlist row records
  that somebody was *asked* to bid while a query records what somebody *said*,
  and only the first is a fact a mock may make up about a real company.
  `test_no_invented_vendor_claims_the_clients_approval` is what holds it now.
- **An item owns a draft shortlist, and it is a draft — nothing there has been
  invited.** A buyer assembles picks out of the four-source pool before any RFQ
  covers the item, so it is stored rather than carried in browser navigation
  state: this API restarts on every code change and clears sessions when it
  does, and a twenty-five vendor selection lost to that is the failure the
  collection exists to prevent. Two rules hold it. **`workflow.json` holds
  exactly the draft entries whose item still exists** — `delete_item` pops the
  draft and `delete_project` reaches through the item cascade, materialising its
  id list before the first deletion. And **an item's draft holds exactly one
  entry per vendor**, keyed on `vendor_id` where there is one and `vendor_name`
  where there is not, so a registry row and a hand-typed one sharing a trading
  name are deliberately **two** rows. That duplicate check lives in the store
  method, never in the route — a check in the caller and a write in the store
  are two critical sections, which is the fifth time this repository has needed
  that rule. `source` is stored because it records *where the buyer found them*,
  which is not derivable later; approvals are not, because they are read live
  through `vendor_id`. There is no `adopt_draft_shortlist` yet, on purpose.
- **`WorkflowStore` knows nothing about disk.** Serialization lives in
  `workflow/persistence.py`, the one module allowed to touch the store's dicts
  directly. That is what made moving one collection to a database a change to
  that module and a new one beside it, with `store.py` untouched.
- **The registry is in SQLite; everything else is in the JSON document.**
  `<ROOT>/bidders.db` holds the bidders, `<ROOT>/workflow.json` holds the rest,
  and the document has **no `bidders` key** — a key there would be a second
  copy for the first edit to disagree with. `persistence.load` hydrates the
  store from both and hands back the same plain-dict object it always did, so
  every guard on the store (`delete_bidder` refusing while a shortlist
  references a vendor; the snapshot read at invitation) is unchanged. The split
  is on a real difference: the registry is reference data, arrives whole from a
  client export, is ~1 300 rows against tens everywhere else, and is queried by
  attribute rather than read whole.
  A document that still carries a `bidders` key predates the move; its registry
  **wins on load**, and the next write drops the key. `python -m
  workflow.avl_db --from-document` does that migration explicitly, and also
  strips the key — leaving it would mean the command appeared to work and
  changed nothing.
  `locked_update` rewrites the registry only when it changed, comparing against
  a shallow copy taken at load. That works *because* `update_bidder` rebuilds
  through the model rather than mutating in place; an in-place edit to a stored
  `Bidder` would silently fail to persist.
  **Three list-valued fields are child tables, not JSON columns**, because they
  are filtered *on* — and a list in a text column can only be filtered with
  `LIKE '%…%'`, which is the substring matching this repository has twice
  recorded as a defect.
- **One function folds a product group label, and it lives in
  `workflow/disciplines.py`.** `bidder_db.fold` is that function re-exported,
  not a second implementation, and the key columns are written with it. It
  collapses *internal* whitespace as well as trimming, because the client's own
  exports disagree with themselves — the full list spells it `CABLES - LV POWER
  DISTRIBUTION` and a subset from the same system uses two spaces. When the SQL
  and Python rules drift, the symptom is silent: a vendor invisible to the very
  filter its own row satisfies.
- **Reading an enquiry document stores nothing, and there is no model behind it
  yet.** `POST /rfqs/extract` hands `workflow/rfq_extractor.py`'s answer back to
  the browser and returns; the RFQ is created when the reader submits the form,
  out of whatever they are looking at by then, so a bad extraction is corrected
  rather than undone and an abandoned upload leaves nothing. The extractor is a
  fixture — it ignores the filename and the bytes — and it says so rather than
  posing as an extractor that is bad at its job: the reference is prefixed
  `MOCK-` because a synthesised fact must not be indistinguishable on screen
  from a recorded one, the same rule the AVL import keeps. **Its discipline is
  quoted from `workflow/disciplines.py`**, because the form's field is a
  controlled vocabulary and a plausible `Mechanical` would arrive marked "not a
  listed discipline" — the auto-fill looking broken in the one demonstration it
  exists for. The route is declared **above** `/rfqs/{rfq_id}` for the reason
  `/bidders/approved` is, and `test_the_extract_path_is_not_read_as_an_rfq_id`
  holds it.
- **`VendorListSource` has four members in two sets, and each set refuses the
  other's operations.** `Client` and `Astra` are **uploaded**: they arrive as
  whole documents and `set_item_vendor_list` replaces one wholesale, so
  `add_item_vendor_entry` and `remove_item_vendor_entry` refuse them.
  `Manual` and `Suggested` are **curated**: built one vendor at a time, so
  `set_item_vendor_list` refuses *those*. Not defensive noise, and not one
  method with a flag — an upload that wiped a buyer's hand-added companies
  would destroy work with no undo, and a hand-add appended to an export would
  leave that list matching no document any re-upload reproduces. Both refusals
  name the source and say what would work instead. Removal is by **id**, never
  by name or position, because two suppliers can share a trading name; that is
  the same rule `remove_shortlist_entry` keeps. `ItemVendorEntry` gains nothing
  for either — a hand-added company is already exactly what that record
  describes, and a `note` rides on `source_document`, which already means *why
  this row is here*. The cascade needed no extending: `delete_item` pops the
  whole entry, per item rather than per source, and
  `test_deleting_an_item_takes_its_hand_added_vendors_too` is what says so.
- **A curated row never links to the registry, and the route never looks the
  name up.** `vendor_id` is always `None` there. A match would silently attach
  a real company's approvals to whatever somebody typed, and this repository
  has twice recorded name matching as a shipped defect; if the vendor really is
  in the registry, the available-vendor card is where to find them, and that
  card links by id. Nothing on these routes creates a bidder or grants an
  approval — the registry is organisation-wide and arrives whole from its own
  import, so a write attached to one line of equipment must not enrol a company
  across every project. On screen this means the curated cards carry **no
  Registry column and no "not found" tally**: rendering "Not in the registry"
  for a row nobody looked up would report a check that never ran, which is the
  `null` vs `false` distinction the shortlist's client-approval column keeps.
- **`POST /projects/{p}/items/{i}/vendor-suggestions` stores nothing**, the
  same shape as `/rfqs/extract` and for the same reason: a suggestion the
  reader rejects should leave nothing behind, and one they accept should be
  recorded as *their* act. Accepting is a second call to the hand-add route,
  one vendor at a time; there is deliberately **no bulk accept**, because
  taking twenty unverified companies in one click is precisely the act that
  needs friction. A suggestion carries no `approved_by`, no `vendor_id` and no
  prequalification — a model-named company claiming the client's approval is
  the failure `test_no_invented_vendor_claims_the_clients_approval` guards,
  arriving through a new door. A provider failure is a **502 and never an empty
  list**: an outage and "no such companies exist" must not look the same, the
  distinction the covering-RFQ summary keeps between `—` and `Nobody invited
  yet`.
- **The label matches the mechanism: "suggested by the model", never "found on
  the internet".** `shared/llm/` wraps Anthropic, OpenAI, Gemini and Bedrock
  through one `classify_structure` call — no crawler, no search index, no
  provider web-search tool — so what comes back is what the model recalls from
  training: undated, unsourced, and capable of being confidently wrong by
  inventing a plausible company name. The card says so before it says anything
  else, every stored row's `source_document` reads *suggested by the model*,
  and the disabled *From the internet* chip was **removed rather than enabled**
  because it promised a search no code performs. Fourth instance of the
  honest-labelling rule, beside the AVL import, the mock rounds and the RFQ
  extractor. Adopting a real server-side web-search tool is a phase of its own —
  a client method beyond `classify_structure`, a citation field, and somewhere
  to record which URL a vendor came from — and the label can change when it
  lands.
- **The model reads; code decides — and the prompt is where that is enforced.**
  `shared/llm/prompts/vendor_search_v1.txt` is versioned like every other, so
  what is asked is a reviewable diff rather than a string literal buried in a
  request. It asks only which companies supply this kind of equipment, and
  `test_the_model_is_never_asked_for_a_verdict` reads the file for the words
  that would make it ask for more. That test is why the item's discipline and
  description ride in `context_text` rather than being appended to the prompt:
  a company legitimately trading as *Best Cables* arriving in `exclude` must
  not be able to fail it. `exclude` is filtered again in Python, folded through
  `disciplines.fold`, because a prompt instruction is a request and only the
  filter is a guarantee. The `[]` default on `vendors` is load-bearing —
  `CLAUDE.md`'s `I5`, an omitted optional array read as a failure rather than
  as no results — and a model asked about an obscure discipline may
  legitimately name none.
- **A `Suggested` row stays labelled one after a person accepts it.** It is not
  promoted to `Manual` on the grounds that a human vouched for it: that the
  name originated with a model is the single thing a later reader would most
  want to know, and promoting it is the only way to lose it. Stated in the
  design as an open question and decided here rather than silently.

## Planning convention

Design specs live in `docs/superpowers/specs/`, implementation plans in
`docs/superpowers/plans/`, and the per-phase execution ledger in
`.superpowers/sdd/<phase>/progress.md`.

**Before writing a plan for phase 3 or 4, read
[`docs/superpowers/PLAN-TEMPLATE.md`](docs/superpowers/PLAN-TEMPLATE.md).** It is
not boilerplate: phase 2 shipped seven defects that survived per-task TDD and
seven honest per-task reviews, because every one of them needed two runs or two
modules to see. The template's three rules — a named store invariant per task, a
two-run mutation matrix on the integration task, and reference code treated as
intent rather than paste-able — are the structural fix. Phase 3 is more exposed
than phase 2, not less.
