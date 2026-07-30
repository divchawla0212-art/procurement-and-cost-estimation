from procurement.revisions import parse_revision, normalised_base, resolve_supersession
from procurement.store.models import DocumentRecord


def _doc(doc_id, path, vendor="ADPOWER"):
    return DocumentRecord(doc_id=doc_id, path=path, vendor=vendor,
                          content_sha256="0" * 64)


def test_parse_revision_forms():
    assert parse_revision("ADP-13158-2024-935(Rev1).pdf") == "1"
    assert parse_revision("Techno Commercial proposal AESL-GTC-60808 -REV00.pdf") == "0"
    assert parse_revision("Comparative Statement (CS) - Gas Generators [Rev.2].xlsx") == "2"
    assert parse_revision("GDX-P-26-072 REV-01 EA T-00935.docx") == "1"
    assert parse_revision("Some Drawing Rev A.pdf") == "A"
    assert parse_revision("ADP-13158-2024-935.pdf") is None


def test_normalised_base_strips_revision_index_and_extension():
    assert (normalised_base("ADP-13158-2024-935(Rev1).pdf")
            == normalised_base("ADP-13158-2024-935.pdf"))
    assert (normalised_base("01 DataSheet Gas Generator.pdf")
            == normalised_base("DataSheet Gas Generator.pdf"))


def test_higher_revision_supersedes_lower():
    docs = resolve_supersession([
        _doc("d1", "vendors/ADPOWER/ADP-13158-2024-935.pdf"),
        _doc("d2", "vendors/ADPOWER/ADP-13158-2024-935(Rev1).pdf"),
    ])
    by_id = {d.doc_id: d for d in docs}
    assert by_id["d1"].superseded_by == "d2"
    assert by_id["d2"].supersedes == "d1"
    assert by_id["d2"].superseded_by is None
    assert by_id["d2"].revision_label == "1"


def test_explicit_superseded_marker_loses_regardless_of_revision():
    docs = resolve_supersession([
        _doc("s1", "requirements/ADN-AEC-ME-SPC-026 MR Gas Genset Superseded with MOM 20241111.pdf", vendor=None),
        _doc("s2", "requirements/ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf", vendor=None),
    ])
    by_id = {d.doc_id: d for d in docs}
    assert by_id["s1"].superseded_by == "s2"
    assert by_id["s2"].superseded_by is None


def test_different_vendors_never_supersede_each_other():
    docs = resolve_supersession([
        _doc("k1", "vendors/KERUI/Quotation.pdf", vendor="KERUI"),
        _doc("m1", "vendors/MKON/Quotation.pdf", vendor="MKON"),
    ])
    assert all(d.superseded_by is None for d in docs)


def test_unrelated_documents_are_untouched():
    docs = resolve_supersession([
        _doc("a", "vendors/KERUI/BOM.pdf", vendor="KERUI"),
        _doc("b", "vendors/KERUI/Quotation.pdf", vendor="KERUI"),
    ])
    assert all(d.superseded_by is None and d.supersedes is None for d in docs)


def test_three_revisions_chain_to_the_newest_only():
    docs = resolve_supersession([
        _doc("v0", "vendors/A/Doc.pdf", vendor="A"),
        _doc("v1", "vendors/A/Doc Rev1.pdf", vendor="A"),
        _doc("v2", "vendors/A/Doc Rev2.pdf", vendor="A"),
    ])
    by_id = {d.doc_id: d for d in docs}
    # superseded_by always names the newest — the document a later
    # extraction-skip step should treat as authoritative.
    assert by_id["v0"].superseded_by == "v2"
    assert by_id["v1"].superseded_by == "v2"
    assert by_id["v2"].superseded_by is None
    # supersedes is the lineage pointer and must name the immediate
    # predecessor, not just any older sibling, so the chain reads v0 -> v1 -> v2
    # rather than v2 skipping straight past v1 to v0.
    assert by_id["v2"].supersedes == "v1"
    assert by_id["v1"].supersedes == "v0"
    assert by_id["v0"].supersedes is None


# --- Adversarial cases beyond the brief's own examples (Task 1's "rev" /
# "BOM"-style word-boundary lesson applies here too: "rev" and "copy" show up
# inside ordinary words, not just as revision/duplicate markers). ---

def test_rev_lookalike_words_are_not_treated_as_revision_markers():
    assert parse_revision("Revenue Report.pdf") is None
    assert parse_revision("Reverse Osmosis.pdf") is None
    assert parse_revision("Revised Scope of Work.pdf") is None


def test_rev_lookalike_words_survive_normalisation_intact():
    # A naive "rev" + single-letter chunk matcher (no trailing word boundary)
    # would treat "Reve" in "Revenue" or "Reve" in "Reverse" as a fake
    # revision marker and mangle the base name. It must not.
    assert normalised_base("Revenue Report.pdf") == "revenuereport"
    assert normalised_base("Reverse Osmosis.pdf") == "reverseosmosis"


def test_copy_lookalike_words_are_not_stripped():
    # "copy" is stripped as an OS-duplicate marker, but only as a whole word.
    assert normalised_base("Photocopy of Contract.pdf") == "photocopyofcontract"
    assert normalised_base("Copyright Notice.pdf") == "copyrightnotice"


def test_unrelated_documents_with_rev_lookalike_names_are_not_grouped():
    docs = resolve_supersession([
        _doc("r1", "vendors/ADPOWER/Revenue Report.pdf"),
        _doc("r2", "vendors/ADPOWER/Reverse Osmosis.pdf"),
    ])
    assert all(d.superseded_by is None and d.supersedes is None for d in docs)


# --- Concatenated revision markers: no separator between the identifier and
# the marker (e.g. "935Rev1.pdf"). A leading word boundary on the "rev"
# regexes would silently stop recognising these; the trailing boundary alone
# is what protects against "Revenue"/"Reverse" (see tests above). ---

def test_parse_revision_recognises_concatenated_marker():
    assert parse_revision("935Rev1.pdf") == "1"


def test_normalised_base_links_concatenated_revision_marker():
    assert normalised_base("935Rev1.pdf") == normalised_base("935.pdf")


def test_concatenated_revision_marker_resolves_lineage():
    docs = resolve_supersession([
        _doc("c1", "vendors/A/Doc.pdf", vendor="A"),
        _doc("c2", "vendors/A/DocRev1.pdf", vendor="A"),
    ])
    by_id = {d.doc_id: d for d in docs}
    assert by_id["c1"].superseded_by == "c2"
    assert by_id["c2"].supersedes == "c1"
    assert by_id["c2"].superseded_by is None
