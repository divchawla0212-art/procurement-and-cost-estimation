"""BUG-009: concurrent mutations of project.json must not lose each other.

`save_project` writes the whole document, and every mutation is a
load-modify-save. Two of them overlapping means the later save writes back
fields it read before the earlier one changed them - so an FX-rate write that
returned 200 was silently discarded by a run finishing in the same window.

The tests below force that window deterministically rather than racing for it:
a hook holds one writer between its load and its save while the other completes.
"""
import io
import threading
import zipfile

from fastapi.testclient import TestClient

from tests.auth_helpers import signed_in_admin

_BODY = (b"unit price 1000 USD. This synthetic fixture body is padded with "
         b"filler prose so its character count clears the pipeline's "
         b"minimum-extractable-text guard, letting the extraction logic "
         b"under test run rather than the guard itself.")


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def _second_client(client) -> TestClient:
    """A second transport sharing `client`'s session.

    These tests deliberately drive the racing writer through its own
    `TestClient`, so the two requests cannot be serialised by the transport.
    That still has to carry a session now that every non-public `/api/` path
    requires one — and it has to be the *same* session, since signing up a
    second admin would collide on the email.
    """
    import api.main as api_main
    other = TestClient(api_main.app)
    other.cookies.update(client.cookies)
    return other


def _hold_first_project_write(monkeypatch, match=None):
    """Hold a project.json write open, between its read and its write.

    Hooks `layout.atomic_write_json` rather than `save_project`: pipeline.py
    does `from procurement.project import save_project`, so patching the
    function on `procurement.project` never reaches its caller — and once the
    mutation is serialised, `save_project` is not the call the run goes through
    at all. Every project.json write ends up here regardless, which is what
    makes this hook survive the fix it is written against.

    `match` picks *which* write to hold, given the document about to be
    written; the default holds the first. It exists because a run writes
    project.json more than once — `bump_generation` on the transaction's exit,
    then `status` at the end — and holding the first one silently tested only
    the former. Mutation testing caught that: reverting the run's status write
    to an unlocked load/save pair failed no test.

    Returns (in_gap, may_proceed).
    """
    from procurement.store import layout

    real_write = layout.atomic_write_json
    in_gap, may_proceed = threading.Event(), threading.Event()
    held = {"done": False}

    def holding_write(path, data):
        if (str(path).endswith("project.json") and not held["done"]
                and (match is None or match(data))):
            held["done"] = True
            in_gap.set()
            assert may_proceed.wait(timeout=30), "test never released the held write"
        return real_write(path, data)

    monkeypatch.setattr(layout, "atomic_write_json", holding_write)
    return in_gap, may_proceed


# A run's terminal status, set once at the very end of run_ingestion. A project
# that has never been ingested sits at "new", so this matches the run's own
# status write and nothing earlier in the same run.
_TERMINAL = {"done", "done_with_failures", "failed"}


