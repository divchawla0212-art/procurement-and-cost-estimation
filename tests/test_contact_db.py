"""The vendor contact directory in SQLite.

It shares `bidders.db` with the registry — same kind of thing, reference data
that arrives whole from an export — but its own tables. The two facts worth
asserting are that a contact survives the round trip whole, and that reloading
the registry does not touch it. The second is the one a single-module test
would never think to write: the two live in one file, and a `DROP` in either
module would silently take the other's rows.
"""
from datetime import datetime, timezone

from workflow import bidder_db, contact_db
from workflow.models.bidder import ADNOC, Bidder
from workflow.models.vendor_contact import VendorContact

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def a_contact(**overrides) -> VendorContact:
    defaults = dict(
        vendor_name="DANWAY ABU DHABI L.L.C",
        emails=["sales@danway.example", "bids@danway.example"],
        source_document="vendors.xlsx",
        uploaded_by="buyer@example.com",
        uploaded_at=WHEN,
    )
    return VendorContact(**{**defaults, **overrides})


def test_a_missing_database_reads_as_an_empty_directory(tmp_path):
    """A first run, not an error — the same reading `persistence.load` gives a
    missing document."""
    assert contact_db.list_all(str(tmp_path)) == []
    assert contact_db.count(str(tmp_path)) == 0


def test_a_contact_survives_the_round_trip_whole(tmp_path):
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    (stored,) = contact_db.list_all(root)
    assert stored.vendor_name == "DANWAY ABU DHABI L.L.C"
    assert stored.emails == ["sales@danway.example", "bids@danway.example"]
    assert stored.source_document == "vendors.xlsx"
    assert stored.uploaded_by == "buyer@example.com"
    assert stored.uploaded_at == WHEN


def test_the_addresses_keep_the_order_they_were_given_in(tmp_path):
    """`position`, not insertion luck: the first address is the one a reader
    treats as the main contact."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact(emails=["z@x.example", "a@x.example"])])
    assert contact_db.list_all(root)[0].emails == ["z@x.example", "a@x.example"]


def test_a_later_upload_replaces_the_directory_wholesale(tmp_path):
    """V-A. A vendor of the previous upload cannot survive a later one — the
    same invariant `persistence.save` has for the document."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact(), a_contact(vendor_name="BIN SARI")])
    contact_db.replace_all(root, [a_contact(vendor_name="BIN SARI")])
    assert [c.vendor_name for c in contact_db.list_all(root)] == ["BIN SARI"]


def test_no_address_outlives_its_vendor(tmp_path):
    """V-B. The cascade, counted in the child table rather than inferred from
    the read — a read that joins would hide an orphan row rather than fail."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    contact_db.replace_all(
        root, [a_contact(vendor_name="BIN SARI", emails=["a@x.example"])]
    )
    with contact_db.connect(root) as conn:
        rows = conn.execute("SELECT count(*) FROM vendor_contact_emails").fetchone()[0]
    assert rows == 1


def test_every_stored_key_is_the_fold_of_its_own_name(tmp_path):
    """V-E. When the key written and the key queried disagree, the symptom is
    silent: a vendor invisible to the very lookup their own row satisfies."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact(vendor_name="DANWAY  Abu Dhabi L.L.C")])
    with contact_db.connect(root) as conn:
        key, name = conn.execute(
            "SELECT name_key, vendor_name FROM vendor_contacts"
        ).fetchone()
    assert key == contact_db.fold(name)
    assert key == "danway abu dhabi l.l.c"


def test_two_names_folding_alike_cannot_both_be_stored(tmp_path):
    """The primary key makes ambiguity structurally impossible: no query has to
    decide which of two rows for one folded name wins."""
    root = str(tmp_path)
    contact_db.replace_all(root, [
        a_contact(vendor_name="Danway Abu Dhabi L.L.C", emails=["a@x.example"]),
        a_contact(vendor_name="DANWAY  ABU DHABI L.L.C", emails=["b@x.example"]),
    ])
    assert contact_db.count(root) == 1


def test_reloading_the_registry_leaves_the_directory_alone(tmp_path):
    """V-D. The two live in one file. `bidder_db.replace_all` deletes from
    `bidders` and nothing else, and this is what keeps it that way."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    bidder_db.replace_all(root, [
        Bidder(id="bdr_1", name="Al Munara Switchgear LLC", approved_by=[ADNOC]),
    ])
    (stored,) = contact_db.list_all(root)
    assert stored.emails == ["sales@danway.example", "bids@danway.example"]


def test_replacing_the_directory_leaves_the_registry_alone(tmp_path):
    """V-D, pointing the other way — the copy nobody thinks to write."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [Bidder(id="bdr_1", name="Al Munara Switchgear LLC")])
    contact_db.replace_all(root, [a_contact()])
    assert bidder_db.count(root) == 1


def test_the_directory_is_read_back_in_folded_name_order(tmp_path):
    root = str(tmp_path)
    contact_db.replace_all(root, [
        a_contact(vendor_name="zeta", emails=["z@x.example"]),
        a_contact(vendor_name="Alpha", emails=["a@x.example"]),
    ])
    assert [c.vendor_name for c in contact_db.list_all(root)] == ["Alpha", "zeta"]


def test_an_emptied_directory_leaves_no_addresses_behind(tmp_path):
    """The zero case of V-A and V-B together. Replacing with nothing is a legal
    upload state only in tests, but the cascade is the same one a real
    replacement depends on."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    contact_db.replace_all(root, [])
    with contact_db.connect(root) as conn:
        assert conn.execute(
            "SELECT count(*) FROM vendor_contact_emails"
        ).fetchone()[0] == 0
    assert contact_db.list_all(root) == []
