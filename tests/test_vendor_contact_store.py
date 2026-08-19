"""The contact directory in the store, and the key it must never write.

`_vendor_contacts` is the second `WorkflowStore` field with no line in
`to_document` / `from_document`, after `_bidders`. That rule exists because a
field without those lines silently fails to survive a restart, so the exemption
is only safe while something else loads and saves it — and while a test says
the document stays clean.
"""
import json
import os
from datetime import datetime, timezone

from workflow import contact_db, persistence
from workflow.models.vendor_contact import VendorContact
from workflow.store import WorkflowStore

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def a_contact(name="DANWAY ABU DHABI L.L.C", emails=None) -> VendorContact:
    return VendorContact(
        vendor_name=name,
        emails=emails if emails is not None else ["sales@danway.example"],
        source_document="vendors.xlsx",
        uploaded_by="buyer@example.com",
        uploaded_at=WHEN,
    )


def test_an_address_is_found_by_a_name_folded():
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    assert store.emails_for("danway  abu dhabi l.l.c") == ["sales@danway.example"]


def test_a_vendor_with_no_row_answers_none_and_never_an_empty_list():
    """Two states, not three. `None` is 'no contact held'; there is no `[]`,
    because a row with no address is refused at parse."""
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    assert store.emails_for("BIN SARI SPECIALIZED TECHNOLOGIES") is None


def test_a_spelling_the_fold_does_not_reach_is_a_miss():
    """`L.L.C` against `LLC`. Widening the fold to strip punctuation is how an
    enquiry reaches the wrong company, so this miss is the correct answer."""
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    assert store.emails_for("Danway Abu Dhabi LLC") is None


def test_the_answer_is_a_copy_so_a_caller_cannot_edit_the_directory():
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    store.emails_for("DANWAY ABU DHABI L.L.C").append("oops@x.example")
    assert store.emails_for("DANWAY ABU DHABI L.L.C") == ["sales@danway.example"]


def test_setting_the_directory_replaces_it_wholesale():
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact(), a_contact(name="BIN SARI")])
    store.set_vendor_contacts([a_contact(name="BIN SARI")])
    assert store.emails_for("DANWAY ABU DHABI L.L.C") is None


def test_setting_the_directory_reports_how_many_it_holds():
    store = WorkflowStore()
    assert store.set_vendor_contacts([a_contact(), a_contact(name="BIN SARI")]) == 2


def test_the_directory_survives_a_save_and_a_reload(tmp_path):
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])
    reloaded = persistence.load(root)
    assert reloaded.emails_for("DANWAY ABU DHABI L.L.C") == ["sales@danway.example"]


def test_the_document_carries_no_vendor_contacts_key(tmp_path):
    """V-C. A key here would be a second copy for the first edit to disagree
    with — the argument that moved the registry out."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])
    with open(persistence.workflow_path(root), encoding="utf-8") as handle:
        document = json.load(handle)
    assert "vendor_contacts" not in document
    assert not any("contact" in key for key in document)


def test_a_document_that_somehow_carries_the_key_does_not_hydrate_from_it(tmp_path):
    """The database is the only source. A hand-edited document must not be able
    to introduce a contact the directory does not hold — unlike `bidders`,
    which has a documented one-way migration, this key never existed and so has
    nothing to migrate."""
    root = str(tmp_path)
    os.makedirs(root, exist_ok=True)
    with open(persistence.workflow_path(root), "w", encoding="utf-8") as handle:
        json.dump(
            {"vendor_contacts": [{"vendor_name": "X", "emails": ["x@x.example"]}]},
            handle,
        )
    assert persistence.load(root).emails_for("X") is None


def test_a_write_that_touches_no_contact_does_not_rewrite_the_directory(
    tmp_path, monkeypatch
):
    """The same economy `locked_update` applies to the registry. Asserted on
    the call rather than on the file's timestamp, which is too coarse to tell
    two writes a millisecond apart apart."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])

    calls = []
    real = contact_db.replace_all
    monkeypatch.setattr(
        persistence.contact_db, "replace_all",
        lambda r, c: (calls.append(r), real(r, c))[1],
    )
    with persistence.locked_update(root):
        pass
    assert calls == []


