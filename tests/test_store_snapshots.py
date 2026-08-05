# tests/test_store_snapshots.py
import threading
import time

import pytest

from procurement.project import create_project
from procurement.store import snapshots
from procurement.store.models import DocumentRecord, VendorFacts


def _doc(i="a" * 12):
    return DocumentRecord(doc_id=i, path=f"vendors/K/{i}.pdf", vendor="K",
                          content_sha256="0" * 64)


def test_documents_round_trip(tmp_path):
    create_project(str(tmp_path), "P")
    snapshots.save_documents(str(tmp_path), "p", [_doc()])
    got = snapshots.load_documents(str(tmp_path), "p")
    assert len(got) == 1 and got[0].doc_id == "a" * 12


def test_load_documents_empty_when_absent(tmp_path):
    create_project(str(tmp_path), "P")
    assert snapshots.load_documents(str(tmp_path), "p") == []


def test_facts_round_trip_per_vendor(tmp_path):
    create_project(str(tmp_path), "P")
    snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="KERUI",
                                                         commercial={"base_price": 5.0}))
    assert snapshots.load_facts(str(tmp_path), "p", "KERUI").commercial == {"base_price": 5.0}
    assert snapshots.load_facts(str(tmp_path), "p", "NOPE") is None
    assert snapshots.list_fact_vendors(str(tmp_path), "p") == ["KERUI"]


def test_saves_alone_do_not_bump_generation(tmp_path):
    create_project(str(tmp_path), "P")
    before = snapshots.get_generation(str(tmp_path), "p")
    snapshots.save_documents(str(tmp_path), "p", [_doc()])
    snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="K"))
    assert snapshots.get_generation(str(tmp_path), "p") == before


def test_transaction_bumps_generation_exactly_once(tmp_path):
    create_project(str(tmp_path), "P")
    before = snapshots.get_generation(str(tmp_path), "p")
    with snapshots.transaction(str(tmp_path), "p"):
        snapshots.save_documents(str(tmp_path), "p", [_doc()])
        snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="K"))
        snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="A"))
    assert snapshots.get_generation(str(tmp_path), "p") == before + 1


def test_failed_transaction_does_not_bump_generation(tmp_path):
    create_project(str(tmp_path), "P")
    before = snapshots.get_generation(str(tmp_path), "p")
    try:
        with snapshots.transaction(str(tmp_path), "p"):
            snapshots.save_documents(str(tmp_path), "p", [_doc()])
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert snapshots.get_generation(str(tmp_path), "p") == before


def test_update_facts_writes_only_what_the_body_assigned(tmp_path):
    """A field the body never touches keeps the value stored when the lock was
    taken -- this is the invariant BUG-010 broke."""
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K", commercial={"vendor": "K", "base_price": 100.0},
        technical_feedback="a reviewer's note"))

    with snapshots.update_facts(root, "p", "K") as facts:
        facts.commercial = {"vendor": "K", "base_price": 200.0}

    stored = snapshots.load_facts(root, "p", "K")
    assert stored.commercial["base_price"] == 200.0
    assert stored.technical_feedback == "a reviewer's note"


def test_update_facts_re_reads_inside_the_lock(tmp_path):
    """Two threads, each mutating a different field. Without the re-read
    happening inside the lock, the slower one writes the other's field back to
    its pre-lock value."""
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="K"))
    start = threading.Barrier(2)

    def set_feedback():
        start.wait()
        with snapshots.update_facts(root, "p", "K") as f:
            f.technical_feedback = "note"

    def set_commercial():
        start.wait()
        with snapshots.update_facts(root, "p", "K") as f:
            f.commercial = {"vendor": "K", "base_price": 5.0}

    threads = [threading.Thread(target=set_feedback),
               threading.Thread(target=set_commercial)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stored = snapshots.load_facts(root, "p", "K")
    assert stored.technical_feedback == "note"
    assert stored.commercial["base_price"] == 5.0


def test_update_facts_refuses_an_unknown_vendor_by_default(tmp_path):
    root = str(tmp_path)
    with pytest.raises(LookupError):
        with snapshots.update_facts(root, "p", "NOBODY"):
            pass
    assert snapshots.load_facts(root, "p", "NOBODY") is None


def test_update_facts_creates_when_asked(tmp_path):
    root = str(tmp_path)
    with snapshots.update_facts(root, "p", "K", create=True) as facts:
        facts.commercial = {"vendor": "K", "base_price": 1.0}
    assert snapshots.load_facts(root, "p", "K").commercial["base_price"] == 1.0


def test_a_raising_body_writes_nothing(tmp_path):
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="K",
                                                technical_feedback="before"))
    with pytest.raises(RuntimeError):
        with snapshots.update_facts(root, "p", "K") as facts:
            facts.technical_feedback = "after"
            raise RuntimeError("boom")
    assert snapshots.load_facts(root, "p", "K").technical_feedback == "before"


def test_update_facts_does_not_bump_generation(tmp_path):
    """generation bumps once per transaction, never once per file."""
    root = str(tmp_path)
    create_project(str(tmp_path), "P")
    snapshots.save_facts(root, "p", VendorFacts(vendor="K"))
    before = snapshots.get_generation(root, "p")
    for i in range(3):
        with snapshots.update_facts(root, "p", "K") as facts:
            facts.quotation_doc_id = f"d{i}"
    assert snapshots.get_generation(root, "p") == before


def test_two_vendors_do_not_serialise_against_each_other(tmp_path):
    """The lock is per vendor, not per project: a run must not block itself.

    A per-project lock cannot get both threads inside their `update_facts`
    bodies at once, so the losing thread blocks on lock acquisition before it
    ever reaches the barrier -- the winner's `both_inside.wait()` then times
    out alone and raises `BrokenBarrierError`. Thread targets swallow
    unhandled exceptions (they do not propagate to the main thread and do not
    fail a bare `is_alive()` check), so this test must capture them itself and
    must bound wall-clock time: with a per-project lock this can only finish
    by timing out, which takes close to the barrier's `timeout=5`.
    """
    root = str(tmp_path)
    for v in ("A", "B"):
        snapshots.save_facts(root, "p", VendorFacts(vendor=v))
    both_inside = threading.Barrier(2, timeout=5)
    errors = []

    def hold(vendor):
        try:
            with snapshots.update_facts(root, "p", vendor) as f:
                both_inside.wait()      # times out (per-project) or proceeds (per-vendor)
                f.quotation_doc_id = vendor
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=hold, args=(v,)) for v in ("A", "B")]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    elapsed = time.monotonic() - started

    assert all(not t.is_alive() for t in threads)
    assert errors == []
    assert elapsed < 2, f"took {elapsed:.1f}s -- the barrier likely timed out"
    assert snapshots.load_facts(root, "p", "A").quotation_doc_id == "A"
    assert snapshots.load_facts(root, "p", "B").quotation_doc_id == "B"
