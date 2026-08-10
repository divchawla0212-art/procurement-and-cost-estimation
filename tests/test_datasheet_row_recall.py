"""The deterministic half of the parser A/B.

No LLM, no key, no network -- so unlike the `needs_human` counts the harness
reports, this runs in CI and guards against a parser regression silently
halving what the pipeline can see.

The corpus is `processed-data/`, which is tracked, so these do not skip.
"""
import os
import shutil

import pytest

from procurement.loaders import _pdftotext, _pypdf
from tools.datasheet_ground_truth import (MIN_NORMALIZED_CHARS, normalize,
                                          parameters, recall, recall_unordered)

_ROOT = os.path.dirname(os.path.dirname(__file__))
_XLSX = os.path.join(
    _ROOT, "processed-data", "01-client-mr-rfq",
    "DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.xlsx")
_KERUI_PDF = os.path.join(
    _ROOT, "processed-data", "02-vendor-bids", "KERUI",
    "01 DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.pdf")


def test_the_ground_truth_sheet_yields_a_real_parameter_list():
    kept, dropped = parameters(_XLSX)
    # 236 at the time of writing. A floor, not an equality: the assertion is
    # "this is a substantial parameter list", not "this workbook is frozen".
    assert len(kept) >= 200, f"only {len(kept)} scorable parameters"
    assert all(len(normalize(n)) >= MIN_NORMALIZED_CHARS for n in kept)
    assert all(len(normalize(n)) < MIN_NORMALIZED_CHARS for n in dropped)


def test_section_banners_are_not_parameters():
    """Column A "1" is a heading -- GENERAL, DESIGN PARAMETERS FOR ENGINE --
    with empty requirement and vendor cells. Counting those inflates the
    denominator with rows no parser could meaningfully "recover"."""
    kept, dropped = parameters(_XLSX)
    names = {normalize(n) for n in kept} | {normalize(n) for n in dropped}
    for banner in ("GENERAL", "SITE & INSTALLATION CONDITION",
                   "DESIGN PARAMETERS FOR ENGINE"):
        assert normalize(banner) not in names


def test_contiguous_recall_is_strict_about_interleaving():
    names = ["Design Ambient Temperature - Maximum"]
    interleaved = "Design Ambient Temperature - 2.3 Compliance Required Maximum"
    assert recall(interleaved, names) == ([], names)
    assert recall("...Design Ambient Temperature - Maximum...", names)[0] == names


def test_unordered_recall_forgives_exactly_that():
    """The pair is the instrument: contiguous says "in one piece", unordered
    says "present at all", and only their difference identifies interleaving."""
    names = ["Design Ambient Temperature - Maximum"]
    interleaved = "Design Ambient Temperature - 2.3 Compliance Required Maximum"
    assert recall_unordered(interleaved, names)[0] == names
    assert recall_unordered("Design Ambient Temperature only", names)[1] == names


def test_pypdf_recovers_the_datasheet_parameters():
    """pypdf is a core dependency, so this floor is always measured.

    Measured at 236/236 contiguous. The floor sits far below that because it
    guards a regression -- it does not certify the reader.
    """
    kept, _dropped = parameters(_XLSX)
    text = _pypdf(_KERUI_PDF)
    found, _missing = recall(text, kept)
    assert len(found) / len(kept) >= 0.85, (
        f"pypdf recovered only {len(found)}/{len(kept)} datasheet parameters "
        f"contiguously")


@pytest.mark.skipif(shutil.which("pdftotext") is None,
                    reason="requires the pdftotext CLI, which CI does not install")
def test_pdftotext_loses_almost_nothing_even_where_it_interleaves():
    """Pins the decomposition that motivated `recall_unordered`.

    Measured: 216/236 contiguous but 235/236 unordered. pdftotext -layout is
    not dropping this content, it is splicing the row number and the
    requirement column into the middle of long description cells. A future
    change that genuinely *lost* text would move the unordered figure, which
    the contiguous one alone could not distinguish.
    """
    kept, _dropped = parameters(_XLSX)
    text = _pdftotext(_KERUI_PDF)
    unordered, _missing = recall_unordered(text, kept)
    assert len(unordered) / len(kept) >= 0.95, (
        f"pdftotext lost content outright: only {len(unordered)}/{len(kept)} "
        f"parameters have all their words present anywhere in the text")


@pytest.mark.skipif(shutil.which("pdftotext") is None,
                    reason="requires the pdftotext CLI, which CI does not install")
def test_layout_padding_is_not_mistaken_for_recovered_text():
    """The trap this experiment nearly fell into.

    pdftotext returns ~3x pypdf's character count on this document, which reads
    as "recovers three times as much". It does not: strip the whitespace and
    both return exactly the same number of alphanumeric characters. Character
    yield is a measure of layout mode, not of extraction quality, and no floor
    in this suite may be built on it.
    """
    a, b = normalize(_pdftotext(_KERUI_PDF)), normalize(_pypdf(_KERUI_PDF))
    assert abs(len(a) - len(b)) / max(len(a), len(b)) < 0.05, (
        f"alphanumeric yield diverged: pdftotext {len(a)} vs pypdf {len(b)}")
