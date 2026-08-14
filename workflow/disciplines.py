"""The disciplines an item can be scoped to, and the product groups behind them.

An item's discipline used to be free text, and the vendor list it was matched
against is a controlled vocabulary of 1 141 product group descriptions quoted
from the client's Approved Vendor List. Typed text never matched — `Electrical`
is not a product group, and neither is `1` — so every item read as though no
approved vendor covered it.

This module is the join. Each discipline is a **family**: a short name a buyer
would use, and the exact product group descriptions from the export that belong
to it. `client_approved` expands the family and matches a vendor registered for
any one of its groups.

Three rules, all of which exist to stop this file drifting from the export:

**Every string here is quoted from the sheet, exactly.** Including the truncated
ones — `…(ESP)-OIL LIFTING HAR` is 60 characters because that column is, and
"correcting" it to the full phrase would stop it matching the very rows it names.

**Accessories are not the thing itself.** Cable trays, glands, lugs and
jointing kits are their own product groups and are deliberately outside
`Cables`, as AVR/excitation equipment is outside `Generators`. An RFQ for cable
that returned tray fabricators would be a worse answer than a short one. When
someone wants those, they become their own discipline rather than being folded
in here.

**Two, on purpose, with more to come.** This is not the export's full
vocabulary; it is the part the product has been asked to cover so far. Adding a
discipline is adding an entry here — no route, screen or test structure
changes.
"""
from collections.abc import Iterable


def fold(label: str) -> str:
    """The one definition of how a product group label is matched.

    Case-folded, and **every run of whitespace collapsed to one space** — not
    merely stripped at the ends. The client's exports are inconsistent about
    this: the same group appears as `CABLES - LV POWER DISTRIBUTION` in the
    full list and `CABLES - LV  POWER DISTRIBUTION`, with two spaces, in a
    subset exported from the same system. A rule that only stripped the ends
    would treat those as different trades and split a discipline in half.

    Every comparison of a label goes through this — `bidders._registered_for`,
    `covering` below, and the key columns `bidder_db` writes and queries. It is
    one function because the failure when two of them disagree is silent: a
    vendor invisible to the very filter its own row satisfies.
    """
    return " ".join(label.split()).casefold()

# Quoted from `Product Group Description` in the ADNOC Approved Vendor List
# export. Verified against the imported registry, not typed from the PDF: every
# one of these matches at least one bidder today.
_CABLES = (
    "CABLES - FIBER OPTICS",
    "CABLES - FIRE RESISTANT",
    "CABLES - FOR INSTRUMENTS & CONTROL",
    "CABLES - FOR SUB SEA POWER TRANSMISSION",
    "CABLES - FOR TELECOMMUNICATION OTHER THAN FIBER OPTIC",
    "CABLES - HV- POWER TRANSMISSION -132KV",
    "CABLES - LV POWER DISTRIBUTION",
    "CABLES - MV (UP TO 33KV)POWER TRANSMISSION",
    "CABLES FOR DOWNHOLE ELECTRIC SUBMERSIBLE PUMPS (ESP)",
    # Truncated in the export at 60 characters. Left exactly as the sheet has
    # it — see the module docstring.
    "CABLES FOR ELECTRIC SUBMERSIBLE PUMPS (ESP)-OIL LIFTING HAR",
    "UMBILICAL CABLE",
)

_GENERATORS = (
    "GENERATOR -SOLAR POWER /SOLAR PANELS",
    "GENERATOR POWER-DIESEL ENGINE DRIVEN",
    "GENERATOR POWER-GAS TURBINE DRIVEN",
    "GENERATOR POWER-OTHERS",
    "GENERATOR POWER-STEAM TURBINE DRIVEN",
    "GENERATOR SET (GEN SET) - PORTABLE TYPE",
    "GENERATORS (ALTERNATORS) - FOR ELECTRIC POWER GENERATION",
    "GENERATORS - THERMO ELECTRIC TYPE",
)

DISCIPLINES: dict[str, tuple[str, ...]] = {
    "Cables": _CABLES,
    "Generators": _GENERATORS,
}


def names() -> list[str]:
    """The disciplines an item may be scoped to, in the order they are offered."""
    return list(DISCIPLINES)


def _key(discipline: str) -> str | None:
    """The canonical name for `discipline`, or None when it is not one of ours.

    Case- and padding-insensitive, because the value arrives from a stored item
    or a query string and neither is guaranteed to have kept the capitalisation
    the picker offered.
    """
    wanted = fold(discipline or "")
    return next((n for n in DISCIPLINES if fold(n) == wanted), None)


def is_known(discipline: str | None) -> bool:
    return discipline is not None and _key(discipline) is not None


def product_groups(discipline: str) -> tuple[str, ...]:
    """The export's product groups for `discipline`.

    An unknown discipline resolves to itself, so an item scoped directly to a
    product group description — which is what the export itself speaks — still
    matches. That is the fallback, not the main path: it means adding a
    discipline here is never the only way to scope something.
    """
    name = _key(discipline)
    return DISCIPLINES[name] if name else (discipline,)


def covering(discipline: str, categories: Iterable[str]) -> bool:
    """Whether any of `categories` is in this discipline's family.

    Whole-string through `fold` — the same rule `bidders._registered_for`
    applies, and for the same reason: substring matching would let "CABLES -
    FIBER OPTICS" answer a request for cable accessories.
    """
    wanted = {fold(g) for g in product_groups(discipline)}
    return any(fold(c) in wanted for c in categories)
