"""Demonstration data for `<ROOT>/workflow.json`.

    python -m workflow.seed_demo [--root projects] [--as-of YYYY-MM-DD] [--force]

Everything here is invented. No real company, client or project is named, and
`INVENTED_BIDDER_NAMES` is asserted against the built store so a real vendor's
prequalification status cannot arrive in a demo by accident.

Three rules shape it.

**It is built through the store's own public methods**, never by assembling a
document. A hand-written document can describe an RFQ that is `Issued` with no
approved shortlist — a state the gates make unreachable — and a demo that shows
one is the product lying to the person being shown it. Going through
`transition` means every stage on show was actually earned.

**Ids are fixed; dates are relative to `as_of`.** A demo gets rehearsed and
reseeded, so the same RFQ has to keep the same identity between builds, and
removal addresses a shortlist entry by id. Dates cannot be fixed the same way:
a bidder whose approval "expires in three weeks" would be expiring in the past
by next month, and the screen would demonstrate nothing.

**It refuses to overwrite a store that holds anything**, unless `--force`. A
demo loader that silently replaces real work is a data-loss bug with a friendly
name.
"""
import argparse
import os
import sys
from datetime import date, timedelta

from workflow import persistence
from workflow.stages import Stage
from workflow.store import WorkflowStore

# The whole cast, in one place, so the "no real company" test can assert
# against a list rather than guess at a heuristic.
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

BUYER = "amal.hassan@example.com"
ENGINEER = "lead.engineer@example.com"
PROCUREMENT = "procurement@example.com"