def _project_with_vendors(client) -> None:
    client.post("/api/projects", json={"name": "P"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for vendor in ("KERUI", "ADPOWER"):
            zf.writestr(f"{vendor}/quote.txt", _BODY)
            zf.writestr(f"{vendor}/sheet.txt", b"Continuous rating 550 kW. " + _BODY)
    client.post("/api/projects/p/vendors",
                files={"file": ("bids.zip", buf.getvalue(), "application/zip")})


def test_an_fx_write_during_a_run_is_not_lost(tmp_path, monkeypatch):
    """The reported case, forced.

    A run is held inside its own project.json read-modify-write; an FX-rate
    PUT is issued in that gap. Whichever order the two serialise in, a PUT that
    reports success must be the state left on disk.
    """
    import api.main as api_main
    import procurement.project as proj

    client = _client(tmp_path, monkeypatch)
    _project_with_vendors(client)

    # Hold the run at *its status write* — the one the reported defect named
    # (pipeline.py, end of run_ingestion), not the earlier `generation` bump.
    # No prior ingest, so the project is still "new" and only the run's own
    # status write carries a terminal value.
    in_gap, may_proceed = _hold_first_project_write(
        monkeypatch, match=lambda doc: doc.get("status") in _TERMINAL)

    run_result: list = []
    runner = threading.Thread(target=lambda: run_result.append(
        client.post("/api/projects/p/ingest")))
    runner.start()

    fx_result: list = []

    def put_rates():
        fx_result.append(_second_client(client).put(
            "/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}}))

    try:
        assert in_gap.wait(timeout=60), "the run never reached its save"
        setter = threading.Thread(target=put_rates)
        setter.start()
        # Give the FX write a bounded chance to land *while the run is held*.
        # Unserialised it completes here, and the run's pending write then
        # overwrites it — the defect. Serialised it blocks on the lock the run
        # holds, this join times out, and it applies after the run instead.
        # Either way the run is released next, so this cannot deadlock.
        setter.join(timeout=2)
        may_proceed.set()
        setter.join(timeout=60)
    finally:
        may_proceed.set()
        runner.join(timeout=60)

    assert fx_result and fx_result[0].status_code == 200, \
        f"the FX write did not succeed: {fx_result}"
    assert proj.load_project(str(tmp_path), "p").fx_rates == {"EUR": 1.08}, \
        "an FX write that returned 200 was discarded by the concurrent run"


def test_concurrent_field_writes_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """Two different fields, two writers, neither may lose the other.

    `fx_rates` and `requirements_file` are written by different endpoints that
    both save the whole document, so this fails on the same mechanism without
    any ingestion involved.
    """
    import api.main as api_main
    import procurement.project as proj

    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})

    in_gap, may_proceed = _hold_first_project_write(monkeypatch)

    first: list = []
    t1 = threading.Thread(target=lambda: first.append(
        _second_client(client).put("/api/projects/p/fx-rates",
                                   json={"rates": {"EUR": 1.08}})))
    t1.start()
    assert in_gap.wait(timeout=30), "the first write never reached its save"

    second: list = []
    t2 = threading.Thread(target=lambda: second.append(
        _second_client(client).post(
            "/api/projects/p/requirements",
            files={"file": ("MR.docx", b"4.2.7 H2S at least 50 ppm",
                            "application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document")})))
    t2.start()
    # Same bounded wait as the test above, and for the same reason: let the
    # second write land while the first is held, so an unserialised pair
    # actually loses one of the two fields instead of depending on which
    # thread the scheduler happens to run first.
    t2.join(timeout=2)
    may_proceed.set()
    t2.join(timeout=60)
    t1.join(timeout=60)

    assert first and first[0].status_code == 200, f"fx write failed: {first}"
    assert second and second[0].status_code == 200, f"requirements upload failed: {second}"

    project = proj.load_project(str(tmp_path), "p")
    assert project.fx_rates == {"EUR": 1.08}, "the FX rates were overwritten"
    assert project.requirements_file == "MR.docx", "the requirements file was overwritten"


def test_concurrent_generation_bumps_are_not_lost(tmp_path):
    """`generation` bumps once per transaction — including when transactions
    from different callers overlap.

    Unlocked, this is the textbook lost update: both readers see N and both
    write N+1, so two transactions advance the counter once. Measured at 25 of
    25 trials before the fix, with only two threads.
    """
    from procurement.project import create_project, load_project
    from procurement.store import snapshots

    root = str(tmp_path)
    create_project(root, "P")

    n = 8
    start = threading.Barrier(n)
    errors: list = []

    def bump():
        start.wait()
        try:
            snapshots.bump_generation(root, "p")
        except Exception as exc:                       # noqa: BLE001 - reported
            errors.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"a bump raised: {[type(e).__name__ for e in errors]}"
    assert load_project(root, "p").generation == n, \
        "concurrent transactions lost a generation bump"


def test_a_vendor_upload_during_another_write_is_not_lost(tmp_path, monkeypatch):
    """The vendor roster is written the same load-modify-save way.

    Losing it is worse than losing an FX rate: `run_ingestion`'s stale-vendor
    sweep deletes stored facts for vendors the project no longer lists, so a
    dropped roster entry can take a vendor's extracted facts with it.
    """
    import api.main as api_main
    import procurement.project as proj

    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})

    in_gap, may_proceed = _hold_first_project_write(monkeypatch)

    first: list = []
    t1 = threading.Thread(target=lambda: first.append(
        _second_client(client).put("/api/projects/p/fx-rates",
                                   json={"rates": {"EUR": 1.08}})))
    t1.start()
    assert in_gap.wait(timeout=30), "the first write never reached its save"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("KERUI/quote.txt", _BODY)
    second: list = []
    t2 = threading.Thread(target=lambda: second.append(
        _second_client(client).post(
            "/api/projects/p/vendors",
            files={"file": ("bids.zip", buf.getvalue(), "application/zip")})))
    t2.start()
    t2.join(timeout=2)
    may_proceed.set()
    t2.join(timeout=60)
    t1.join(timeout=60)

    assert first and first[0].status_code == 200, f"fx write failed: {first}"
    assert second and second[0].status_code == 200, f"vendor upload failed: {second}"

    project = proj.load_project(str(tmp_path), "p")
    assert project.fx_rates == {"EUR": 1.08}, "the FX rates were overwritten"
    assert project.vendors == ["KERUI"], \
        f"the vendor roster was overwritten: {project.vendors}"
