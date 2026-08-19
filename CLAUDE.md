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
| a developer workstation, `data/` and an ingested multi-vendor `projects/` present, `pdftotext` on PATH | **1819 passed, 3 skipped, 0 failed** |
| CI, and any clean checkout | **1799 passed, 23 skipped, 0 failed** |

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
turned into skips — `1799 = 1819 - 4 - 3 - 2 - 11`, `23 = 3 + 4 + 3 + 2 + 11`;
1822 tests either way. When the counts move, measure the workstation row and derive
the CI row from it; editing the two rows independently is how they drift
apart.

**The workstation row is measured, not derived**: **1819 passed, 3 skipped**,
taken on 2026-08-16 on the `rfq-platform-phase-1` branch, after the RFQ
documents below, in an environment
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

The **13** after that are the approved vendor list, and **this count is
known wrong**: it credited 7 tests in `test_bidder_suitability.py` for
`client_approved` and 6 in `test_bidder_endpoints.py` for `/bidders/approved`.
The endpoint never existed (see the approved-vendor-list paragraph below), so
those 6 were never written, and the suitability file carries 5 rather than 7 by
`grep -c '^def test_client_approved\|^def test_a_client_approved'`. The
measured totals elsewhere in this file are unaffected — they were taken by
running the suite, not by adding these up — which is the whole argument for
measuring rather than deriving. Left as a corrected record rather than a
silently patched number, because a per-phase attribution that was wrong once is
worth being able to see.

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

`test_there_is_no_adoption_yet` asserted `WorkflowStore` had no
`adopt_draft_shortlist`. That was deliberate scope, not an oversight — turning
a draft into invitations is an attributed act with per-vendor guards, and it
belonged to the task where the RFQ-raising screen needed it. **BD-6 is that
task, and it replaced that test with the adoption cases below** rather than
leaving an absence-assertion nobody could satisfy.

The last **67** are Bid Desk's BD-6, real documents against an RFQ: 14 in
`test_safe_extract.py`, 43 in `test_rfq_documents.py`, 8 more in
`test_draft_shortlist.py` (adoption, net of the one deleted above) and 2 in
`test_draft_shortlist_endpoints.py`. None reads a fixture directory or calls a
provider, so all 67 land in both rows and the AVL gate is still eleven — none
of this change touched the three files carrying the marker, which is the
condition set above for re-measuring it.

Four of those are the ones to keep.

`test_deleting_one_of_two_records_sharing_a_blob_leaves_the_blob` is the whole
of I-D. Content addressing means two records legitimately point at one file, so
a delete that dropped the blob unconditionally would destroy a document another
record still names — and a test with **one** document cannot tell the two
behaviours apart. Its sibling deletes both and asserts the file goes.

`test_the_sharing_guard_still_holds_after_a_reload` is the two-run one, and the
same trap `test_the_duplicate_guard_still_holds_after_a_reload` records: the
guard reads the records beside the one being removed, and on run 2 that list
came off disk. An `rfq_documents` key lost in serialization reads as "nothing
else references this" and the delete takes a file another record names.

`test_a_record_whose_rfq_is_gone_does_not_load` is the reachable half of I-E.
**Nothing in this repository deletes an RFQ** — deleting a project is refused
while it holds one — so the plan's "deleting an RFQ takes its documents" case
has no door to knock on, and inventing an RFQ-deletion endpoint to test it
would have been a user-visible feature nobody asked for. The invariant is held
at both ends instead: `add_rfq_document` refuses an RFQ that is not there, and
a record whose RFQ has gone is dropped on load rather than kept as a row
pointing at nothing.

`test_no_leaf_name_may_carry_a_path_separator` is why `doc_store` needs no
traversal check of its own. A blob path is built from three leaves — an RFQ
id, a digest and a file name — each refused if it carries a separator, so
containment holds by construction. There is exactly one traversal guard in this
repository and it is `workflow/safe_extract.py`; a second copy in the blob
store would be the copy that does not get fixed.

The web suite is separate and not part of either row above — both rows are
`python -m pytest` counts. Run it with `npm test` under `web/` (vitest,
non-watching, exits non-zero on failure); `npm run build` also type-checks the
test files, since `web/tsconfig.app.json` includes `src`. CI runs both, in the
`web` job of the same workflow. It stands at **379 passed** across 24 files,
measured on 2026-08-19. The 335 this line carried before was stale by 36 —
the count is only worth anything measured, which is the same argument the two
Python rows make above.

The last **8** are the enquiry card's zero state, all in `SendEnquiry.test.tsx`.
A preview that reaches nobody used to render a live `Send to 0 vendors` button
under `This sends real email to 0 vendors.` — a control whose only honest
outcome is nothing happening, sitting where the action goes, on precisely the
screen a buyer reaches once the enquiry has gone out. There is now no send
control for nobody: the card says which audience reached nobody, and offers the
one act that would change it.

