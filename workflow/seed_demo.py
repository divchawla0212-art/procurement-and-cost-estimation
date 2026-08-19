"""Demonstration data for `<ROOT>/workflow.json`.

    python -m workflow.seed_demo [--root projects] [--as-of YYYY-MM-DD]
                                 [--avl <ADNOC export.xlsx>] [--force]

Two sources of bidders, and which one is in use changes what may be claimed.

**With `--avl`** the registry is a real ADNOC Approved Vendor List export —
about 1 300 real companies, each carrying the product groups they are actually
listed against and the manufacturers they represent. Everything the export
does not say is left unsaid: no expiry dates, no holds, no ratings. See
`workflow/avl_import.py` for why that matters more than a fuller-looking
screen. The Astra subset laid over it is invented, and says so.

**Without it** the registry is twelve invented companies, listed in
`INVENTED_BIDDER_NAMES` and asserted against the built store, so no real
vendor's prequalification status can arrive in a demo by accident. This is the
path CI takes, since the export is untracked.

The projects, items and RFQs are invented either way. Their disciplines are
real ADNOC product group descriptions, so scope matching resolves to real
vendors against an imported registry instead of never matching anything.

Three further rules shape it.

**It is built through the store's own public methods**, never by assembling a
document. A hand-written document can describe an RFQ that is `Issued` with no
approved shortlist — a state the gates make unreachable — and a demo that shows
one is the product lying to the person being shown it. Going through
`transition` means every stage on show was actually earned.

**Ids are fixed; dates are relative to `as_of`.** A demo gets rehearsed and
reseeded, so the same RFQ has to keep the same identity between builds, and
removal addresses a shortlist entry by id. Dates cannot be fixed the same way:
an invented bidder whose approval "expires in three weeks" would be expiring in
the past by next month, and the screen would demonstrate nothing.

**It refuses to overwrite a store that holds anything**, unless `--force`. A
demo loader that silently replaces real work is a data-loss bug with a friendly
name.
"""
import argparse
import os
import sys
from datetime import date, timedelta

from workflow import persistence
from workflow.avl_import import ADNOC, ASTRA, parse_avl
from workflow.bidders import evaluate
from workflow.models.bidder import Bidder
from workflow.models.rfq import Attachment
from workflow.stages import Stage
from workflow.store import WorkflowStore

# The whole invented cast, in one place, so the "no real company" test can
# assert against a list rather than guess at a heuristic.
INVENTED_BIDDER_NAMES = [
    "Al Munara Switchgear LLC",
    "Northwind Valve Works",
    "Meridian Instrument Co.",
    "Sandstone Piping Industries",
    "Falcon Bay Rotating Equipment",
    "Cedarline Electromechanical",
    "Gulf Crescent Fabricators",
    "Ras Dana Cables & Conductors",
    "Silverdune Process Systems",
    "Khaleej Tank & Vessel Co.",
    "Blue Harbour Marine Services",
    "Orion Gulf Automation",
]

# Real ADNOC product group descriptions, quoted exactly. The demo RFQs use
# these as their discipline so that scope matching against an imported registry
# resolves to real vendors — a made-up discipline like "Electrical" matches
# nothing in the export, and every candidate would read as a scope mismatch.
PG_LV_SWITCHGEAR = "SWITCHGEARS - LV -415V"
PG_BALL_VALVES = 'VALVES - BALL - API 6D - UP TO 12"'
PG_AIR_COMPRESSORS = "COMPRESSORS - INSTRUMENT AIR COMPRESSOR PACKAGE"
PG_PRESSURE_TX = "PRESSURE & DIFFERENTIAL PRESSURE TRANSMITTERS"
PG_MV_CABLE = "CABLES - MV (UP TO 33KV)POWER TRANSMISSION"
PG_STEEL = "STEEL STRUCTURE FABRICATED"
PG_FLANGES = "FLANGES FOR PIPES - CS/AS/SS"
PG_HEAT_EXCHANGERS = "HEAT EXCHANGERS - SHELL & TUBES"