def test_a_write_that_changes_a_contact_does_rewrite_the_directory(
    tmp_path, monkeypatch
):
    """The other half. Skipping the write when nothing changed is only safe if
    the comparison actually notices when something does."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])

    calls = []
    real = contact_db.replace_all
    monkeypatch.setattr(
        persistence.contact_db, "replace_all",
        lambda r, c: (calls.append(r), real(r, c))[1],
    )
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact(emails=["new@danway.example"])])
    assert calls == [root]
    assert persistence.load(root).emails_for("DANWAY ABU DHABI L.L.C") == [
        "new@danway.example",
    ]


def test_an_empty_directory_reads_back_as_no_contacts(tmp_path):
    assert persistence.load(str(tmp_path)).vendor_contacts() == []


def test_the_directory_reads_back_in_folded_name_order():
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact(name="zeta"), a_contact(name="Alpha")])
    assert [c.vendor_name for c in store.vendor_contacts()] == ["Alpha", "zeta"]


# -- the client export truncates every name at 35 characters --------------------


def test_a_name_the_client_export_truncated_still_finds_its_contact():
    """The ADNOC export cuts `Vendor Name` at exactly 35 characters — 9 362
    rows in the file this registry was built from. The contact sheet carries
    the *full* name, so an exact fold match misses and a real company that has
    an address on file reads as "no address on file".

    Nothing in this repository does the truncating, so it cannot be undone at
    import; it is absorbed here, at the one lookup that suffers from it.
    """
    store = WorkflowStore()
    store.set_vendor_contacts([
        a_contact("CONCORDE TECHNICAL - SOLE PROPRIETORSHIP", ["sales@concorde.example"]),
    ])

    truncated = "CONCORDE TECHNICAL - SOLE PROPRIETO"
    assert len(truncated) == 35
    assert store.emails_for(truncated) == ["sales@concorde.example"]


def test_an_exact_match_still_wins_over_the_truncation_rule():
    store = WorkflowStore()
    store.set_vendor_contacts([
        a_contact("A" * 35, ["exact@example.com"]),
        a_contact("A" * 35 + " EXTENDED", ["longer@example.com"]),
    ])

    assert store.emails_for("A" * 35) == ["exact@example.com"]


def test_two_contacts_sharing_a_truncated_prefix_resolve_to_neither():
    """The rule that keeps this from becoming the fuzzy matching this
    repository has twice recorded as a defect. Two real companies whose names
    agree for 35 characters are indistinguishable from a truncated name, so
    the honest answer is that nothing is on file — a buyer told "no address"
    uploads a sheet, while a buyer sent to the wrong company cannot undo it.
    """
    store = WorkflowStore()
    store.set_vendor_contacts([
        a_contact("B" * 35 + " TRADING", ["one@example.com"]),
        a_contact("B" * 35 + " SHIPPING", ["two@example.com"]),
    ])

    assert store.emails_for("B" * 35) is None


def test_a_short_name_is_never_matched_by_prefix():
    """Only a name of exactly the export's cut length may be a truncation. A
    genuinely short name that happens to prefix a longer one is a different
    company, and matching it would be guessing."""
    store = WorkflowStore()
    store.set_vendor_contacts([
        a_contact("AL MASAOOD OIL INDUSTRY", ["full@example.com"]),
    ])

    assert store.emails_for("AL MASAOOD") is None


def test_the_truncation_rule_folds_like_every_other_lookup():
    store = WorkflowStore()
    store.set_vendor_contacts([
        a_contact("concorde  technical - sole   proprietorship", ["folded@example.com"]),
    ])

    assert store.emails_for("CONCORDE TECHNICAL - SOLE PROPRIETO") == [
        "folded@example.com",
    ]
