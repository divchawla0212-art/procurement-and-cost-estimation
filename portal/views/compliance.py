"""The compliance screen — read-only (spec section 2).

Every number shown here comes from `procurement.matrix`. This module holds no
logic worth testing: it decides layout, not meaning.
"""
import streamlit as st

from procurement.matrix import build_matrix, rows_in_group

_GROUP_LABEL = {"not_matched": "Not matched",
                "needs_human": "Needs a human",
                "matched": "Matched"}

_VERDICT_MARK = {"pass": "✅", "fail": "❌", "deviation": "⚠️",
                 "unanswered": "❓", "review": "👤"}


def _bound(row) -> str:
    """The requirement's checkable bound, or its clause text."""
    if row.checkability == "stated":
        return (f"{row.parameter} = {row.value}" if row.value is not None
                else f"{row.parameter} (must be stated)")
    if row.checkability != "auto":
        return row.text
    value = row.value if not isinstance(row.value, list) else "..".join(
        str(v) for v in row.value)
    return f"{row.parameter} {row.operator} {value} {row.unit or ''}".strip()


def _render_coverage(coverage) -> None:
    st.markdown("#### Coverage")
    checked = coverage.auto_cells + coverage.stated_cells
    if not checked:
        st.caption("No checked requirements are stored yet.")
        return

    cols = st.columns(4)
    cols[0].metric("Checked cells", checked)
    for col, verdict in zip(cols[1:], ("pass", "fail", "unanswered")):
        count = coverage.by_verdict.get(verdict, 0)
        col.metric(verdict.capitalize(), f"{count}  ({count / checked:.0%})")

    # `unanswered_silent`/`unanswered_refused` only ever tally `auto` cells
    # (matrix.py's docstring: the split "only makes sense against `auto`").
    # A `stated` requirement is `unanswered` for exactly the "silent" reason -
    # no vendor document named the parameter - so the remainder after
    # subtracting the auto-tier split is that stated-tier count, not slop.
    silent, refused = coverage.unanswered_silent, coverage.unanswered_refused
    unanswered_total = coverage.by_verdict.get("unanswered", 0)
    stated_unanswered = unanswered_total - silent - refused
    if silent or refused or stated_unanswered:
        lines = [
            f"- **{silent}** unanswered because no vendor document stated the "
            "parameter — this is the real coverage gap.",
            f"- **{refused}** unanswered because the fact was found and the "
            "comparison could not be made — this measures our reach, not the "
            "vendor's answer.",
        ]
        if stated_unanswered:
            lines.append(
                f"- **{stated_unanswered}** unanswered on a `stated` "
                "requirement — no vendor document named the parameter at all."
            )
        st.markdown("\n".join(lines))
    st.caption(
        "Counted over checked requirements only — `auto` and `stated`. A "
        "percentage over every requirement would be dominated by the ones "
        "needing human judgement and would say nothing about extraction."
    )


def _render_row(row, vendors) -> None:
    st.markdown(f"**{row.clause_ref}** — {_bound(row)}")
    if row.checkability in ("auto", "stated"):
        st.caption(row.text)
    for vendor in vendors:
        cell = row.cells[vendor]
        mark = _VERDICT_MARK.get(cell.verdict, "•")
        st.markdown(f"&nbsp;&nbsp;{mark} **{vendor}** — {cell.verdict}",
                    unsafe_allow_html=True)
        if cell.rationale:
            # Displayed verbatim, never parsed (spec section 5).
            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;{cell.rationale}")
    st.divider()


def _render_worklist(matrix) -> None:
    for group in ("not_matched", "needs_human"):
        rows = rows_in_group(matrix, group)
        st.markdown(f"### {_GROUP_LABEL[group]} ({len(rows)})")
        if not rows:
            st.caption("Nothing here.")
            continue
        for row in rows:
            _render_row(row, matrix.vendors)

    matched = rows_in_group(matrix, "matched")
    with st.expander(f"{_GROUP_LABEL['matched']} ({len(matched)})"):
        for row in matched:
            _render_row(row, matrix.vendors)


def _render_grid(matrix) -> None:
    table = [{"Clause": row.clause_ref, "Requirement": _bound(row),
              **{v: f"{_VERDICT_MARK.get(row.cells[v].verdict, '')} "
                    f"{row.cells[v].verdict}" for v in matrix.vendors}}
             for row in matrix.rows]
    st.dataframe(table, use_container_width=True, hide_index=True)


def render(root: str, slug: str) -> None:
    matrix = build_matrix(root, slug)
    st.markdown("### Compliance")
    if not matrix.rows:
        st.info("Run ingestion to build the compliance matrix.")
        return
    if not matrix.vendors:
        st.warning("The project has no vendors, so there is nothing to compare.")
        return

    _render_coverage(matrix.coverage)
    st.markdown("---")
    mode = st.radio("View", ["Worklist", "Full grid"], horizontal=True,
                    key="compliance_mode", label_visibility="collapsed")
    if mode == "Worklist":
        _render_worklist(matrix)
    else:
        _render_grid(matrix)