**That resend is one press, and it is the one place a send happens without the
addresses being on screen first.** Under `unsent` an already-sent vendor's row
shows why they are out of scope, not the mailbox they would be written to, so a
contact sheet re-uploaded since the first send changes where the mail goes
without this table showing it. It was chosen deliberately over
widening-then-previewing — the vendors are the ones already listed above, and
the second press was the thing being removed — and the send re-previews
afterwards, so the table is never left describing a state the press just
changed.

Two of the eight assert an **absence** — no `Send to 0` control, and no resend
offered when `all` is already the audience — so both passed the moment they were
written. All eight were watched failing against the previous component
(`git show HEAD:web/src/pages/wizard/SendEnquiry.tsx` swapped in), which is the
only thing that makes an absence-assertion known to be wired to anything.

Three existing tests changed with them, and the reason is the same in each: they
previewed an **empty** recipient list and then clicked a send control that no
longer exists there. One of the three had been asserting that `smtp` says "real
email" against a preview reaching nobody — a sentence that is now simply not
rendered, because promising real mail to zero people is the defect. None of the
three assertions moved; only the fixtures they run against.

No server change: `AUDIENCES`, `resend` and the append-only `EnquirySend` rule
already did all of this, and the Python suite is untouched by it.

The last **12** are the collapsible card: 5 in `primitives.test.tsx`, which had
no `Card` block at all, and 7 in `ItemDetail.test.tsx`. The item screen's four
vendor lists and the registry card under them each fold away from their own
header — measured in a browser, the client's 52-row export made that card
2 914px tall and pushed everything below it off the screen.

**An upload holding rows starts folded; everything else starts open.** Not a
row-count threshold, which would be a rule nobody can see — a card behaving
differently at ten rows and eleven. The line is drawn on what the card *is*: an
export is a document somebody already read before loading it, and the two
curated cards under it are where the work happens. The `entries.length` half is
the part worth keeping: an empty upload's body is not a list at all, it is the
sentence telling the reader to edit the item and load the export, and folding
the instructions away leaves a card that says only that it is empty.

The load-bearing one is `keeps the body mounted, so collapsing loses no
half-finished work`. Rendering the children only while expanded is the obvious
implementation, and it throws away whatever the reader had typed into the card —
on this screen the add-a-vendor form, and a list of suggestions that cost a
provider call to fetch. `Card` sets `hidden` on the body instead, which is also
what keeps the `aria-controls` target in the document. Verified by switching to
the unmounting version and watching that one test go red while the hide
assertions stayed green; those were verified the other way, by dropping the
`hidden` attribute entirely.

**`hidden` is why three older tests in that file had to be opened first.** A
`hidden` body answers "not in the document" to every role query and *nothing at
all* to `getByText`, so the default above silently changed what they measured:
two asserted a vendor name that `getByText` finds inside a folded card just as
happily, and `carry no add or remove control` — the one that matters — would
have passed just as well against an uploaded card that had grown a Remove
button. All three now click Show first, and the third asserts the table is there
before asserting the controls are not.

`collapsible` is **opt-in** and one test asserts the absence, because every card
on every screen renders through this component and a Hide control on the metrics
strip or an edit form would be a change nobody asked for. The count in the
heading is not decoration: the heading is all there is to read once a card is
folded, and a collapsed list that does not say how big it is gives the reader no
reason to open it again. It rides in the `<h2>`, so the region's accessible name
becomes "Client list 52" — every existing `findByRole('region', { name })` query
on this screen matches on a regex and still resolves.

Two things here jsdom cannot see, both checked by measuring in a browser.
`.card-head-actions` wraps the toggle *and* whatever `actions` a card already
passed, so the two cards that pass one (`ProjectDetail`'s Items, and
`ExtractionStatus`) had their header geometry re-measured rather than assumed —
unchanged, since `actions` was already a flex row. And a collapsed card is a
header with nothing under it, so `.card-head`'s `border-bottom` would draw a
second line 1px above the card's own bottom border; `.card-head:has(+ [hidden])`
zeroes it. That rule was written ahead of the defect rather than after it, and
then *confirmed* by measuring `borderBottomWidth` in both states — which is the
only way to tell a rule that works from one that was never needed.

The change before that **removed** one, net: the Issued step is now one upload control
and the list of what has been uploaded, and nothing else. The technical-package
editor that stood there — a revision, a basis of design, a category picker, two
upload buttons, Save and Freeze — and the VDRL register under it are both gone,
asked for as "just a single location to upload files". `RaiseRfqStep.test.tsx`
goes 12 → 13 and four tests in `RfqWizard.test.tsx` become two, deleted rather
than skipped: they drove a revision field, a Freeze button and an attachment
register that no longer exist. `IssuedStep.tsx` went with them — it was a
wrapper around the two halves — and `api.ts` no longer carries
`setTechnicalPackage`, `freezeTechnicalPackage`, `addVdrlLine` or
`removeVdrlLine`. Their routes are still served and still covered by the Python
suite, the same treatment the removed Bidders screen's writes got.

