# tests/test_store_snapshots.py
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