BUYER = "amal.hassan@example.com"
ENGINEER = "lead.engineer@example.com"
PROCUREMENT = "procurement@example.com"

# Curated exception text, keyed by invented bidder id. Only the invented cast
# has blocked bidders worth inviting anyway; an imported registry is entirely
# approved, so nothing here applies to it. `_invite` skips a blocked bidder
# with no entry here rather than inventing a justification — a generated
# override reason is exactly the kind of record this system exists to stop.
INVENTED_OVERRIDES = {
    "bdr_sandstone": (
        "Only fabricator with the 16-inch tie-in spool jig; renewal audit "
        "booked for next month."
    ),
    "bdr_silverdune": (
        "Retained for competitive tension; award is conditional on the HAL-19 "
        "dispute closing."
    ),
}


def invented_bidders(as_of: date) -> list[Bidder]:
    """Twelve fictional companies spanning every prequalification state.

    The two date-driven ones are the reason `as_of` exists: Sandstone has
    lapsed and Northwind lapses inside the caution window, whenever this runs.
    Their trade categories are real ADNOC product group descriptions so that
    the same demo RFQs work against either registry.
    """
    spec = [
        ("bdr_almunara", "Al Munara Switchgear LLC", "United Arab Emirates",
         [PG_LV_SWITCHGEAR], [ADNOC, ASTRA], "Approved", as_of + timedelta(days=595),
         False, None, "AED 50–100m", 4.4, 7,
         "Consistent on LV frames; slipped twice on documentation."),
        ("bdr_northwind", "Northwind Valve Works", "Oman",
         [PG_BALL_VALVES], [ADNOC, ASTRA], "Approved", as_of + timedelta(days=21),
         False, None, "AED 100–250m", 4.1, 5,
         "Prequalification renewal submitted, awaiting HSE audit."),
        ("bdr_meridian", "Meridian Instrument Co.", "United Arab Emirates",
         [PG_PRESSURE_TX], [ADNOC, ASTRA], "Approved", as_of + timedelta(days=410),
         False, None, "AED 10–50m", 4.6, 9, None),
        ("bdr_sandstone", "Sandstone Piping Industries", "Saudi Arabia",
         [PG_FLANGES, PG_STEEL, PG_BALL_VALVES], [ADNOC], "Approved",
         as_of - timedelta(days=96), False, None, "AED 250m+", 3.8, 4,
         "Approval lapsed; renewal not yet submitted."),
        ("bdr_falconbay", "Falcon Bay Rotating Equipment", "United Arab Emirates",
         [PG_AIR_COMPRESSORS], [ADNOC, ASTRA], "Approved", as_of + timedelta(days=730),
         False, None, "AED 100–250m", 4.3, 6, None),
        ("bdr_cedarline", "Cedarline Electromechanical", "Qatar",
         [PG_LV_SWITCHGEAR], [ADNOC], "Under review", None,
         False, None, "AED 10–50m", None, 0,
         "First submission; technical questionnaire under review."),
        ("bdr_gulfcrescent", "Gulf Crescent Fabricators", "United Arab Emirates",
         [PG_STEEL], [ADNOC], "Suspended", as_of + timedelta(days=300),
         False, None, "AED 50–100m", 2.9, 3,
         "Suspended pending closure of two NCRs raised on JAT-04."),
        ("bdr_rasdana", "Ras Dana Cables & Conductors", "United Arab Emirates",
         [PG_MV_CABLE], [ADNOC, ASTRA], "Approved", as_of + timedelta(days=520),
         False, None, "AED 50–100m", 4.0, 8, None),
        ("bdr_silverdune", "Silverdune Process Systems", "Bahrain",
         [PG_AIR_COMPRESSORS, PG_HEAT_EXCHANGERS], [ADNOC], "Approved",
         as_of + timedelta(days=480), True,
         "Unresolved commercial dispute on HAL-19", "AED 100–250m", 3.5, 2,
         "On hold at the client's request until the dispute closes."),
        ("bdr_khaleej", "Khaleej Tank & Vessel Co.", "Kuwait",
         [PG_HEAT_EXCHANGERS, PG_STEEL, PG_BALL_VALVES], [ADNOC, ASTRA], "Approved",
         as_of + timedelta(days=365), False, None, "AED 50–100m", 4.2, 5, None),
        ("bdr_blueharbour", "Blue Harbour Marine Services", "United Arab Emirates",
         [], [ADNOC], "Not qualified", None,
         False, None, "AED 10–50m", None, 0,
         "Declined at prequalification: no comparable scope delivered."),
        ("bdr_oriongulf", "Orion Gulf Automation", "United Arab Emirates",
         [PG_PRESSURE_TX, PG_MV_CABLE], [ADNOC, ASTRA], "Approved",
         as_of + timedelta(days=640), False, None, "AED 10–50m", 4.5, 3, None),
    ]
    return [
        Bidder(
            id=bid_id, name=name, country=country, trade_categories=cats,
            approved_by=approvals, prequal_status=status, prequal_expires_on=expires,
            on_hold=on_hold, hold_reason=hold_reason, turnover_band=band,
            performance_rating=rating, past_awards=awards, notes=notes,
        )
        for (bid_id, name, country, cats, approvals, status, expires, on_hold,
             hold_reason, band, rating, awards, notes) in spec
    ]


