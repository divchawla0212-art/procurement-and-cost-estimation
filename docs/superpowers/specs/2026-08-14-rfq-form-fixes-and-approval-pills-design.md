# Raise-an-RFQ form fixes, approval pills, and shortlisting from the item screen — design

**Date:** 2026-08-14
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-13-client-approval-gap-design.md`](2026-08-13-client-approval-gap-design.md)

---

## The problem

Four separate reports against the screens raised in the last three phases. Two
are defects in the raise-an-RFQ form, one is a gap in how approval is shown, and
one is a placeholder for work not yet scoped.

1. **A money field the scroll wheel edits.** Both money inputs are
   `type="number"` — `RaiseRfqForm`'s *Estimated budget (AED)*
   ([`forms.tsx:454`](../../../web/src/pages/forms.tsx)) and `ItemForm`'s
   *Estimated value (AED)* ([`forms.tsx:313`](../../../web/src/pages/forms.tsx)).
   A focused number input increments on wheel scroll in Chrome and Edge, so
   scrolling a long form past a filled-in budget silently changes it. Nothing on
   screen marks the value as edited and the form submits the altered number.
   This is the defect class the store invariants already forbid downstream —
   "missing data is never coerced to a passing or zero value" — arriving instead
   at the point of entry, where no invariant guards it.

2. **The form denies holding the file it just read.** The enquiry-document
   picker clears itself immediately after reading:

   ```tsx
   e.target.value = ''            // forms.tsx:421
   ```

   That line has a real reason — it is what makes picking the *same* file twice
   read it twice, which is the retry-after-failure case. But clearing a file
   input resets its native label to **"No file chosen"**, so a successful read
   leaves the screen reporting that nothing was uploaded. The four fields fill
   in and the control beside them contradicts that. There is also no way to
   re-open the document to check the extraction against it.

3. **Approval is a grey badge, and half of it never reaches the shortlist.**
   `approved_by` is rendered as an undifferentiated `.approval-badge`
   ([`theme.css:1345`](../../../web/src/theme.css)) — same border, same muted
   grey for ADNOC and for Astra, on the candidate cards only. On a registry
   imported from a client AVL nearly every candidate carries ADNOC, so the one
   badge that actually distinguishes two vendors is the one rendered identically
   to the badge that does not.

   Worse on the shortlist itself: `_shortlist_payload` sends `client_approved`,
   a single boolean about `CLIENT_APPROVER`
   ([`workflow_routes.py:471`](../../../api/workflow_routes.py)). Astra approval
   is **not derivable from it**, so the Shortlisting step can render "ADNOC" or
   "Not on the ADNOC list" and can say nothing at all about our own
   qualification — the other half of what `AVAILABLE_APPROVERS` calls available.

   And the item screen, which is where a buyer looks *before* an RFQ exists,
   lists available vendors with no approval mark and no way to act on one. The
   `AvailableVendorList` card is read-only: having found the vendor, the reader
   has to navigate to a project, raise or open an RFQ, reach the Shortlisting
   step, and search for them again.

4. **No door to a web search for comparable vendors.** Wanted as a visible
   affordance now, with no implementation behind it.

## What this builds

- Both money fields become text inputs carrying a string, parsed once on submit.
- The enquiry-document picker states what it holds and offers to open it.
- `approved_by` reaches the shortlist row, derived on read.
- One shared pill component, coloured per approver, on all three surfaces.
- A **Shortlist** control on the item screen's vendor list.
- An approval summary against each RFQ covering the item.
- A disabled web-search button on the RFQ wizard header.

**Nothing new is stored.** The eight stages, `TRANSITIONS`, every gate,
`ShortlistEntry`, `Bidder`, and the shape of `workflow.json` are untouched. That
is the single invariant this whole change owns, and §8 says how it is defended.

---

## 1. The money fields — `web/src/pages/forms.tsx`

Both become:

```tsx
<input id="r-value" className="field" type="text" inputMode="decimal" required
       value={value} onChange={(e) => setValue(e.target.value)} />