**Two capabilities left the browser with them, and this is the record of it.**
Nothing in the front end freezes a package any more, so an RFQ raised from here
has no revision for an addendum to supersede — `issue_addendum` is unreachable
in practice for anything new, though the store rule is untouched. And nothing
sets a VDRL line, so the `vdrl_received / vdrl_required` tally on Bids Received
only ever counts lines the demo seed wrote. Both were stated before the change
and chosen anyway; neither is an oversight to be quietly "fixed" by restoring
the editor. The frozen view survives because the server still refuses uploads
against a package frozen earlier — the controls go and the reason is said out
loud, rather than an upload failing with a refusal nobody can explain.

Three of the thirteen are the ones to keep. `walks a dropped folder and sends
its paths, positionally` is the one that needed the code it tests:
`dataTransfer.files` is **empty** for a dropped folder, so a handler reading
only that ignores the drop entirely and reads on screen as nothing happening.
Its sibling refuses a partial path list, because `paths` is positional
server-side — the same rule the two upload buttons kept, now reached through one
zone. And `is one upload control and a list, and nothing else` asserts the
absence of all six deleted controls plus a single `.dropzone`; it is what fails
if any of it grows back.

**The zone is the ingestion screen's `.dropzone`, not a second one.** A first
pass wrote a new `.dropzone` block in `theme.css` and shipped it green: same
class name as the Setup screen's, later in the file, silently restyling both
upload zones there — display, padding, border and radius all changed under a
component nobody had touched. jsdom cannot see it, and neither can a test that
renders one screen at a time. It was found by measuring both zones in a browser
and is fixed by reusing the existing widget: a `<label>` wrapping an `.sr-only`
input, with `.drag` while a drag is over it, which also means a click reaches
the picker natively rather than through a handler that can go missing.

One measurement trap worth knowing, since it cost a false alarm: with the
preview pane hidden, `document.visibilityState` is `"hidden"` and **CSS
transitions never advance**, so `getComputedStyle` returns the pre-transition
colour forever. `.dropzone` transitions `border-color`, so the drag highlight
reads as broken. Set `style.transition = 'none'` before measuring a transitioned
property, or you will chase a defect that is not there.

The last **9** are the collapsible card: 5 in `primitives.test.tsx`, which had
no `Card` block at all, and 4 in `ItemDetail.test.tsx`. The item screen's four
vendor lists and the registry card under them each fold away from their own
header — measured in a browser, the client's 52-row export made that card
2 914px tall and pushed everything under it off the screen.

The load-bearing one is `keeps the body mounted, so collapsing loses no
half-finished work`. Rendering the children only while expanded is the obvious
implementation, and it throws away whatever the reader had typed into the card —
on this screen the add-a-vendor form, and a list of suggestions that cost a
provider call to fetch. `Card` sets `hidden` on the body instead, which is also
what keeps the `aria-controls` target in the document. Verified by switching to
the unmounting version and watching that one test go red while the four hide
assertions stayed green; those four were verified the other way, by dropping the
`hidden` attribute entirely.

`collapsible` is **opt-in** and one test asserts the absence, because every card
on every screen renders through this component and a Hide control on the metrics
strip or an edit form would be a change nobody asked for. The count in the
heading is not decoration: the heading is all there is to read once a card is
folded, and a collapsed list that does not say how big it is gives the reader no
reason to open it again. It rides in the `<h2>`, so the region's accessible name
becomes "Client list 52" — every existing `findByRole('region', { name })` query
on this screen matches on a regex and still resolves.

Two things here jsdom cannot see, both checked by measuring in a browser.
`.card-head-actions` wraps the toggle *and* whatever `actions` a card already
passed, so the two cards that pass one (`ProjectDetail`'s Items, and
`ExtractionStatus`) had their header geometry re-measured rather than assumed —
unchanged, since `actions` was already a flex row. And a collapsed card is a
header with nothing under it, so `.card-head`'s `border-bottom` would draw a
second line 1px above the card's own bottom border; `.card-head:has(+ [hidden])`
zeroes it. That one was written ahead of the defect rather than after it, and
then *confirmed* by measuring `borderBottomWidth` in both states — which is the
only way to tell a rule that works from one that was never needed.

The **12** before those are Bid Desk's BD-6, in the new `RaiseRfqStep.test.tsx`. That
component replaced `TechnicalPackageEditor.tsx`, which is deleted: the register
of document codes it edited is now the documents themselves. `AttachmentTable`
stays — `Addendum.attachments` still uses the record, and a package that
carries lines still renders them — but nothing in the wizard adds one any more,
so `Save package` sends back the lines already there rather than `[]`.

Two of the twelve are worth knowing about. `offers a folder picker, not just a
file picker` asserts the `webkitdirectory` **attribute**, because React's
typings do not carry it and it is set through a ref — the line is one `useEffect`
wide, and without it "Add a folder" quietly adds one file. And
`sends no paths at all when only some files carry one` is the positional rule:
`paths` lines up with `files` server-side, so a partial list attaches a path to
the wrong file. **That one needed `beforeEach(() => vi.clearAllMocks())` to
mean anything** — without it `toHaveBeenCalledWith` matches a call an *earlier*
test made, and it passed against a deliberately broken component. Verified by
flipping `every` to `some` and watching it go red, which is the only reason the
gap was found.