def _load_registry(store: WorkflowStore, bidders: list[Bidder]) -> None:
    """Through `create_bidder`, not by writing `store._bidders` — the store is
    what validates, and a seed that bypassed it could plant a record no route
    could ever have created."""
    for bidder in bidders:
        store.create_bidder(bidder_id=bidder.id, **bidder.model_dump(exclude={"id"}))


def _projects_and_items(store: WorkflowStore, as_of: date) -> None:
    store.create_project(
        project_id="prj_haliba", name="Haliba Field Development", code="HAL",
        client="Al Dhafra Petroleum", location="Haliba field, Al Dhafra, UAE",
        live_period_start=as_of - timedelta(days=220),
        live_period_end=as_of + timedelta(days=900),
    )
    store.create_project(
        project_id="prj_ruwais", name="Ruwais Utilities Upgrade", code="RUU",
        client="Emirates Downstream", location="Ruwais Industrial City, UAE",
        live_period_start=as_of - timedelta(days=90),
        live_period_end=as_of + timedelta(days=640),
    )
    store.create_project(
        project_id="prj_jebelali", name="Jebel Ali Terminal Expansion", code="JAT",
        client="Gulf Ports Authority", location="Jebel Ali, Dubai, UAE",
        live_period_start=as_of - timedelta(days=340),
        live_period_end=as_of + timedelta(days=420),
    )

    items = [
        ("itm_hal_switchgear", "prj_haliba", "LV switchgear",
         "LV switchboards and motor control centres, 415 V", 6, "ea",
         PG_LV_SWITCHGEAR, 4_100_000, 210, False),
        ("itm_hal_valves", "prj_haliba", "Ball valves",
         "Wellhead tie-in ball valves, 6\" to 12\", API 6D", 148, "ea",
         PG_BALL_VALVES, 9_400_000, 260, True),
        ("itm_hal_flanges", "prj_haliba", "Flanges",
         "Carbon steel flanges and fittings for the tie-in spools", 1, "lot",
         PG_FLANGES, 12_700_000, 300, False),
        ("itm_hal_instruments", "prj_haliba", "Pressure transmitters",
         "Wellhead pressure and differential pressure transmitters", 96, "ea",
         PG_PRESSURE_TX, 2_300_000, 330, False),
        ("itm_ruu_compressors", "prj_ruwais", "Instrument air packages",
         "Instrument air compressor packages, 2 x 100 %", 2, "ea",
         PG_AIR_COMPRESSORS, 15_800_000, 400, True),
        ("itm_ruu_instruments", "prj_ruwais", "Pressure transmitters",
         "Field transmitters for the utilities battery limit", 1, "lot",
         PG_PRESSURE_TX, 3_600_000, 250, False),
        ("itm_ruu_exchangers", "prj_ruwais", "Heat exchangers",
         "Shell and tube exchangers for the cooling water loop", 4, "ea",
         PG_HEAT_EXCHANGERS, 5_200_000, 470, False),
        ("itm_ruu_cable", "prj_ruwais", "MV cable",
         "11 kV cable for the new substation feeders", 2_400, "m",
         PG_MV_CABLE, 1_150_000, 190, False),
        ("itm_jat_hvcable", "prj_jebelali", "MV cable",
         "11 kV three-core XLPE cable, terminal ring main", 4_800, "m",
         PG_MV_CABLE, 6_900_000, 120, False),
        ("itm_jat_steel", "prj_jebelali", "Structural steel",
         "Pipe rack and access platform steelwork", 640, "t",
         PG_STEEL, 8_800_000, 380, True),
        ("itm_jat_exchangers", "prj_jebelali", "Heat exchangers",
         "Bunkering line heat exchangers", 2, "ea",
         PG_HEAT_EXCHANGERS, 21_500_000, 150, True),
        # Deliberately dated past the project's live period, so the advisory
        # live-period caution has something to show. It is a warning, not a
        # refusal — which is the point being demonstrated.
        ("itm_jat_late_valves", "prj_jebelali", "Ball valves",
         "Isolation valves for the berth 4 tie-in", 40, "ea",
         PG_BALL_VALVES, 3_100_000, 520, False),
    ]
    for (item_id, project_id, item_type, description, qty, uom, discipline,
         value, days_out, long_lead) in items:
        store.create_item(
            item_id=item_id,
            project_id=project_id,
            item_type=item_type,
            description=description,
            qty=qty,
            uom=uom,
            discipline=discipline,
            estimated_value_aed=value,
            required_on_site=as_of + timedelta(days=days_out),
            is_long_lead=long_lead,
        )