```

`type="text"` is what removes the behaviour: no spinners, no wheel stepping, no
arrow-key stepping. `inputMode="decimal"` keeps the numeric keypad on a phone,
which is the one thing `type="number"` was buying.

**Why not `onWheel={(e) => e.currentTarget.blur()}`.** The common workaround
suppresses the wheel only while the field is unfocused-by-that-blur. Arrow keys
still step the value, the spinners are still there to be clicked, and scrolling
back and clicking into the field re-arms it. It treats one symptom of a control
that is wrong for the job.

**Parsing moves to submit, and is guarded.** Today `ItemForm` calls
`Number(e.target.value)` on every keystroke, so a half-typed value round-trips
through `NaN`, and `RaiseRfqForm` calls `Number(value)` at submit with no check
at all — `"1,800,000"` reaches the server as `NaN`, which serialises to `null`
and is refused with a validation error naming a field the reader did not think
they had touched.

```tsx
const parsed = Number(text.trim())
if (!Number.isFinite(parsed)) throw new Error('Estimated budget must be a number.')
```

Thrown from inside the `useSubmit` callback, so it surfaces through the existing
`FormError` slot with no new plumbing. `Number('')` is `0`, not `NaN`, so the
empty case stays with `required` where it already is.

Each form names its own field — `Estimated budget must be a number.` in
`RaiseRfqForm`, `Estimated value must be a number.` in `ItemForm` — because the
two forms are rendered on the same screen and a shared sentence would not say
which one to go and fix.

**`ItemForm` holds two pieces of state for one field**: the raw string the
reader is typing, and `estimated_value_aed` on the input object. The string is
the source of truth for the input; the number is written from it on submit only.
Keeping the number live per keystroke is what forces the coercion this section
removes.

**Load-bearing consequence:** `ProjectDetail.test.tsx:322` asserts
`toHaveValue(500000)` on the budget field. A text input's value is a string, so
that assertion becomes `'500000'`. This is a real behaviour change and the test
is updated to state it, not loosened to accept both.

## 2. The picked enquiry document — `web/src/pages/forms.tsx`

`RaiseRfqForm` gains `const [picked, setPicked] = useState<File | null>(null)`,
set before `extract` runs.

`e.target.value = ''` **stays.** Its comment already explains why, and the bug
is not that line — it is that the input's native label was the only thing
reporting what had been picked. The React state is now that report, and it
survives the clear.

Below the picker, replacing the `Optional. PDF, Word or Excel.` hint once a file
exists: the file's name, and a **View** button that opens it.

```tsx
const url = URL.createObjectURL(picked)
window.open(url, '_blank', 'noopener')
```

The object URL is created on click and revoked when `picked` is replaced or the
form unmounts — not held from the moment of picking, which would leak a blob for
every file the reader tried.

**Shown after a failed read too.** The reader picked a file; being able to open
the one that failed is more useful than being able to open only the ones that
worked, and the failure message sits directly above it.

### Known limitation, stated rather than solved

The document is **never uploaded**. `POST /rfqs/extract` reads and discards, and
CLAUDE.md records that as deliberate: "the RFQ is created when the reader submits
the form … so a bad extraction is corrected rather than undone and an abandoned
upload leaves nothing." An object URL is therefore the only thing that can back
a View control, and it dies with the form — reload the page and the link is gone
because the file is gone.

That is honest and it is the small version. Storing the enquiry document against
the RFQ is a real feature — a route, a location on disk, a retention question,
and a change to what `/rfqs/extract` is permitted to do — and it is deliberately
not smuggled in behind a bug fix.

## 3. `approved_by` on a shortlist row — `api/workflow_routes.py`

`_shortlist_payload` gains one key beside `client_approved`:

```python
"approved_by": None if bidder is None else list(bidder.approved_by),
```

**Three states, matching the key above it.** `None` is "no registry row to
check" — a vendor typed in by hand, or a `vendor_id` pointing at a deleted
bidder. `[]` is a real registry row that nobody has approved. Collapsing the
first into the second would put a finding on screen that nobody made, which is
the mistake `client_approved`'s docstring already names.

**Derived on read, never stored.** No field on `ShortlistEntry`, no key in
`workflow.json`. Same rule and same reason as `client_approved` and
`approval_caution`: a copy taken at invitation is wrong the moment `approved_by`
is corrected, and correcting it is the common case.

**Why not derive it in the browser from `client_approved`.** It cannot be
derived. `client_approved` is one boolean about `CLIENT_APPROVER`; Astra
approval is not a function of it. A screen wanting both approvals has to be sent
both.

**Why not drop `client_approved` now that the list is sent.** It carries the
rule — `missing_client_approval` is the one definition of what counts as client
approval, and it is the value `test_the_column_flips_when_approved_by_is_patched`
asserts against. Rebuilding that predicate in the browser from `approved_by`
would be the second definition CLAUDE.md forbids. The list is for *identity*;
the boolean is for the *verdict*.

`RfqDetail.client_approver` already rides on the payload, so the browser still
never spells the client's name.

## 4. One pill component — `web/src/components/primitives.tsx`

```tsx
export function ApprovalPills({ approvers }: { approvers: string[] }): JSX.Element
```

Renders one `<span>` per approver with
``className={`approval-badge approval-badge--${slug(approver)}`}``, where `slug`
lower-cases and hyphenates — the same transformation `ShortlistingStep` already
applies to `effective_prequal`.

**Why a component rather than three call sites.** The candidate card, the
shortlist row and the item screen's vendor table all render the same fact, and
three copies of a `.map` is three places to fix when a fourth approver appears.

**Why the class is a slug rather than a lookup.** An unrecognised approver falls
through to the base `.approval-badge` rule and renders in today's neutral grey.
A lookup table would have to decide what to do about a name it does not know,
and the honest answer — show it, uncoloured — is what the CSS cascade already
gives for free.

### The colours — `web/src/theme.css`

```css
.approval-badge--adnoc {          /* the client's list */
  background: #e8eefb;
  border-color: #c3d3f0;
  color: #274a94;
}

