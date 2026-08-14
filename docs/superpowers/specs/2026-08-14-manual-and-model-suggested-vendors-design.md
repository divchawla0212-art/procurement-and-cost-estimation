# Adding vendors by hand and by model, and opening an RFQ from an item — design

**Date:** 2026-08-14
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-14-item-vendor-nominations-design.md`](2026-08-14-item-vendor-nominations-design.md)

---

## The problem

Three reports, two of which share a shape.

1. **A company that is on nobody's list cannot be recorded against an item.**
   The item now carries the client's list and Astra's, both uploaded. A buyer
   who knows a supplier that is on neither has nowhere to put them. The wizard
   has `UnregisteredVendorRow` for this, but it writes to an RFQ's *shortlist* —
   which needs an RFQ to exist, and answers "who are we sending this to" rather
   than "who could do this work".

2. **There is no way to look for vendors the product has never heard of.** The
   RFQ wizard carries a disabled *Search the web for similar vendors* button and
   the item screen a disabled *From the internet* chip. Both are placeholders
   this design makes real.

3. **An RFQ covering an item cannot be opened from the item.** The *RFQs
   covering this item* table is read-only text; getting into one means going
   back to the project, or to the RFQ roster, and finding it again.

## What this builds

- Two more sources for an item's vendor list: **Manual** and **Suggested**.
- An LLM-backed vendor suggestion route, storing nothing until a human adds it.
- A `›` control on each covering-RFQ row.

**The registry is untouched**, as it was last phase. Nothing here creates a
bidder or grants an approval.

---

## 1. The honesty problem, stated first

**These providers cannot browse.** `shared/llm/` wraps Anthropic, OpenAI,
Gemini and Bedrock through one `classify_structure(prompt, output_schema,
context_text)` call. There is no crawler, no search index, and no provider
web-search tool wired up. Asking for "vendors from the internet" therefore
returns **what the model recalls from training**, which is a different thing
from a search result in three ways that matter here:

- it has no date — a company may have been acquired, renamed or dissolved;
- it has no source — there is no link to check it against;
- it can be confidently wrong in the specific way that matters most, by
  inventing a plausible company name.

So the feature is built and labelled as **"Suggested vendors"**, not "found on
the internet", and every row says it is unverified. Calling it a web search on
screen would put a synthesised fact where a recorded one is expected — the rule
this repository states for the AVL import, the mock rounds and the RFQ
extractor, all pointing the same way.

The `Web` source name is therefore **`Suggested`**, and the disabled *From the
internet* chip is replaced rather than enabled.

**The upgrade path is named, not built:** Anthropic's API offers a server-side
web-search tool. Adopting it means a client method beyond `classify_structure`,
a citation field on the result, and a way to record which URL a vendor came
from. That is a phase of its own, and when it lands the label can change to
match. Until then the label matches what the code does.

## 2. Two more sources — `workflow/models/project.py`

```python
VendorListSource = Literal["Client", "Astra", "Manual", "Suggested"]
```

`ItemVendorEntry` gains nothing. A hand-added company and a model-suggested one
are already exactly what the record describes: a vendor name, no registry link
(`vendor_id` is `None`), and the item-relevant trade categories. The state that
says "the registry does not hold them" is the one they arrive in.

### The behavioural fork this creates

The uploaded sources **replace wholesale** — a second upload is a correction.
The two new ones **accumulate** — adding a company one at a time must not wipe
the one added before it.

That is a real difference and it gets two store methods, not one with a flag:

| method | used by | semantics |
|---|---|---|
| `set_item_vendor_list(item_id, source, entries)` | Client, Astra | replaces that source |
| `add_item_vendor_entry(item_id, entry)` | Manual, Suggested | appends one |
| `remove_item_vendor_entry(item_id, entry_id)` | Manual, Suggested | removes one by **id** |

**`set_` refuses `Manual` and `Suggested`, and `add_`/`remove_` refuse `Client`
and `Astra`.** Not defensive noise: an upload that replaced a buyer's
hand-added companies would destroy work with no undo, and a hand-add that
appended to an uploaded list would leave the export no longer matching its
source document. The refusals name the source and the reason.

Removal is by **id**, never by name or position — the same rule the shortlist
keeps, and here it matters because two suppliers can share a trading name.

## 3. Adding by hand

```
POST /projects/{project_id}/items/{item_id}/vendors        {vendor_name, trade_categories?, note?}
DELETE /projects/{project_id}/items/{item_id}/vendors/{entry_id}
```

- `vendor_name` is required and trimmed; blank is refused. A vendor with no
  name cannot be invited later, and storing one would put a row on screen that
  no action can be taken on.
- `vendor_id` is always `None`. **The route never looks the name up in the
  registry**, deliberately: a fuzzy match would silently attach a real
  company's approvals to whatever somebody typed, and this repository has twice
  recorded substring matching as a shipped defect. If the vendor is in the
  registry, the available-vendor card is where to find them.
- `trade_categories` defaults to the item's discipline expansion, so a
  hand-added vendor sits in the same shape as an uploaded one.
- `source_document` records `"added by hand"` and `uploaded_by` the session
  user, so every row on this screen is attributable however it arrived.

**Deleting is allowed only for `Manual` and `Suggested`.** An uploaded row is
part of a document; correcting it means re-uploading the corrected export.

## 4. Suggesting by model

```
POST /projects/{project_id}/items/{item_id}/vendor-suggestions
```

**Stores nothing.** It returns candidates; adding one is a second, attributed
call to §3's route. Same shape as `/rfqs/extract`, and for the same reason: a
suggestion the reader rejects should leave nothing behind, and one they accept
should be recorded as *their* act, not the model's.

### The module — `workflow/vendor_suggestions.py`

```python
class SuggestedVendor(BaseModel):
    name: str
    country: str | None = None
    supplies: str | None = None      # what the model says they make
    basis: str | None = None         # why the model believes it

