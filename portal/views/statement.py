"""The comparative statement screen — phase 4's landing view."""
import streamlit as st

from procurement.export import statement_to_csv_str, statement_to_xlsx_bytes
from procurement.render import statement_to_html
from procurement.statement import build_statement
from procurement.store import events, snapshots
from procurement.store.models import Event
from procurement.pipeline import _now


def save_feedback(root: str, slug: str, vendor: str, text: str, reason: str) -> None:
    """The only write on this screen (INV-S5).

    One transaction, so `generation` bumps exactly once, and the reason lands
    in the event rather than on the field: `VendorFacts` has no per-field
    metadata slot, and the History screen is where a rationale is read.
    """
    if not (reason or "").strip():
        raise ValueError("a feedback note needs a reason")
    facts = snapshots.load_facts(root, slug, vendor)
    if facts is None:
        raise ValueError(f"no facts stored for {vendor}")
    with snapshots.transaction(root, slug):
        facts.technical_feedback = text
        snapshots.save_facts(root, slug, facts)
        events.append_event(root, slug, Event(
            at=_now(), run_id="portal", actor="portal",
            action="facts.feedback_edited", target=vendor,
            detail={"reason": reason.strip(), "text": text}))


def render(root: str, slug: str) -> None:
    statement = build_statement(root, slug)
    st.markdown("### Comparative Statement")
    if not statement.vendors:
        st.info("Add a vendor and run ingestion to build the statement.")
        return

    st.markdown(statement_to_html(statement), unsafe_allow_html=True)
    st.caption(
        "Blank means the quotation did not state it — never zero. "
        "A `*` on FINAL VALUE means the column excludes something; hover it."
    )

    c1, c2 = st.columns(2)
    c1.download_button(
        "Download Excel", statement_to_xlsx_bytes(statement),
        file_name=f"{slug}-comparative-statement.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c2.download_button(
        # utf-8-sig: the tally separator is U+00B7 and Excel reads a BOM-less
        # UTF-8 CSV as cp1252, which turns it into mojibake in the deliverable.
        "Download CSV", statement_to_csv_str(statement).encode("utf-8-sig"),
        file_name=f"{slug}-comparative-statement.csv", mime="text/csv")

    with st.expander("Technical feedback"):
        vendor = st.selectbox("Vendor", statement.vendors, key="fb_vendor")
        facts = snapshots.load_facts(root, slug, vendor)
        current = (facts.technical_feedback if facts else None) or ""
        text = st.text_area("Note", value=current, key="fb_text")
        reason = st.text_input("Reason for this change", key="fb_reason")
        if st.button("Save note", key="fb_save"):
            try:
                save_feedback(root, slug, vendor, text, reason)
                st.success(f"Saved feedback for {vendor}.")
            except ValueError as exc:
                st.error(str(exc))