**A fifth rendering defect joins the four below, same story again.**
`.field-label` has asymmetric stacked margins (`0.8rem` over, `0.3rem` under),
right for a label above its input and wrong inside `.fxrow`, which centres its
children: "Category" measured 5px below the control it names. `.field-label--inline`
zeroes them. jsdom applies no stylesheet and does no layout, so nothing in this
suite can see it — it was found by measuring `getBoundingClientRect` on all four
children of that row in a real browser, and confirmed fixed the same way.

The **5** web tests before those are Bid Desk's BD-5: reworking the
Shortlisting step and dropping the item screen's Raise RFQ door. Three in
`ShortlistingStep.test.tsx` assert what replaced the Registry section — no
heading, no candidate search, and the escape hatch for a vendor nobody
registered still there behind its disclosure — plus that the approval control,
with its status line, now precedes the invited-bidders table in DOM order.
`_shortlisting_exit` (`workflow/gates.py`) checks an included vendor, then
approval, then the TBE template, in that order; the control used to sit after
the whole vendor half of the step — the invited-bidders table, the Registry
section and the disclosure — rather than leading it, so a top-to-bottom reader
met it only after scrolling past everything it approves. Two in
`ItemDetail.test.tsx` assert the covering-RFQ card carries no Raise RFQ
control, with an RFQ already covering the item and without one.

**Fourteen** web tests were deleted outright across two files, not skipped —
a gross count, not a net one, since four more land elsewhere below. Seven in
`RfqWizard.test.tsx` drove the candidate-list UI directly — inviting from a
candidate row, the scope-fit filter, the candidate search box, an override
reason typed against a blocked candidate, and the already-invited mark inside
that list — none of which exists any more: choosing who to invite now happens
on the item screen's `AvailableVendorList`, over the four-source pool BD-1
built, which was already strictly more than the registry-only search this
step carried. Seven in `ItemDetail.test.tsx` drove `RaiseRfqForm` reached
through this screen's own Raise RFQ button: two exercised the button and form
directly, and five drove the form's Discipline picker, its product groups,
its package placeholder and its budget field.

Only one of those five was already covered elsewhere — the budget field's
bare existence, which `ProjectDetail.test.tsx`'s own assertion that the same
field takes text rather than a number cannot pass without the field being
there under that label. **The other four were not**, and a first pass at this
paragraph claimed they were, checked by memory of what `ProjectDetail.tsx`
covers rather than by reading it — a review caught it, with `grep -rn
"optgroup" web/src` turning up nothing outside the block that had just been
deleted. `forms.tsx`'s `DisciplineSelect` in its `productGroups` mode (used
only by `RaiseRfqForm`) was left with no assertion on its `<optgroup>`
structure, on offering both the family and the leaf product groups, on a leaf
value being what actually gets sent, or on the package field's placeholder.
All four are now ported into `ProjectDetail.test.tsx`, adapted to the door it
already opens the form through (tick an item, click Raise RFQ) rather than
copied verbatim from the deleted block, bringing the web total from 307 to
**311**.

None of this phase's tests touch a fixture directory or a provider key, and
none of the three files carrying the `needs_real_avl` marker changed, so the
AVL gate is still eleven and was not re-measured — the Python suite is
untouched by this phase.

The **4** Python tests and **1** web test before those fix `StageStrip`'s
process-code labelling — the gap the BD-4 paragraph below once flagged as known
and not fixed. Two in `test_workflow_stages.py` assert `STAGE_CODES` against the
client document's own references rather than a position; two in
`test_workflow_endpoints.py` assert the two endpoints that now serve it,
`/api/workflow/stages` and the RFQ roster. None touches a fixture directory or a
provider key, so all four land in both rows. The web test extends
`StageStrip.test.tsx` to assert Negotiation and Awarded render the *same* code —
the case a positional strip cannot get right. `StageStrip` now renders whatever
`codes` it is sent instead of computing `RFQ-${i + 1}`; the codes travel from
`workflow.stages.STAGE_CODES` through both endpoints to both call sites,
`RfqWorkflow` and `RfqDetail`.

The **8** Python tests and **1** web test before those are Bid Desk's BD-4,
removing the `Scoping` stage — see the retired-stage invariant above. The wizard
is now Shortlisting → Issued → Clarifications, and **the technical-package
editor moved rather than vanishing**: `_scoping_exit` was the only thing
requiring a frozen package and the Scoping step was the only place to freeze
one, so deleting both would have left addenda permanently refused with nothing
able to unblock them. It moved to `wizard/TechnicalPackageEditor.tsx`, rendered
by `IssuedStep` above the VDRL, freeze rule unchanged — and BD-6 then replaced
that file with `wizard/RaiseRfqStep.tsx`, in the same slot and under the same
rule, editing real documents rather than a register of codes.

Two gate tests were **deleted, not skipped**, with a comment where they sat:
they asserted an exit criterion that no longer exists.