.approval-badge--astra {          /* ours */
  background: var(--accent-soft);
  border-color: #b7dcdf;
  color: var(--accent-ink);
}
```

Astra is the house accent on purpose — our own list in our own colour — and
ADNOC is a blue far enough from it to separate at a glance in a table. Both keep
the base rule's shape, weight and letter-spacing, so only the hue changes.

Two distinct calm hues, and deliberately **not** the pass/deviation/fail palette
the prequalification chips reuse. That palette's comment says why it is shared:
those chips "answer 'can work proceed here'". An approver's name is not a
verdict — a vendor on one list and not the other is not thereby failing
anything — so borrowing green and amber would tell the reader a vendor is in
trouble when the truth is only that two organisations keep different lists.

## 5. The wizard's shortlist column — `web/src/pages/wizard/ShortlistingStep.tsx`

The *Client approval* column becomes *Approvals* and renders `ApprovalPills`.
Its three states are preserved exactly:

| `approved_by` | renders |
|---|---|
| `null` | `Not checked`, muted — no registry row, so no finding either way |
| `[]` | `Not on the {client_approver} list`, warn |
| non-empty | pills, plus the warn line when `client_approved` is `false` |

The last row is the one worth stating: a vendor carrying `["Astra"]` shows an
Astra pill **and** the "not on the ADNOC list" warning. Both are true, and
showing only the pill would let our own qualification read as clearance we do
not have.

`CandidateRow`'s existing badge `.map` is replaced by the same component, so the
candidate list picks up the colours with no other change.

## 6. Shortlisting from the item screen — `web/src/pages/ItemDetail.tsx`

Each row of `AvailableVendorList` gains an *Approvals* cell and a **Shortlist**
button calling `inviteRegisteredBidder(rfqId, { vendor_id })`.

**Resolving which RFQ.** The button needs a target, and the item may have none,
one, or several covering RFQs:

| covering RFQs | behaviour |
|---|---|
| 0 | no button; a line says to raise an RFQ first |
| 1 | invites into it, named on the button's accessible label |
| 2+ | a `<select>` of references above the table chooses the target |

The card therefore takes `covering: Rfq[]` as a prop rather than fetching — the
item screen already has them filtered out of the project payload, and a second
fetch could disagree with the table rendered below it.

**Already-invited vendors show `Invited` instead of a button**, exactly as
`CandidateRow` does. That fact comes from the shortlists §7 already fetches, so
the two features share one read rather than each taking their own: the screen
must not offer to invite somebody it is simultaneously counting as invited two
cards below. While that read is in flight the button renders enabled — a
double invitation is refused by the server and surfaced beside the row, which
is a better failure than a control that is dead on arrival every time the page
loads.

**Nothing is decided in the browser.** No eligibility check, no override
handling: the server owns whether an invitation is legal, and a refusal — the
blocked-bidder case demanding an `override_reason` — renders verbatim beside
the row that caused it, keyed by bidder id. That is the same rule
`ProjectDetail` keeps for a refused delete, and the reason the key is an id and
never a row index.

Inviting re-reads the project, so the summary in §7 and the button's own state
move together.

## 7. The approval summary per covering RFQ — `web/src/pages/ItemDetail.tsx`

*RFQs covering this item* gains a column reading, e.g.:

> `4 invited · 3 ADNOC · 2 Astra · 1 not checked`

An RFQ with nobody invited reads `Nobody invited yet`, not `0 invited · 0 ADNOC`
— a row of zeroes invites the reader to scan for a number that is never
meaningful, and the wizard's Shortlisting step already uses that exact sentence
for the same state. An approver contributing zero is omitted rather than shown
as `0 Astra`.

Counted from each RFQ's own shortlist, so it needs one `fetchRfq` per covering
RFQ — the same read §6 uses to mark a vendor as already invited. **The cost is
stated because it is real**: the project payload carries no
shortlist, so the alternative is a new server route aggregating it, and for the
one-to-three RFQs an item typically has, N reads of an existing endpoint beats a
route that exists only for this column. If an item ever covers enough RFQs for
that to hurt, the fix is the aggregate route, not a cap that under-reports.

A failed read leaves that row's cell reading `—`, never `0 invited`: an
unanswered question and an empty shortlist are different facts, and the whole
change is about not showing findings nobody made.

`not checked` is the `approved_by === null` count, reported rather than folded
into the total, for the reason §3 gives.

## 8. What this change stores: nothing

`PLAN-TEMPLATE.md`'s Rule 2 asks for a two-run mutation matrix over the
collections a phase adds. **This phase adds none**, and the plan says so rather
than fabricating rows for it — the precedent is `test_workflow_persistence.py`,
which already records that only four of the nine standard rows have an analogue
in a subsystem that stores nothing and calls no model.

The single invariant owned here is the converse, and it is assertable in one
read of the document:

> **Store invariant owned:** `workflow.json` gains no key for any value this
> change surfaces. After an RFQ's shortlist is read with `approved_by` present
> in the response, the persisted document contains no `approved_by` under any
> shortlist entry, and no reference to any enquiry document.

Defended by a round-trip test in `tests/test_workflow_persistence.py`: read the
RFQ over HTTP, save, reload, and assert the entry's stored keys are exactly what
they were before. This is the rule that fails if anyone later "optimises" the
derived value by writing it down — the same failure `ShortlistEntry` already has
a test guarding against for `client_approved`.

## 9. The web-search button — `web/src/pages/RfqWizard.tsx`

A disabled button in the header beside the reference:

```tsx
<button type="button" className="btn" disabled>
  Search the web for similar vendors