class SuggestedVendors(BaseModel):
    vendors: list[SuggestedVendor] = []   # defaulted — see below


def suggest(client, *, discipline: str, description: str, exclude: Iterable[str]) -> list[SuggestedVendor]
```

- **The `[]` default is load-bearing.** `CLAUDE.md` records `I5` — "the model
  returns a response omitting an optional array" read as a failure rather than
  as no results. A model asked for vendors in an obscure discipline may
  legitimately name none.
- **The model reads; code decides.** It is never asked whether a vendor is
  eligible, approved, or should be invited — only which companies supply this
  kind of equipment. Every judgement stays in Python, the rule the extractors
  already keep.
- **`exclude` is the names already on this item's lists**, passed so the model
  is not asked to re-suggest them. Filtered again in Python on the way out,
  because a prompt instruction is a request and not a guarantee.
- **A failed call is reported, never silently empty.** An outage and "no such
  vendors exist" must not look the same — the same distinction the covering-RFQ
  summary keeps between `—` and `Nobody invited yet`.

### The prompt — `shared/llm/prompts/vendor_search_v1.txt`

Versioned in the prompts directory like every other, so a change to it is a
reviewable diff rather than an edited string literal. It states plainly that a
company the model is not confident actually exists must be omitted, and that no
approval, qualification or ranking is to be asserted.

### What a suggestion may never carry

- **No `approved_by`, ever.** A model-named company claiming ADNOC approval is
  the exact failure `test_no_invented_vendor_claims_the_clients_approval`
  already guards for the mock rounds, arriving through a new door.
- **No `vendor_id`.** It is not in the registry; that is why it was suggested.
- **No prequalification.** The sheet does not say it and neither does the model.

## 5. The screens

**A card per source becomes four**, reusing the component built last phase. The
Manual and Suggested cards carry a remove control per row; the uploaded two do
not.

**The Suggested card leads with what it is:** *Suggested by the model from its
training data. Not a web search — verify each company before inviting them.*
Every row shows the model's `basis` where it gave one, so the reader can judge.
Rows are added to the item's list one at a time, by an explicit **Add** —
never in bulk, because accepting twenty unverified companies in one click is
precisely the act that needs friction.

**The disabled *From the internet* chip is removed** and the RFQ wizard's
disabled *Search the web for similar vendors* button is repointed at this
feature, since it now exists.

## 6. The `›` control

Each row of *RFQs covering this item* gains a control opening that RFQ.
`ItemDetail` takes an `onOpenRfq(rfqId)` prop, and `ItemRoute` — which already
holds `useNavigate` — passes `(id) => navigate(`/rfqs/${id}`)`. No new route:
`/rfqs/:rfqId` already exists and already chooses the wizard or the read-only
screen by stage.

Its accessible name is `Open {reference}`, not `›`, for the reason the vendor
row button was shortened last phase: the glyph is the visible label and the
name is what a screen reader and a test read.

## 7. What deliberately does not change

- **The registry.** No route here creates a bidder or grants an approval.
- **The shortlist stays RFQ-scoped.** These are item vendor lists; inviting is
  still `POST /rfqs/{id}/shortlist` with its own guards.
- **Uploaded lists stay immutable** except by re-upload. §2.
- **`/rfqs/extract`** and every existing route.

## 8. Testing

| file | what it covers |
|---|---|
| `tests/test_item_vendor_lists.py` | `add_`/`remove_` append and remove by id; `set_` refuses Manual/Suggested and `add_` refuses Client/Astra, each naming the source. |
| `tests/test_vendor_suggestions.py` *(new)* | The model is never asked for a verdict (the prompt carries no eligibility question); a response omitting `vendors` reads as none rather than as a failure; `exclude` is filtered in Python and not merely requested; a raised provider error surfaces rather than returning `[]`. Uses `MockLLMClient` — no key, so it runs in CI. |
| `tests/test_item_vendor_endpoints.py` | A blank name is refused; a hand-added vendor never gets a `vendor_id` even when a registry row has that exact name; deleting a `Client` row is refused; the suggestion route stores nothing. |
| `web/src/pages/ItemDetail.test.tsx` | Four cards; remove only on the two editable ones; the Suggested caption; adding one suggestion at a time; the `›` control's accessible name and target. |

**The suggestion route needs no `needs_real_avl`-style gate** — `MockLLMClient`
means no provider key, which is the rule `CLAUDE.md` sets for CI.

## 9. Open question for the reader

**Should a suggested vendor, once added, be visually distinguishable forever?**
This design says yes — `source` is stored, so a Suggested row stays labelled
even after somebody accepts it. The alternative is promoting an accepted
suggestion to `Manual`, on the grounds that a human vouched for it. That reads
as tidier and loses the fact that the name originated with a model, which is
the one thing a later reader would most want to know. Stated here rather than
decided silently.