def _freeze(store: WorkflowStore, rfq_id: str, revision: str, basis: str,
            attachments: list[tuple[str, str, str]]) -> None:
    store.set_technical_package(
        rfq_id,
        revision=revision,
        basis_of_design=basis,
        attachments=[Attachment(doc_code=c, title=t, revision=r) for c, t, r in attachments],
    )
    store.freeze_package(rfq_id, by=ENGINEER)


def _shortlistable(bidders: list[Bidder], product_group: str, count: int) -> list[Bidder]:
    """Who this demo invites to a package, deterministically.

    Astra-approved first, then alphabetically. That is both a defensible
    procurement habit — you draw from your own list before the client's wider
    one — and, more practically here, the only way a rehearsed demo picks the
    same three companies twice out of a hundred that match.
    """
    matching = [b for b in bidders if product_group in b.trade_categories]
    matching.sort(key=lambda b: (ASTRA not in b.approved_by, b.name.casefold()))
    return matching[:count]


def _invite(store: WorkflowStore, rfq_id: str, picks: list[Bidder],
            as_of: date) -> list[Bidder]:
    """Invite each pick, and return the ones that actually went on.

    A pick with blockers goes on only if `INVENTED_OVERRIDES` has curated text
    for it; otherwise it is skipped. Generating an override reason would be
    manufacturing the exact record — an attributed justification for an
    exception — that this whole feature exists to make deliberate.
    """
    invited = []
    rfq = store.get_rfq(rfq_id)
    for bidder in picks:
        reason = INVENTED_OVERRIDES.get(bidder.id)
        if not evaluate(bidder, rfq, as_of).eligible and not reason:
            continue
        store.add_shortlist_entry(
            rfq_id,
            # Stable, and readable in the document: the RFQ's tail and the
            # vendor number. Removal addresses an entry by id, so a rehearsed
            # "remove this row" has to hit the same row every time.
            entry_id=f"sle_{rfq_id.removeprefix('rfq_')}_{bidder.id.removeprefix('bdr_')}",
            vendor_id=bidder.id,
            as_of=as_of,
            override_by=PROCUREMENT if reason else None,
            override_reason=reason,
        )
        invited.append(bidder)
    return invited