Known and not fixed by that phase, **since fixed separately**: `StageStrip`
computed each stage's process code as `RFQ-{i+1}`, but those codes are **fixed
references, not positional** — there is an `RFQ-04A`, and Negotiation and
Awarded deliberately share `RFQ-06`. It was already mislabelling those two
before BD-4; removing Scoping just moved the error to the first stage where it
was visible. The fix is above, out of chronological order in this file because
it landed after BD-4 rather than as part of it: `api/workflow_routes.py` now
serves `STAGE_CODES` from `workflow/stages.py` on both endpoints the strip
reads, and `StageStrip` renders the sent code instead of computing one.
`docs/rfq-process.html` still documents nine stages and is tracked separately.

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

- **A retired stage keeps its name in history, and only the RFQ's own `stage`
  moves.** `Scoping` was removed in Bid Desk BD-4, and a document written before
  that names it in two different places. Its **history** entries are left
  exactly as they are — history is append-only, and rewriting the entry that
  records an RFQ's creation to say it began somewhere it did not is precisely
  the falsification that rule exists to prevent. Its **`stage`** cannot stand,
  because `TRANSITIONS` has no key for a retired stage and the RFQ would be
  stuck; it moves to the replacement in `stages.RETIRED_STAGES`, with one
  appended entry recording the move, `by: "system"`.
  This is why `StageTransition.from_stage` and `to_stage` are `Stage | str`
  rather than `Stage`. Not laxity — the validator admits **only** the names in
  `RETIRED_STAGES` as strings and still raises on anything else, so a typo
  cannot load as a stage nothing can transition out of. Without it every RFQ
  ever raised would fail to load, because `history[0].to_stage` is `"Scoping"`.
  The migration keys on **`stage`, never on the history**, and that is what
  makes it idempotent: once moved there is no retired stage left to find. Keyed
  on "does the history mention Scoping" it would append on every load forever,
  and only a two-run test tells the two apart —
  `test_loading_a_scoping_document_twice_appends_one_entry`, verified by making
  the migration history-keyed and watching it and its sibling go red.
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
- **The client's export truncates every vendor name at 35 characters, and
  `emails_for` absorbs it.** `AVL_NAME_LIMIT` lives in `workflow/avl_import.py`
  beside the column it describes: the December 2025 ADNOC file has 9 362 rows
  at exactly 35 characters and **none longer**, so the registry's own names are
  cut. Nothing in this repository does the cutting and there is no longer form
  anywhere in the workbook to recover — it cannot be undone at import.
  It does harm in exactly one place: a contact sheet carries the company's
  *full* name, so an exact fold match misses and a real company with an address
  on file reads as "no address on file". `store.emails_for` therefore falls
  back to a folded **prefix** match — and the fallback is deliberately narrow,
  because name matching is a defect this file records twice elsewhere. It
  applies **only** to a name of exactly `AVL_NAME_LIMIT` characters, and
  resolves **only when exactly one contact matches**. Two real companies
  agreeing for 35 characters are indistinguishable from a truncation, so the
  honest answer there is `None`: a buyer told "no address" uploads a sheet,
  while a buyer whose tender reached the wrong company cannot undo it.
  `test_two_contacts_sharing_a_truncated_prefix_resolve_to_neither` is the one
  that keeps this from widening into guessing.
- **The eligibility checklist replaced the TBE template, and the gate that
  wanted one is gone.** `_issued_exit` blocked `Issued → Clarifications` until
  somebody attached a template; the free-text editor that could attach one was
  removed from the Issued step, so keeping the gate would have stranded every
  RFQ at Issued with nothing anywhere able to unblock it — the BD-4 trap
  exactly. **`Issued → Clarifications` is now ungated**, and
  `test_the_issued_gate_is_open_and_asks_for_no_tbe_template` is the assertion
  that fails if the check is ever quietly reinstated.
  The removal is not a loosening. The gate asked whether anyone had settled
  what bidders must return, and that question is now answered *by
  construction*: the checklist is `EligibilityCategory`, nine fixed returnables
  that apply to every RFQ and are stored nowhere per RFQ, so there is nothing
  left to forget. What a buyer may still add on top lives in
  `TbeTemplate.items` and is **extra** — blocking an RFQ for want of an
  optional addition would refuse it for something that is not a requirement,
  which `test_a_buyers_own_checklist_additions_never_gate_the_rfq` pins.
  `store.set_tbe_template` therefore takes `items` rather than `criteria`; bare
  strings are still accepted and read as non-mandatory items, because that is
  precisely what a line of free text was. `persistence` reads a pre-checklist
  document's `criteria` key the same way — a reading of an older key, not a
  migration, which is why `VERSION` does not move.
  **The nine are never stored per RFQ and no route can set them.**
  `TbeTemplateIn` carries only the buyer's own labels, and it has no
  `mandatory` flag: the three categories that block a bid are the gate's, and
  letting a buyer mint a fourth from a text box is a decision that wants its
  own screen.
  The step blurb moved with the control. A blurb is the only sentence telling a
  reader what a step is for and so is the first thing to go stale — the Issued
  step promised a "TBE template" for one commit after the editor had gone, and
  `promises the eligibility checklist on Issued and no TBE template anywhere`
  asserts on the whole step card so the control and the sentence describing it
  are covered by one assertion.
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
  have no RFQ, so nothing there is eligible or blocked. Five tests in
  `test_bidder_suitability.py` cover it.
  **It has no HTTP route, and this paragraph used to say it did.** The claim
  was that `GET /bidders/approved` served it, declared above
  `/bidders/{bidder_id}`, held there by
  `test_the_approved_path_is_not_read_as_a_bidder_id`. None of that was ever
  written: `git log -S'/bidders/approved'` over `api/workflow_routes.py`
  returns nothing on any branch, the named test is in no file, and requesting
  the path today gets `404 Unknown bidder: approved` because FastAPI reads
  `approved` as an id. Nothing in the browser calls it, so no screen was ever
  broken by the gap — which is exactly why it survived: a documented endpoint
  with no caller has nothing to fail. The function is reachable in Python and
  the capability is served over HTTP by `/bidders/available`, which does
  exist. Add the route and its ordering test if a screen ever needs the
  client's whole register on its own; until then this is a note about what is
  absent, not a description of what is there.
  The shadowing rule it described is real even though this instance of it was
  not — `/rfqs/extract` sits above `/rfqs/{rfq_id}` for that reason, and
  `test_the_extract_path_is_not_read_as_an_rfq_id` (`test_rfq_extractor.py:76`)
  is a guard that genuinely exists.
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
  through `vendor_id`.