</button>
<p className="muted">Not built yet.</p>
```

`RfqWizard`'s own docstring argues that a disabled button explains nothing,
which is why its stage control is enabled and surfaces the gate's sentence. That
reasoning does not transfer: there the button *would* work and the question is
whether the gate lets it, so the refusal is information. Here there is nothing
behind it at all, and an enabled control that silently does nothing is worse
than a disabled one that says why. The caption is what keeps the rule — a
disabled control states its reason.

Explicitly out of scope: any search, any provider, any route.

## 10. What deliberately does not change

- **`/rfqs/extract` still stores nothing.** §2's limitation is the price.
- **`ShortlistEntry` gains no field**, and neither does `Bidder`.
- **`client_approved` stays**, for the reason in §3.
- **`ProjectDetail`'s RFQ table gets no summary column.** The report was about
  the item's RFQs; widening it to the project screen is a separate call.
- **No stage, gate or transition moves.** Shortlisting a vendor from the item
  screen does exactly what shortlisting from the wizard does, and the RFQ
  advances only through the gated transition it always did.

## 11. Testing

| file | what it covers |
|---|---|
| `web/src/pages/ProjectDetail.test.tsx` | Both money fields are text inputs. A budget of `1,800,000` is refused with the form's sentence and `createRfq` is **not** called. The existing extraction test's `toHaveValue` becomes a string. The picked file's name appears and survives the input being cleared, a **View** control is offered, and both appear after a failed read with the error still beside them. |
| `web/src/pages/ItemDetail.test.tsx` | Approval pills carry the per-approver class. The Shortlist button is absent with no covering RFQ, invites directly with one, and offers a chooser with two. A server refusal renders against its own row and not against another. The summary column counts invited, each approver, and `not checked` separately; a failed read shows `—`. |
| `web/src/pages/wizard/ShortlistingStep.test.tsx` *(new file)* | The three `approved_by` states, and the load-bearing one: an `["Astra"]` vendor shows the Astra pill **and** the not-on-the-client-list warning. |
| `web/src/pages/RfqWizard.test.tsx` | The button is present, disabled, and captioned. |
| `tests/test_bidder_endpoints.py` | `approved_by` is on every shortlist row; `null` for a hand-typed vendor and for a `vendor_id` pointing at a deleted bidder; `[]` for a registry row with no approvals; and it follows a patched `approved_by` on re-read without the shortlist being rewritten. |
| `tests/test_workflow_persistence.py` | The §8 invariant: no shortlist entry in the saved document carries `approved_by`. |

`web/src/pages/workflow-fixtures.ts` gains `approved_by` on its shortlist
fixture so the types check across every suite that imports it.

**Baselines.** `CLAUDE.md`'s two rows move. The workstation row is re-measured
with `python -m pytest`; the CI row is derived from it by the subtraction that
file documents, never edited independently. None of the new Python tests reads a
fixture directory or a provider key, so the AVL gate stays at eleven — and
because none of them touches the three files carrying `needs_real_avl`, it is
**not** re-measured, which is the condition that file sets. The web suite's
count moves separately and is stated separately.
