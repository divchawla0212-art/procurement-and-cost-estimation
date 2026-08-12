import json
import os
import threading
import pytest
from procurement.store import layout


def test_atomic_write_creates_file(tmp_path):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})
    assert json.load(open(p)) == {"x": 1}
    assert not os.path.exists(p + ".tmp")


def test_atomic_write_leaves_previous_intact_on_failure(tmp_path, monkeypatch):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(layout.os, "replace", boom)
    with pytest.raises(OSError):
        layout.atomic_write_json(p, {"x": 2})

    # the previous good copy must survive an interrupted write
    assert json.load(open(p)) == {"x": 1}


def test_failed_serialization_leaves_no_orphan_tmp(tmp_path):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})
    circular: dict = {}
    circular["self"] = circular          # json.dump raises mid-write
    with pytest.raises(ValueError):
        layout.atomic_write_json(p, circular)
    # Asserted over the whole directory, not `p + ".tmp"`. Temp names are unique
    # per write since BUG-008, so the old `not os.path.exists(p + ".tmp")` form
    # passed no matter what the cleanup did — it named a file the writer never
    # creates any more. Mutation-checked: deleting the os.remove in
    # atomic_write_json's error path must fail this test.
    assert os.listdir(tmp_path) == ["a.json"], \
        f"a half-written temp must not be left behind: {sorted(os.listdir(tmp_path))}"
    assert json.load(open(p)) == {"x": 1}


def test_atomic_write_fsyncs_before_replacing(tmp_path, monkeypatch):
    # os.replace() is only crash-atomic if the new contents are on disk first;
    # without the fsync a power loss can leave a truncated file in place.
    p = str(tmp_path / "a.json")
    order: list[str] = []
    real_fsync, real_replace = layout.os.fsync, layout.os.replace
    monkeypatch.setattr(layout.os, "fsync", lambda fd: order.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(layout.os, "replace", lambda s, d: order.append("replace") or real_replace(s, d))
    layout.atomic_write_json(p, {"x": 1})
    assert order == ["fsync", "replace"]


# --------------------------------------------------------------------------
# BUG-008: two writers of one path must not be able to interleave.
#
# The temp name used to be derived from the destination (`path + ".tmp"`), so
# concurrent writers of the same snapshot shared one temp file: the second
# open(..., "w") truncated what the first was still writing, and both then
# renamed the result into place. Measured before the fix, 25 of 25 two-thread
# races left a destination holding records from BOTH writers - valid JSON,
# wrong content, undetectable downstream.
# --------------------------------------------------------------------------

def _race_two_writers(path, payload_a, payload_b, rounds):
    """Run `rounds` two-thread races against one path.

    Returns (errors, landings): every exception a writer raised, and what the
    destination held after EACH round.

    Reading the destination once at the end is not enough, and quietly missed
    the defect this file exists to catch: a round that lands a corrupt file is
    overwritten by the next round, so a 15-round test reports only round 15.
    Mutation testing caught that - reinstating the shared temp name left the
    interleaving test green. Every round is inspected instead.
    """
    errors: list[Exception] = []
    landings: list[object] = []

    def writer(payload):
        start.wait()
        try:
            layout.atomic_write_json(path, payload)
        except Exception as exc:                      # noqa: BLE001 - reported
            errors.append(exc)

    for _ in range(rounds):
        start = threading.Barrier(2)
        threads = [threading.Thread(target=writer, args=(payload_a,)),
                   threading.Thread(target=writer, args=(payload_b,))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        try:
            with open(path, encoding="utf-8") as fh:
                landings.append(json.load(fh))
        except FileNotFoundError:
            landings.append("MISSING")
        except json.JSONDecodeError as exc:
            landings.append(f"CORRUPT: {exc.msg}")

    return errors, landings


def test_concurrent_writers_never_interleave_into_one_file(tmp_path):
    # Big enough that json.dump spans many write() calls - a small payload can
    # land in a single buffer flush and hide the race.
    a = [{"tag": "A" * 200, "i": i} for i in range(2000)]
    b = [{"tag": "B" * 200, "i": i} for i in range(2000)]
    path = str(tmp_path / "documents.json")

    _errors, landings = _race_two_writers(path, a, b, rounds=15)

    for i, landed in enumerate(landings):
        assert landed in (a, b), (
            f"round {i}: destination is not exactly one writer's payload - "
            + (landed if isinstance(landed, str)
               else f"holds tags {sorted({r['tag'][0] for r in landed})}"))


def test_concurrent_writers_do_not_fail_each_other(tmp_path):
    # Sharing one temp name also made writers destroy each other's file: the
    # loser raised FileNotFoundError (POSIX) or PermissionError (Windows) from
    # its own os.replace, surfacing as a 502 from the ingest endpoint.
    a = [{"tag": "A" * 200, "i": i} for i in range(2000)]
    b = [{"tag": "B" * 200, "i": i} for i in range(2000)]
    path = str(tmp_path / "documents.json")

    errors, _landings = _race_two_writers(path, a, b, rounds=15)

    assert not errors, \
        f"a concurrent writer failed: {[type(e).__name__ for e in errors]}"


def test_every_concurrent_writer_renames_its_own_temp_away(tmp_path):
    """The loser of a race must not leave its temp file behind.

    Unique temp names mean each writer creates its own file; the one whose
    os.replace lands second still has to have moved its file, not abandoned it.
    Thirty writes here, so an accumulating leak shows up as a directory with
    more than the one snapshot in it.

    This guards the SUCCESS path only. Cleanup on the *failure* path is
    `test_failed_serialization_leaves_no_orphan_tmp` — no writer fails here
    once the race is fixed, so this test cannot cover it, and an earlier
    version of it that claimed to was vacuous.
    """
    a = [{"tag": "A" * 200, "i": i} for i in range(1500)]
    b = [{"tag": "B" * 200, "i": i} for i in range(1500)]
    path = str(tmp_path / "documents.json")

    _race_two_writers(path, a, b, rounds=15)

    assert os.listdir(tmp_path) == ["documents.json"], \
        f"stray files left behind: {sorted(os.listdir(tmp_path))}"


def test_read_json_missing_returns_default(tmp_path):
    assert layout.read_json(str(tmp_path / "nope.json"), default=[]) == []


def test_content_sha256_is_stable_and_content_sensitive(tmp_path):
    f = tmp_path / "d.bin"
    f.write_bytes(b"hello")
    first = layout.content_sha256(str(f))
    assert first == layout.content_sha256(str(f))
    f.write_bytes(b"hello!")
    assert layout.content_sha256(str(f)) != first


def test_doc_id_is_path_addressed_not_content_addressed():
    # same path -> same id regardless of content; different vendor -> different id
    a = layout.doc_id_for("KERUI", "vendors/KERUI/Quote.pdf")
    b = layout.doc_id_for("KERUI", "vendors/KERUI/Quote.pdf")
    c = layout.doc_id_for("ADPOWER", "vendors/ADPOWER/Quote.pdf")
    assert a == b and a != c
    assert len(a) == 12


def test_doc_id_for_rfq_document():
    assert layout.doc_id_for(None, "requirements/MR.pdf") != layout.doc_id_for("KERUI", "requirements/MR.pdf")