def _bidders(store: WorkflowStore, as_of: date) -> dict[str, str]:
    """The registry, spanning every prequalification state the screen renders.

    The two date-driven ones are the reason `as_of` exists: `sandstone` has
    lapsed and `northwind` lapses inside the caution window, whenever this is
    run.
    """
    spec = [
        ("bdr_almunara", "Al Munara Switchgear LLC", "United Arab Emirates",
         ["Electrical", "LV switchgear"], "Approved", as_of + timedelta(days=595),
         False, None, "AED 50–100m", 4.4, 7,
         "Consistent on LV frames; slipped twice on documentation."),
        ("bdr_northwind", "Northwind Valve Works", "Oman",
         ["Valves", "Piping"], "Approved", as_of + timedelta(days=21),
         False, None, "AED 100–250m", 4.1, 5,
         "Prequalification renewal submitted, awaiting HSE audit."),
        ("bdr_meridian", "Meridian Instrument Co.", "United Arab Emirates",
         ["Instrumentation"], "Approved", as_of + timedelta(days=410),
         False, None, "AED 10–50m", 4.6, 9, None),
        ("bdr_sandstone", "Sandstone Piping Industries", "Saudi Arabia",
         ["Piping", "Structural steel"], "Approved", as_of - timedelta(days=96),
         False, None, "AED 250m+", 3.8, 4,
         "Approval lapsed; renewal not yet submitted."),
        ("bdr_falconbay", "Falcon Bay Rotating Equipment", "United Arab Emirates",
         ["Rotating equipment"], "Approved", as_of + timedelta(days=730),
         False, None, "AED 100–250m", 4.3, 6, None),
        ("bdr_cedarline", "Cedarline Electromechanical", "Qatar",
         ["Electrical", "HVAC"], "Under review", None,
         False, None, "AED 10–50m", None, 0,
         "First submission; technical questionnaire under review."),
        ("bdr_gulfcrescent", "Gulf Crescent Fabricators", "United Arab Emirates",
         ["Structural steel"], "Suspended", as_of + timedelta(days=300),
         False, None, "AED 50–100m", 2.9, 3,
         "Suspended pending closure of two NCRs raised on JAT-04."),
        ("bdr_rasdana", "Ras Dana Cables & Conductors", "United Arab Emirates",
         ["Cables", "Electrical"], "Approved", as_of + timedelta(days=520),
         False, None, "AED 50–100m", 4.0, 8, None),
        ("bdr_silverdune", "Silverdune Process Systems", "Bahrain",
         ["Process packages", "Rotating equipment"], "Approved",
         as_of + timedelta(days=480), True,
         "Unresolved commercial dispute on HAL-19", "AED 100–250m", 3.5, 2,
         "On hold at the client's request until the dispute closes."),
        ("bdr_khaleej", "Khaleej Tank & Vessel Co.", "Kuwait",
         ["Tanks & vessels"], "Approved", as_of + timedelta(days=365),
         False, None, "AED 50–100m", 4.2, 5, None),
        ("bdr_blueharbour", "Blue Harbour Marine Services", "United Arab Emirates",
         ["Marine"], "Not qualified", None,
         False, None, "AED 10–50m", None, 0,
         "Declined at prequalification: no comparable scope delivered."),
        ("bdr_oriongulf", "Orion Gulf Automation", "United Arab Emirates",
         ["Instrumentation", "Control systems"], "Approved",
         as_of + timedelta(days=640), False, None, "AED 10–50m", 4.5, 3, None),
    ]
    for (bid_id, name, country, cats, status, expires, on_hold, hold_reason,
         band, rating, awards, notes) in spec:
        store.create_bidder(
            bidder_id=bid_id,
            name=name,
            country=country,
            trade_categories=cats,
            prequal_status=status,
            prequal_expires_on=expires,
            on_hold=on_hold,
            hold_reason=hold_reason,
            turnover_band=band,
            performance_rating=rating,
            past_awards=awards,
            notes=notes,
        )
    return {b.id: b.name for b in store.list_bidders()}


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
         "Electrical", 4_100_000, 210, False),
        ("itm_hal_valves", "prj_haliba", "Valves",
         "Wellhead tie-in ball and gate valves, 6\" to 16\"", 148, "ea",
         "Valves", 9_400_000, 260, True),
        ("itm_hal_piping", "prj_haliba", "Piping",
         "Carbon steel line pipe and fittings for the tie-in spools", 1, "lot",
         "Piping", 12_700_000, 300, False),
        ("itm_hal_instruments", "prj_haliba", "Instrumentation",
         "Wellhead pressure and temperature transmitters", 96, "ea",
         "Instrumentation", 2_300_000, 330, False),
        ("itm_ruu_compressors", "prj_ruwais", "Rotating equipment",
         "Instrument air compressor packages, 2 x 100 %", 2, "ea",
         "Rotating equipment", 15_800_000, 400, True),
        ("itm_ruu_instruments", "prj_ruwais", "Instrumentation",
         "Field instrumentation for the utilities battery limit", 1, "lot",
         "Instrumentation", 3_600_000, 250, False),
        ("itm_ruu_control", "prj_ruwais", "Control systems",
         "Utilities PLC and HMI upgrade", 1, "lot",
         "Control systems", 5_200_000, 470, False),
        ("itm_ruu_hvac", "prj_ruwais", "HVAC",
         "Substation HVAC replacement units", 8, "ea", "HVAC", 1_150_000, 190, False),
        ("itm_jat_hvcable", "prj_jebelali", "Cables",
         "11 kV three-core XLPE cable, terminal ring main", 4_800, "m",
         "Cables", 6_900_000, 120, False),
        ("itm_jat_tanks", "prj_jebelali", "Tanks & vessels",
         "Bunkering storage tanks, 2 x 5 000 m³", 2, "ea",
         "Tanks & vessels", 21_500_000, 380, True),
        ("itm_jat_steel", "prj_jebelali", "Structural steel",
         "Pipe rack and access platform steelwork", 640, "t",
         "Structural steel", 8_800_000, 150, False),
        # Deliberately dated past the project's live period, so the advisory
        # live-period caution has something to show. It is a warning, not a
        # refusal — which is the point being demonstrated.
        ("itm_jat_marine", "prj_jebelali", "Marine",
         "Fender and bollard replacement at berth 4", 1, "lot",
         "Marine", 3_100_000, 520, False),
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
    from workflow.models.rfq import Attachment

    store.set_technical_package(
        rfq_id,
        revision=revision,
        basis_of_design=basis,
        attachments=[Attachment(doc_code=c, title=t, revision=r) for c, t, r in attachments],
    )
    store.freeze_package(rfq_id, by=ENGINEER)