- **Adoption is what turns the draft into invitations, and it happens when the
  RFQ is raised.** `adopt_draft_shortlist(rfq_id, item_id)` runs inside
  `create_rfq`'s own `locked_update`, once per covered item, so an RFQ never
  exists with its adoption half-done and there is no second screen asking a
  buyer to confirm the selection they already made. Four rules, none of them
  new: it **does not clear the draft** (a second RFQ may cover the same item
  later, and emptying the basket is a surprise nobody can undo); it is
  **idempotent**, keyed the way the draft is keyed, so a registry row and a
  hand-typed one sharing a trading name adopt as two rows; a registry pick
  adopts **through the existing `vendor_id` path**, so the snapshot is derived
  from the registry by `add_shortlist_entry` rather than copied from a draft
  that holds none of it; and a **blocked bidder is skipped, not invited** —
  inviting one requires a recorded reason and adoption has nobody to attribute
  one to.
  **A skip is reported, never dropped.** It returns a `DraftAdoption` — the
  count *and* every skipped vendor with the refusal's own sentence — and
  `POST /rfqs` sends both back as `shortlist_adopted` / `shortlist_skipped`,
  which the project screen renders in its warning banner. A count alone is
  silent about what did not arrive, and a buyer whose shortlist is quietly one
  row short has nothing to act on; that is the same rule as "a gate never
  returns a bare `False`". Exactly **two** conditions are absorbed and both are
  named — a registry row that has gone, checked before the call, and
  `BlockedBidder`, which exists as a distinct `ValueError` subclass precisely
  so this catch can be narrow. The blanket `except (ValueError, KeyError)` this
  replaced also swallowed `IncompleteShortlistEntry`, and would have swallowed
  any validation `add_shortlist_entry` grows later: a regression there would
  have presented as vendors quietly vanishing from shortlists.
- **An RFQ's documents are real files, and the bytes are content-addressed.**
  `<ROOT>/rfq-docs/<rfq_id>/<sha256[:2]>/<sha256>/<original filename>`, written
  by `workflow/doc_store.py` — stdlib only, no `workflow.*` imports, the same
  rule `procurement/store/layout.py` states. **I-D**: that directory holds
  exactly the blobs referenced by live `RfqDocument` records, so deleting a
  record deletes its blob **only when no other record still references it**.
  Two records legitimately point at one blob — the same bytes uploaded twice —
  and the answer is computed in `remove_rfq_document`, after the removal and
  against what is left, because it is a read that gates a write to the
  filesystem. The key is the digest **and** the leaf name, since the path is
  built from both: the same bytes under two names are two files, and keying on
  the digest alone would strand one of them. `BlobRef.rel_path` is
  storage-relative and never absolute, so a record survives the root moving and
  an S3 implementation has somewhere to put a key; **S3 is not built**, and the
  `BlobStore` protocol exists so that adding it is a new class rather than a
  refactor.
  **I-E**: `workflow.json` holds exactly the `RfqDocument` records whose RFQ
  still exists. Nothing deletes an RFQ, so it is held at both ends —
  `add_rfq_document` refuses an unknown RFQ, and `persistence.from_document`
  drops a record whose RFQ is gone rather than loading a row pointing at
  nothing.
  **One collection serves both halves of the enquiry.** `submitted_by_vendor_id`
  is `None` for a document the contractor issued and a vendor id for one a
  bidder returned; the freeze rule guards the **contractor's** half only,
  because bids arrive after the freeze and refusing them would turn the rule
  that protects the enquiry into one that destroys the responses to it.
  `TechnicalPackage.documents` is **rebuilt from the records** by
  `_relist_package_documents` and `set_technical_package`, never accumulated,
  so a list of ids on the package cannot disagree with the collection it points
  into — and a package created after its documents were uploaded still lists
  them.