def _vdrl(store: WorkflowStore, rfq_id: str, lines: list[tuple[str, str, str, bool]]) -> None:
    for code, title, doc_type, mandatory in lines:
        store.add_vdrl_line(
            rfq_id, doc_code=code, title=title, doc_type=doc_type, mandatory=mandatory
        )


def build_demo_store(as_of: date, bidders: list[Bidder] | None = None) -> WorkflowStore:
    """The whole demo, built the way a user would have built it.

    `bidders` is the registry to work from — an imported AVL, or the invented
    cast when omitted. Everything downstream picks from whichever it is given,
    so the same six RFQs demonstrate the same six stages either way.

    Order matters in one place: approval is revoked by any edit to the vendor
    set, so every invitation is issued before `approve_shortlist`. That is not
    a quirk of the seed — it is the rule the screen enforces, reproduced here
    because going through the store is what makes the seeded states real.
    """
    roster = invented_bidders(as_of) if bidders is None else bidders
    store = WorkflowStore()
    _load_registry(store, roster)
    _projects_and_items(store, as_of)

    # -- HAL-01: raised and nothing else. A package is attached but nobody has
    # been invited, so the Shortlisting gate is closed on its first clause and
    # the demo has something to *do*. This is the one RFQ with an empty
    # shortlist, and `test_every_demo_rfq_finds_real_vendors_for_its_product_group`
    # counts on that.
    store.create_rfq(
        rfq_id="rfq_hal01", project_id="prj_haliba", item_ids=["itm_hal_switchgear"],
        reference="HAL-RFQ-2026-001", package="LV switchgear",
        discipline=PG_LV_SWITCHGEAR, value_estimate_aed=4_100_000,
    )
    store.set_technical_package(
        "rfq_hal01", revision="Rev. A",
        basis_of_design="Six LV boards, 415 V, form 4b, per HAL-SLD-002.",
        attachments=[],
    )

    # -- HAL-02: shortlisting. Frozen package; vendors invited, approval
    # deliberately not yet given, so the Shortlisting gate is closed on its
    # second clause rather than its first.
    store.create_rfq(
        rfq_id="rfq_hal02", project_id="prj_haliba",
        item_ids=["itm_hal_valves", "itm_hal_flanges"],
        reference="HAL-RFQ-2026-002", package="Wellhead tie-in ball valves",
        discipline=PG_BALL_VALVES, value_estimate_aed=22_100_000,
    )
    _freeze(store, "rfq_hal02", "Rev. C",
            "148 wellhead tie-in ball valves and the associated flange material.",
            [("HAL-PID-014", "Tie-in P&ID", "Rev. C"),
             ("HAL-DS-101", "Valve datasheets", "Rev. B"),
             ("HAL-SPC-007", "Piping material specification", "Rev. D")])
    _invite(store, "rfq_hal02", _shortlistable(roster, PG_BALL_VALVES, 4), as_of)

    # -- RUU-01: issued. Everything the Shortlisting gate wants is in place.
    store.create_rfq(
        rfq_id="rfq_ruu01", project_id="prj_ruwais", item_ids=["itm_ruu_compressors"],
        reference="RUU-RFQ-2026-004", package="Instrument air compressor packages",
        discipline=PG_AIR_COMPRESSORS, value_estimate_aed=15_800_000,
    )
    _freeze(store, "rfq_ruu01", "Rev. B",
            "Two 100 % instrument air packages, oil-free, with dryers.",
            [("RUU-DS-220", "Compressor datasheet", "Rev. B"),
             ("RUU-PID-031", "Instrument air P&ID", "Rev. A")])
    _invite(store, "rfq_ruu01", _shortlistable(roster, PG_AIR_COMPRESSORS, 3), as_of)
    store.approve_shortlist("rfq_ruu01", by=PROCUREMENT)
    store.set_tbe_template(
        "rfq_ruu01",
        items=["Capacity at site conditions", "Specific power",
                  "Dryer dew point", "Noise at 1 m", "Spares for two years"],
        source_rfq_reference="RUU-RFQ-2025-018",
    )
    store.transition("rfq_ruu01", Stage.ISSUED, by=BUYER,
                     reason="Issued to the approved shortlist")
    _vdrl(store, "rfq_ruu01",
          [("GA-001", "General arrangement drawing", "GA Drawing", True),
           ("DS-002", "Filled compressor datasheet", "Datasheet", True),
           ("TP-003", "Factory acceptance test procedure", "Test Procedure", True),
           ("CE-004", "Motor efficiency certificate", "Certificate", False)])
    # One issued addendum, so the demo shows a package that moved and the trail
    # that records how. The frozen Rev. B is superseded rather than edited —
    # `set_technical_package` still refuses it, which is the whole point.
    _ruu01_addendum = store.draft_addendum(
        "rfq_ruu01", addendum_id="add_ruu01_01", revision="Rev. C",
        summary=(
            "Site ambient revised to 50 °C; capacity re-stated at the new "
            "condition."
        ),
        attachments=[
            Attachment(doc_code="RUU-DS-220", title="Compressor datasheet",
                       revision="Rev. C"),
            Attachment(doc_code="RUU-PID-031", title="Instrument air P&ID",
                       revision="Rev. A"),
        ],
        bid_due_date=date(2026, 9, 15),
    )
    store.issue_addendum("rfq_ruu01", _ruu01_addendum.id, by=BUYER)

    # -- RUU-02: clarifications. The technical query round.
    store.create_rfq(
        rfq_id="rfq_ruu02", project_id="prj_ruwais",
        item_ids=["itm_ruu_instruments", "itm_ruu_cable"],
        reference="RUU-RFQ-2026-006", package="Field transmitters and MV cable",
        discipline=PG_PRESSURE_TX, value_estimate_aed=4_750_000,
    )
    _freeze(store, "rfq_ruu02", "Rev. A",
            "Battery-limit field transmitters plus the substation feeder cable.",
            [("RUU-IDX-410", "Instrument index", "Rev. A"),
             ("RUU-IO-411", "IO list", "Rev. A")])
    _invite(store, "rfq_ruu02", _shortlistable(roster, PG_PRESSURE_TX, 3), as_of)
    store.approve_shortlist("rfq_ruu02", by=PROCUREMENT)
    store.set_tbe_template(
        "rfq_ruu02",
        items=["Accuracy class", "Hazardous area certification",
                  "Turndown ratio", "Cyber-security compliance"],
    )
    store.transition("rfq_ruu02", Stage.ISSUED, by=BUYER)
    _vdrl(store, "rfq_ruu02",
          [("IDX-001", "Priced instrument index", "Datasheet", True),
           ("CE-002", "ATEX / IECEx certificates", "Certificate", True)])
    store.transition("rfq_ruu02", Stage.CLARIFICATIONS, by=BUYER,
                     reason="Two technical queries raised on the IO list")
    # The two queries that reason names. Until this phase the sentence was the
    # only evidence they existed; now the register holds them, one answered and
    # circulated, one still open — which is what leaves the exit gate visibly
    # closed on screen rather than only in a test.
    _ruu02_invited = store.shortlist_for("rfq_ruu02")
    _answered = store.raise_query(
        "rfq_ruu02", _ruu02_invited[0].id, query_id="clq_ruu02_01",
        question=(
            "The IO list shows 42 transmitters and the instrument index shows "
            "40. Which governs for pricing?"
        ),
        category="Technical", raised_on=date(2026, 8, 10),
    )
    store.answer_query(
        "rfq_ruu02", _answered.id,
        answer="The instrument index at Rev. A governs. Price 40 transmitters.",
        by=BUYER,
    )
    store.raise_query(
        "rfq_ruu02", _ruu02_invited[min(1, len(_ruu02_invited) - 1)].id,
        query_id="clq_ruu02_02",
        question=(
            "Confirm whether the cyber-security compliance statement is "
            "required at bid stage or at award."
        ),
        category="Technical", raised_on=date(2026, 8, 12),
    )
    # A draft addendum, so the second half of the gate's sentence is visible
    # too. Deliberately left unissued: an issued one here would clear the gate.
    store.draft_addendum(
        "rfq_ruu02", addendum_id="add_ruu02_01", revision="Rev. B",
        summary=(
            "Instrument index corrected to 40 transmitters, matching the answer "
            "to TQ-001."
        ),
        attachments=[
            Attachment(doc_code="RUU-IDX-410", title="Instrument index",
                       revision="Rev. B"),
            Attachment(doc_code="RUU-IO-411", title="IO list", revision="Rev. B"),
        ],
        arising_from_query_ids=[_answered.id],
        bid_due_date=date(2026, 9, 30),
    )

    # -- JAT-01: bids received, not yet opened for evaluation. The gate to
    # Evaluation is closed until bids are selected, which is the next click.
    store.create_rfq(
        rfq_id="rfq_jat01", project_id="prj_jebelali", item_ids=["itm_jat_hvcable"],
        reference="JAT-RFQ-2026-009", package="11 kV ring main cable",
        discipline=PG_MV_CABLE, value_estimate_aed=6_900_000,
    )
    _freeze(store, "rfq_jat01", "Rev. B",
            "4 800 m of 11 kV three-core XLPE, drum lengths per the schedule.",
            [("JAT-CS-501", "Cable schedule", "Rev. B")])
    cable_bidders = _invite(store, "rfq_jat01", _shortlistable(roster, PG_MV_CABLE, 2), as_of)
    store.approve_shortlist("rfq_jat01", by=PROCUREMENT)
    store.set_tbe_template("rfq_jat01", items=["Conductor size", "Drum lengths",
                                                  "Type test certificates"])
    store.transition("rfq_jat01", Stage.ISSUED, by=BUYER)
    _vdrl(store, "rfq_jat01",
          [("TT-001", "Type test certificate", "Certificate", True),
           ("DS-002", "Cable datasheet", "Datasheet", True)])
    store.transition("rfq_jat01", Stage.CLARIFICATIONS, by=BUYER)
    store.transition("rfq_jat01", Stage.BIDS_RECEIVED, by=BUYER,
                     reason="Bids received before the deadline")
    for offset, bidder in enumerate(cable_bidders):
        bid = store.register_bid(
            "rfq_jat01", vendor_name=bidder.name,
            headline_price_aed=6_450_000 + offset * 570_000,
        )
        store.record_vdrl_receipt(bid.id, doc_code="TT-001",
                                  state="received" if offset == 0 else "unreadable",
                                  revision="Rev. 0" if offset == 0 else None)
        if offset == 0:
            store.record_vdrl_receipt(bid.id, doc_code="DS-002", state="received",
                                      revision="Rev. 0")

    # -- JAT-02: under evaluation, with a recorded selection rationale.
    store.create_rfq(
        rfq_id="rfq_jat02", project_id="prj_jebelali",
        item_ids=["itm_jat_steel", "itm_jat_exchangers"],
        reference="JAT-RFQ-2026-011", package="Pipe rack steel and bunkering exchangers",
        discipline=PG_STEEL, value_estimate_aed=30_300_000,
    )
    _freeze(store, "rfq_jat02", "Rev. A",
            "640 t of rack and platform steelwork with the bunkering exchangers.",
            [("JAT-ST-601", "Steel tonnage schedule", "Rev. A"),
             ("JAT-HX-602", "Exchanger datasheets", "Rev. A")])
    steel_bidders = _invite(store, "rfq_jat02", _shortlistable(roster, PG_STEEL, 2), as_of)
    store.approve_shortlist("rfq_jat02", by=PROCUREMENT)
    store.set_tbe_template("rfq_jat02", items=["Plate grade", "Weld procedure",
                                                  "Coating system", "Delivery to site"])
    store.transition("rfq_jat02", Stage.ISSUED, by=BUYER)
    _vdrl(store, "rfq_jat02",
          [("WP-001", "Welding procedure specification", "Test Procedure", True),
           ("CO-002", "Coating system datasheet", "Datasheet", True),
           ("MT-003", "Material test certificates", "Certificate", True)])
    store.transition("rfq_jat02", Stage.CLARIFICATIONS, by=BUYER)
    store.transition("rfq_jat02", Stage.BIDS_RECEIVED, by=BUYER)
    steel_bids = []
    for offset, bidder in enumerate(steel_bidders):
        bid = store.register_bid(
            "rfq_jat02", vendor_name=bidder.name,
            headline_price_aed=28_900_000 + offset * 2_850_000,
        )
        steel_bids.append(bid)
        for code in ("WP-001", "CO-002", "MT-003"):
            # The second bidder is short one returnable, so the VDRL tally has
            # a gap to show rather than two identical green rows.
            if offset and code == "CO-002":
                store.record_vdrl_receipt(bid.id, doc_code=code, state="not_received")
            else:
                store.record_vdrl_receipt(bid.id, doc_code=code, state="received",
                                          revision="Rev. 0")
    store.select_bids(
        "rfq_jat02", [b.id for b in steel_bids], by=PROCUREMENT,
        rationale="Both bids are technically responsive; taking both to TBE so "
                  "the coating deviation can be priced rather than assumed.",
    )
    store.transition("rfq_jat02", Stage.EVALUATION, by=BUYER,
                     reason="Both bids taken forward to TBE")

    return store