def _invite(store: WorkflowStore, rfq_id: str, pairs: list[tuple[str, str]],
            as_of: date, override: dict[str, str] | None = None) -> None:
    """`pairs` is (entry_id, bidder_id). Entry ids are pinned for the same
    reason RFQ ids are: a rehearsed demo removes a specific row."""
    for entry_id, bidder_id in pairs:
        reason = (override or {}).get(bidder_id)
        store.add_shortlist_entry(
            rfq_id,
            entry_id=entry_id,
            vendor_id=bidder_id,
            as_of=as_of,
            override_by=PROCUREMENT if reason else None,
            override_reason=reason,
        )


def _vdrl(store: WorkflowStore, rfq_id: str, lines: list[tuple[str, str, str, bool]]) -> None:
    for code, title, doc_type, mandatory in lines:
        store.add_vdrl_line(
            rfq_id, doc_code=code, title=title, doc_type=doc_type, mandatory=mandatory
        )


def build_demo_store(as_of: date) -> WorkflowStore:
    """The whole demo, built the way a user would have built it.

    Order matters in one place: approval is revoked by any edit to the vendor
    set, so every invitation is issued before `approve_shortlist`. That is not
    a quirk of the seed — it is the rule the screen enforces, reproduced here
    because going through the store is what makes the seeded states real.
    """
    store = WorkflowStore()
    _bidders(store, as_of)
    _projects_and_items(store, as_of)

    # -- HAL-01: still being scoped. Package attached, not yet frozen, so the
    # Scoping gate is closed and the demo has something to *do*.
    store.create_rfq(
        rfq_id="rfq_hal01", project_id="prj_haliba", item_ids=["itm_hal_switchgear"],
        reference="HAL-RFQ-2026-001", package="LV switchgear",
        discipline="Electrical", value_estimate_aed=4_100_000,
    )
    store.set_technical_package(
        "rfq_hal01", revision="Rev. A",
        basis_of_design="Six LV boards, 415 V, form 4b, per HAL-SLD-002.",
        attachments=[],
    )

    # -- HAL-02: shortlisting. Frozen package; vendors invited, including one
    # override, and approval deliberately not yet given.
    store.create_rfq(
        rfq_id="rfq_hal02", project_id="prj_haliba",
        item_ids=["itm_hal_valves", "itm_hal_piping"],
        reference="HAL-RFQ-2026-002", package="Wellhead tie-in valves and piping",
        discipline="Valves", value_estimate_aed=22_100_000,
    )
    _freeze(store, "rfq_hal02", "Rev. C",
            "148 wellhead tie-in valves and the associated spool material.",
            [("HAL-PID-014", "Tie-in P&ID", "Rev. C"),
             ("HAL-DS-101", "Valve datasheets", "Rev. B"),
             ("HAL-SPC-007", "Piping material specification", "Rev. D")])
    store.transition("rfq_hal02", Stage.SHORTLISTING, by=BUYER,
                     reason="Package frozen at Rev. C")
    _invite(store, "rfq_hal02",
            [("sle_hal02_north", "bdr_northwind"),
             ("sle_hal02_sand", "bdr_sandstone"),
             ("sle_hal02_khal", "bdr_khaleej")],
            as_of,
            override={"bdr_sandstone":
                      "Only fabricator with the 16-inch tie-in spool jig; "
                      "renewal audit booked for next month."})

    # -- RUU-01: issued. Everything the Shortlisting gate wants is in place.
    store.create_rfq(
        rfq_id="rfq_ruu01", project_id="prj_ruwais", item_ids=["itm_ruu_compressors"],
        reference="RUU-RFQ-2026-004", package="Instrument air compressor packages",
        discipline="Rotating equipment", value_estimate_aed=15_800_000,
    )
    _freeze(store, "rfq_ruu01", "Rev. B",
            "Two 100 % instrument air packages, oil-free, with dryers.",
            [("RUU-DS-220", "Compressor datasheet", "Rev. B"),
             ("RUU-PID-031", "Instrument air P&ID", "Rev. A")])
    store.transition("rfq_ruu01", Stage.SHORTLISTING, by=BUYER,
                     reason="Package frozen at Rev. B")
    _invite(store, "rfq_ruu01",
            [("sle_ruu01_falcon", "bdr_falconbay"),
             ("sle_ruu01_silver", "bdr_silverdune")],
            as_of,
            override={"bdr_silverdune":
                      "Retained for competitive tension; award is conditional "
                      "on the HAL-19 dispute closing."})
    store.approve_shortlist("rfq_ruu01", by=PROCUREMENT)
    store.set_tbe_template(
        "rfq_ruu01",
        criteria=["Capacity at site conditions", "Specific power",
                  "Dryer dew point", "Noise at 1 m", "Spares for two years"],
        source_rfq_reference="RUU-RFQ-2025-018",
    )
    store.transition("rfq_ruu01", Stage.ISSUED, by=BUYER,
                     reason="Issued to two bidders")
    _vdrl(store, "rfq_ruu01",
          [("GA-001", "General arrangement drawing", "GA Drawing", True),
           ("DS-002", "Filled compressor datasheet", "Datasheet", True),
           ("TP-003", "Factory acceptance test procedure", "Test Procedure", True),
           ("CE-004", "Motor efficiency certificate", "Certificate", False)])

    # -- RUU-02: clarifications. The technical query round.
    store.create_rfq(
        rfq_id="rfq_ruu02", project_id="prj_ruwais",
        item_ids=["itm_ruu_instruments", "itm_ruu_control"],
        reference="RUU-RFQ-2026-006", package="Field instrumentation and PLC upgrade",
        discipline="Instrumentation", value_estimate_aed=8_800_000,
    )
    _freeze(store, "rfq_ruu02", "Rev. A",
            "Battery-limit field instruments plus the utilities PLC and HMI.",
            [("RUU-IDX-410", "Instrument index", "Rev. A"),
             ("RUU-IO-411", "IO list", "Rev. A")])
    store.transition("rfq_ruu02", Stage.SHORTLISTING, by=BUYER)
    _invite(store, "rfq_ruu02",
            [("sle_ruu02_merid", "bdr_meridian"),
             ("sle_ruu02_orion", "bdr_oriongulf")],
            as_of)
    store.approve_shortlist("rfq_ruu02", by=PROCUREMENT)
    store.set_tbe_template(
        "rfq_ruu02",
        criteria=["Instrument accuracy class", "Hazardous area certification",
                  "PLC spare IO capacity", "Cyber-security compliance"],
    )
    store.transition("rfq_ruu02", Stage.ISSUED, by=BUYER)
    _vdrl(store, "rfq_ruu02",
          [("IDX-001", "Priced instrument index", "Datasheet", True),
           ("CE-002", "ATEX / IECEx certificates", "Certificate", True)])
    store.transition("rfq_ruu02", Stage.CLARIFICATIONS, by=BUYER,
                     reason="Two technical queries raised on the IO list")

    # -- JAT-01: bids received, not yet opened for evaluation. The gate to
    # Evaluation is closed until bids are selected, which is the next click.
    store.create_rfq(
        rfq_id="rfq_jat01", project_id="prj_jebelali", item_ids=["itm_jat_hvcable"],
        reference="JAT-RFQ-2026-009", package="11 kV ring main cable",
        discipline="Cables", value_estimate_aed=6_900_000,
    )
    _freeze(store, "rfq_jat01", "Rev. B",
            "4 800 m of 11 kV three-core XLPE, drum lengths per the schedule.",
            [("JAT-CS-501", "Cable schedule", "Rev. B")])
    store.transition("rfq_jat01", Stage.SHORTLISTING, by=BUYER)
    _invite(store, "rfq_jat01",
            [("sle_jat01_rasdana", "bdr_rasdana"),
             ("sle_jat01_almunara", "bdr_almunara")],
            as_of)
    store.approve_shortlist("rfq_jat01", by=PROCUREMENT)
    store.set_tbe_template("rfq_jat01", criteria=["Conductor size", "Drum lengths",
                                                  "Type test certificates"])
    store.transition("rfq_jat01", Stage.ISSUED, by=BUYER)
    _vdrl(store, "rfq_jat01",
          [("TT-001", "Type test certificate", "Certificate", True),
           ("DS-002", "Cable datasheet", "Datasheet", True)])
    store.transition("rfq_jat01", Stage.CLARIFICATIONS, by=BUYER)
    store.transition("rfq_jat01", Stage.BIDS_RECEIVED, by=BUYER,
                     reason="Both bids received before the deadline")
    ras_bid = store.register_bid("rfq_jat01", vendor_name="Ras Dana Cables & Conductors",
                                 headline_price_aed=6_450_000)
    munara_bid = store.register_bid("rfq_jat01", vendor_name="Al Munara Switchgear LLC",
                                    headline_price_aed=7_020_000)
    store.record_vdrl_receipt(ras_bid.id, doc_code="TT-001", state="received", revision="Rev. 0")
    store.record_vdrl_receipt(ras_bid.id, doc_code="DS-002", state="received", revision="Rev. 0")
    store.record_vdrl_receipt(munara_bid.id, doc_code="TT-001", state="unreadable")

    # -- JAT-02: under evaluation, with a recorded selection rationale.
    store.create_rfq(
        rfq_id="rfq_jat02", project_id="prj_jebelali",
        item_ids=["itm_jat_tanks", "itm_jat_steel"],
        reference="JAT-RFQ-2026-011", package="Bunkering tanks and pipe rack steel",
        discipline="Tanks & vessels", value_estimate_aed=30_300_000,
    )
    _freeze(store, "rfq_jat02", "Rev. A",
            "Two 5 000 m³ bunkering tanks with the supporting rack steelwork.",
            [("JAT-TK-601", "Tank general arrangement", "Rev. A"),
             ("JAT-ST-602", "Steel tonnage schedule", "Rev. A")])
    store.transition("rfq_jat02", Stage.SHORTLISTING, by=BUYER)
    _invite(store, "rfq_jat02",
            [("sle_jat02_khal", "bdr_khaleej"),
             ("sle_jat02_sand", "bdr_sandstone")],
            as_of,
            override={"bdr_sandstone":
                      "Incumbent on the adjacent rack; lapse is documentary only."})
    store.approve_shortlist("rfq_jat02", by=PROCUREMENT)
    store.set_tbe_template("rfq_jat02", criteria=["Plate grade", "Weld procedure",
                                                  "Coating system", "Delivery to site"])
    store.transition("rfq_jat02", Stage.ISSUED, by=BUYER)
    _vdrl(store, "rfq_jat02",
          [("WP-001", "Welding procedure specification", "Test Procedure", True),
           ("CO-002", "Coating system datasheet", "Datasheet", True),
           ("MT-003", "Material test certificates", "Certificate", True)])
    store.transition("rfq_jat02", Stage.CLARIFICATIONS, by=BUYER)
    store.transition("rfq_jat02", Stage.BIDS_RECEIVED, by=BUYER)
    khaleej_bid = store.register_bid("rfq_jat02", vendor_name="Khaleej Tank & Vessel Co.",
                                     headline_price_aed=28_900_000)
    sandstone_bid = store.register_bid("rfq_jat02", vendor_name="Sandstone Piping Industries",
                                       headline_price_aed=31_750_000)
    for code in ("WP-001", "CO-002", "MT-003"):
        store.record_vdrl_receipt(khaleej_bid.id, doc_code=code, state="received",
                                  revision="Rev. 0")
    store.record_vdrl_receipt(sandstone_bid.id, doc_code="WP-001", state="received",
                              revision="Rev. 0")
    store.record_vdrl_receipt(sandstone_bid.id, doc_code="CO-002", state="not_received")
    store.select_bids(
        "rfq_jat02", [khaleej_bid.id, sandstone_bid.id], by=PROCUREMENT,
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

    store = build_demo_store(args.as_of or date.today())
    persistence.save(args.root, store)
    print(
        f"Wrote {len(store.list_bidders())} bidders, "
        f"{len(store.list_projects())} projects and "
        f"{len(store.list_rfqs())} RFQs to {persistence.workflow_path(args.root)}."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