- **Enquiry mail defaults to safe, and the flag is read in one place.**
  `mail.transport_for` is the only reader of `MAIL_TRANSPORT`, and anything
  other than an explicit `smtp` — absent, empty, `0`, `false` — returns
  `OutboxTransport`, which writes an `.eml` under `<ROOT>/outbox/` and sends
  nothing. Routes never construct a transport, so there is no second place for
  the default to be got wrong. A *refused* SMTP configuration raises
  `MailConfigError` at construction and **never falls back to the outbox**: an
  operator who asked for real mail and silently got a file believes a tender
  was sent.
  **One `OutboundMail` per vendor, built inside the loop.** A single message
  addressed to the whole shortlist tells every bidder who their competitors
  are, which is a tender that has to be re-run. No `Cc` or `Bcc` spans vendors,
  and the test asserts across the whole dispatch — a per-message assertion
  passes against a loop that sends the same all-recipients message N times.
  Only the contractor's documents ride along (`submitted_by_vendor_id is
  None`), so a returned bid can never reach a competitor.
  **Exactly three conditions skip a vendor** — no address in the directory,
  the package over `MAX_ATTACHMENT_BYTES`, and already sent — each reported
  with its own sentence. Everything else propagates: a blanket `except` would
  report a broken mailer as "vendors skipped" and send a buyer to fix the
  directory. The count alone is never enough, the same rule as *a gate never
  returns a bare `False`*.
  **`EnquirySend.to` is stored and frozen, and that is the opposite of the
  shortlist's `email`.** That key is derived on read, so correcting the contact
  sheet corrects every shortlist at once; this records what actually happened,
  and re-uploading the sheet must not rewrite who a tender reached — the same
  reason `ShortlistEntry.prequal_status` is frozen. `transport` is on the
  record too, because "this went to the outbox" and "this reached a real
  mailbox" must not be indistinguishable later.
  The collection is **append-only and holds one record per *send***, not one
  per shortlist entry: removing a shortlist entry leaves its send record,
  because the mail was sent and deleting the record would falsify that. A
  second press of Send reaches whoever was shortlisted since and nobody else.
  **Unless a wider audience is asked for.** `enquiry.AUDIENCES` has three
  members and they are deliberately **nested** — `unsent` ⊆ `outdated` ⊆
  `all`. `unsent` is the default and reaches only vendors never sent to.
  `outdated` adds those whose last send predates the newest contractor
  document: the answer to "something was added to the RFQ, who has not seen
  it?" A vendor never sent to counts as outdated, because a disjoint
  "only the stale ones" bucket would have to be run *alongside* `unsent` to
  cover the shortlist, and the second run is the one people forget. `all`
  reaches everybody again.
  An unknown audience is a **422, never a fallback to the default** — reaching
  nobody is indistinguishable on screen from "everybody is up to date", the
  same reasoning as the approver filter's refusal. `check_audience` is the one
  place that decides, and both routes call it.
  Staleness is computed from `store.last_send_by_entry`, which takes the
  **max** `sent_at` per entry: the collection holds one record per send, so an
  entry sent twice has two and only the latest answers "are they behind?".
  A package holding no documents makes nobody outdated — there is nothing to be
  behind on — and an unparseable `uploaded_at` is treated as infinitely new
  rather than as absent, so a corrupt stamp tells the buyer to re-send instead
  of silently reporting everyone up to date.
  The store's permission stays a plain boolean: `dispatch` passes
  `resend=audience != UNSENT` to `record_enquiry_send`. Audience is *policy*
  about who to mail; `resend` is *permission* to write a second record, and
  keeping them apart is why the store needs no notion of what an audience is.
  Once permitted the record is *appended*; refusing it would leave a mail that
  really went out unrecorded, and overwriting the first would destroy the date
  the vendor was originally written to. `already_sent_entry_ids` stays a
  **set** — it answers whether, not how many times.
  A wider audience widens who is *asked for*; it never invents a way to reach
  somebody. A vendor with no address on file is skipped under every audience.
  On both routes it is a **query parameter, never a body field**, so the send
  route still takes no request body — the property that makes it impossible to
  sign somebody else's name to a tender. `audiences` rides on the preview
  response so the browser spells none of the names itself, the same reason
  `selectable_approvers` and `document_categories` are sent.
  **The picker renders both before and after a preview, and that is a fix
  rather than a detail.** It first shipped only in the pre-preview branch —
  with a test asserting it disappeared — so the moment a buyer saw *everyone
  has already been sent*, which is precisely when the audience needs widening,
  the control had gone and only a page reload brought it back. Changing it now
  re-previews immediately, so the table on screen is always the list that would
  go out. The send uses `preview.audience` — what the server echoed back for
  the list being displayed — never the picker's current value: a confirmation
  screen that lists one set of vendors and then mails another is worse than no
  confirmation at all.
  **`workflow.json` holds exactly the records whose RFQ still exists**, held at
  both ends like `RfqDocument` — the store refuses an unknown RFQ, and
  `from_document` drops a record whose RFQ has gone.
  **No test may require an SMTP host.** The suite is key-free, and a test that
  reads `MAIL_TRANSPORT` from the developer's own environment is a test that
  mails from their account — see `test_enquiry_endpoints.py`, which clears the
  variable for exactly that reason.
- **The checklist has one definition and three readers, and it is
  `workflow/checklist.py`.** Pure — no store, no I/O, no clock, the same shape
  as `bidders.py` and `eligibility.py`. The nine returnables **are**
  `EligibilityCategory`, iterated in its declared order and lettered from that
  position, and the `(must have)` marks come from
  `eligibility.assess(issued, submitted=[])` — with nothing submitted,
  `missing` *is* the required set. Neither the labels nor the mandatory rule is
  copied anywhere.
  The three readers are the enquiry mail's body (`workflow/enquiry_body.py`,
  which formats these rows and decides none of them), the RFQ payload the
  browser renders (`eligibility_checklist`, sent for the same reason
  `document_categories` and `selectable_approvers` are), and
  `eligibility.assess` itself, which is the source rather than a reader. A
  checklist typed into a screen would tell the buyer one thing while the mail
  told the vendor another and the gate enforced a third, and **none of those
  mismatches is visible from any of the three ends** — which is why the browser
  is sent the rows and `shows no returnable the server did not send` asserts it
  builds none of its own.
  `enquiry_body` is pure in the same way; `dispatch` resolves the project, the
  items, the contractor's documents and the package at the boundary and calls
  in.
  That is not tidiness. The bidder cannot see the gate; this body is the only
  description of it they will ever get, so a checklist typed into a string
  literal would keep asking for the old thing while the gate kept rejecting
  them for the new one — and the mismatch is invisible from both ends. Asking
  `assess` also means the buyer's own additions arrive free the moment
  `extra_items` lands there, with no edit here.
  Only (c)'s **wording** lives in this module, because it reads two ways — a
  compliance sheet was issued and must come back filled in, or none was and a
  deviation list is wanted instead. The rule deciding *whether* (c) is required
  at all stays in `assess`, which is why an enquiry carrying no documents does
  not mark it.
  **The estimate never travels.** `RfqRecord.value_estimate_aed` and
  `Item.estimated_value_aed` are the contractor's own budget; in front of the
  bidders being asked to price the work they are the number to beat, and there
  is no recovering the tender afterwards. `test_the_body_never_discloses_the_estimate`
  checks four renderings of it, and its sibling in `test_enquiry_dispatch.py`
  checks the message that actually leaves.
  **One body, built once, before the loop.** That is the one-message-per-vendor
  rule reaching into the content: a body assembled per vendor is a body that
  could *differ* per vendor, and prose naming a competitor is a leak no address
  assertion would ever catch. `build_body` is not given a shortlist at all, and
  a test asserts the parameter's absence.
  `_contractor_documents` is the single filter on `submitted_by_vendor_id is
  None`; `_attachments` turns those into bytes and the body names them. Two
  copies of that filter is how a vendor's returned bid ends up described in a
  mail to their competitor.
- **There is one traversal guard, and it is `workflow/safe_extract.py`.**
  Extracted from `procurement/project.py::unpack_vendor_zip`, which now calls
  it: a second copy of a security check is the copy that does not get fixed.
  Zip members and a browser's `webkitRelativePath` are the same risk arriving
  through two doors, so they take the same guard. It is deliberately stricter
  than the inline version it replaced — a `..` segment is refused **on the
  segment**, not on where it lands, and both separators are normalised first,
  so `..\\..\\evil` is refused on Linux where it is a legal filename as well as
  on Windows where it is traversal. Refusals name the offending entry and never
  the destination directory: the entry is the uploader's own text and the
  directory is ours. `doc_store` needs no such check — it builds paths from
  validated leaves — and adding one there would be the second copy again.
  **`os.path.splitdrive` is not how you ask whether something is absolute.**
  It is `ntpath` on Windows and `posixpath` on Linux, and the POSIX one is a
  no-op: `posixpath.splitdrive('C:/Windows/x')` answers `('', 'C:/Windows/x')`.
  The drive check shipped built on it, so it refused a drive letter on a
  workstation and waved it through on the deployment platform and on CI — a
  check that reads as one and is not, which is worse than none. It is now
  `safe_extract.has_drive`, a regex, and `doc_store` imports *that* rather than
  keeping its own copy: the one exception to that module's no-`workflow.*` rule,
  bound to that one predicate, because the alternative is two copies of a check
  that has already been wrong once. `test_the_drive_check_gives_the_same_answer_on_every_platform`
  asserts the predicate rather than the refusal, since that is the only way a
  Windows run can tell the two implementations apart — `pytest.raises` is
  satisfied by both here and by only one on CI, which is how it passed locally
  while asserting something false.
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
  exists for. The route is declared **above** `/rfqs/{rfq_id}`, or FastAPI
  reads `extract` as an id and the route 404s, and
  `test_the_extract_path_is_not_read_as_an_rfq_id` holds it. (This paragraph
  used to cite `/bidders/approved` as the precedent. That route does not
  exist — see the approved-vendor-list paragraph — so the rule is stated here
  on its own terms instead of leaning on a sibling that was never built.)
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
