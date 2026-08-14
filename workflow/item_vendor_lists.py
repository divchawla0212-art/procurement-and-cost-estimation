"""Narrowing an uploaded Approved Vendor List to one item.

Both uploads — the client's Approved Vendor List and Astra's subset of it — are
the same export format, so nothing here parses: `avl_import.parse_avl` reads
them and hands back `Bidder` objects keyed `bdr_<vendor number>`.

What this module does is the part that makes the result *item*-specific. The
client export runs to 1 346 vendors; an item is one line of equipment. Attaching
the whole register to it would make "this item's vendor list" mean nothing.

Pure — no store, no I/O, no clock. The registry's ids, the timestamp and the
uploader are passed in, the same shape `workflow/bidders.py` is written in and
for the same reason: the rule can then be tested without building a store.
"""
from collections.abc import Container, Iterable
from datetime import datetime

from workflow import disciplines
from workflow.models.bidder import Bidder
from workflow.models.project import ItemVendorEntry, VendorListSource


def entries_for(
    bidders: Iterable[Bidder],
    *,
    item_id: str,
    discipline: str,
    source: VendorListSource,
    known_ids: Container[str],
    uploaded_by: str,
    uploaded_at: datetime,
    source_document: str,
) -> list[ItemVendorEntry]:
    """The item's list: the upload, narrowed to what this item is scoped to.

    `discipline` is expanded through `workflow.disciplines` first, exactly as
    `/bidders/available` expands it, so "Cables" reaches the eleven cable
    product groups the sheet actually names rather than looking for a group
    called "Cables" and finding none.

    Comparison is **whole-string through `fold`**, the rule `disciplines.covering`
    already applies. Folding matters here in particular: the client's own
    exports disagree with themselves about internal whitespace — the full list
    spells it `CABLES - LV POWER DISTRIBUTION` and the Astra subset uses two
    spaces — so an unfolded comparison would silently drop every vendor from one
    of the two files. Substring matching is refused for the reason this
    repository has recorded twice: a plausible near-miss puts vendors on an
    item's list who do not do the work, and looks identical on screen to a list
    that matches.

    A discipline that expands to nothing yields **no entries**, never the whole
    upload. Falling back would attach a 1 346-vendor export to a single line of
    equipment, which is the opposite of what this list is for — and items
    predating the discipline vocabulary carry free text like `"1"`, so this is a
    live path rather than a transitional one. The caller reports the count so an
    empty result reads as a narrowing that found nobody, not as a failure.

    `known_ids` decides which entries link to the registry. It is passed in
    rather than looked up so this module stays pure; a vendor the registry does
    not hold is **kept and marked**, never created — the registry arrives whole
    through its own import, and a name in a spreadsheet is not evidence it
    belongs there.
    """
    wanted = {disciplines.fold(group) for group in disciplines.product_groups(discipline)}

    entries: list[ItemVendorEntry] = []
    for bidder in bidders:
        # Only the groups this item is scoped to. A cable supplier who also
        # sells valves belongs on a cable item's list *for cables*, and the
        # valves would be noise on that screen.
        relevant = sorted(
            group for group in bidder.trade_categories if disciplines.fold(group) in wanted
        )
        if not relevant:
            continue
        entries.append(
            ItemVendorEntry(
                item_id=item_id,
                source=source,
                vendor_id=bidder.id if bidder.id in known_ids else None,
                vendor_name=bidder.name,
                trade_categories=relevant,
                uploaded_by=uploaded_by,
                uploaded_at=uploaded_at,
                source_document=source_document,
            )
        )
    return entries