def _holds_anything(store: WorkflowStore) -> bool:
    return bool(
        store.list_projects() or store.list_bidders() or store.list_rfqs()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m workflow.seed_demo",
        description="Write demonstration data to <ROOT>/workflow.json.",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects"),
        help="the store root holding workflow.json (default: %(default)s)",
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=None,
        help="the date the demo is relative to (default: today)",
    )
    parser.add_argument(
        "--avl", default=None, metavar="XLSX",
        help="an ADNOC Approved Vendor List export to use as the registry. "
             "Without it, twelve invented companies are used instead.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite a store that already holds data",
    )
    args = parser.parse_args(argv)

    existing = persistence.load(args.root)
    if _holds_anything(existing) and not args.force:
        print(
            f"{persistence.workflow_path(args.root)} already holds "
            f"{len(existing.list_projects())} project(s), "
            f"{len(existing.list_bidders())} bidder(s) and "
            f"{len(existing.list_rfqs())} RFQ(s). Nothing was written. "
            f"Pass --force to replace them.",
            file=sys.stderr,
        )
        return 1

    bidders = None
    if args.avl:
        if not os.path.exists(args.avl):
            print(f"No such Approved Vendor List: {args.avl}", file=sys.stderr)
            return 1
        bidders = parse_avl(args.avl, astra_subset=True)

    store = build_demo_store(args.as_of or date.today(), bidders=bidders)
    registry = store.list_bidders()
    astra = sum(1 for b in registry if ASTRA in b.approved_by)
    source = f"the AVL at {args.avl}" if args.avl else "the invented cast"
    print(
        f"Wrote {len(registry)} bidders from {source} "
        f"({astra} of them also on the Astra list), "
        f"{len(store.list_projects())} projects and "
        f"{len(store.list_rfqs())} RFQs to {persistence.workflow_path(args.root)}."
    )
    persistence.save(args.root, store)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
