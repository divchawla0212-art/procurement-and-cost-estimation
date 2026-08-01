"""The pre-reviewed vendor comparison screen.

This screen bypasses the pipeline entirely: the workbook it renders is already
a reviewed artefact, so nothing here classifies, extracts, or writes to the
store. It appears only for the vendor roster the workbook actually covers —
showing a KAN-vs-ADPOWER matrix under a project of different vendors would
attribute one vendor's answers to another.
"""
import os

import streamlit as st

from procurement.render_comparison import comparison_to_html, comparison_to_page
from procurement.vendor_comparison import load_comparison

WORKBOOK = os.path.join("data", "procurement-data", "Gas_Genset_Vendor_Comparison.xlsx")

# The roster this workbook covers. Vendor A is MKON's offer (manufacturer KAN,
# Italy); Vendor B is ADPOWER FZCO. Both must be present — a project with only
# one of them has no comparison to show.
REQUIRED_VENDORS = frozenset({"adpower", "mkon"})


def applies_to(vendors: list[str]) -> bool:
    return REQUIRED_VENDORS.issubset({v.strip().lower() for v in vendors})


def render(vendors: list[str], workbook: str = WORKBOOK) -> None:
    if not applies_to(vendors):
        return
    if not os.path.exists(workbook):
        st.warning(f"Comparison workbook not found: {workbook}")
        return
    try:
        comparison = load_comparison(workbook)
    except (ValueError, OSError) as exc:
        st.error(f"Could not read the comparison workbook: {exc}")
        return

    st.markdown("### Gas genset — vendor compliance comparison")
    st.markdown(comparison_to_html(comparison), unsafe_allow_html=True)
    st.caption(
        "Green is the reviewer's 'Confirmed'. Yellow is every other verdict — "
        "furnished data, a qualified yes, a refusal, or no response — and each "
        "cell keeps the wording so you can tell which. Read straight from "
        f"{os.path.basename(workbook)}; nothing on this screen is re-derived."
    )

    c1, c2 = st.columns(2)
    c1.download_button(
        "Download comparison (HTML)",
        comparison_to_page(comparison).encode("utf-8"),
        file_name="gas-genset-vendor-comparison.html", mime="text/html")
    with open(workbook, "rb") as fh:
        c2.download_button(
            "Download source workbook (XLSX)", fh.read(),
            file_name=os.path.basename(workbook),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if comparison.summary:
        with st.expander("Executive summary & recommendation"):
            for line in comparison.summary:
                st.write(line)
