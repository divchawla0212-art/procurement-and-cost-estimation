from procurement.project import create_project
from procurement.store import events
from procurement.store.models import Event


def _ev(run_id, action, target=None):
    return Event(at="2026-07-29T10:00:00Z", run_id=run_id, actor="pipeline",
                 action=action, target=target)


def test_events_append_in_order(tmp_path):
    create_project(str(tmp_path), "P")
    events.append_event(str(tmp_path), "p", _ev("r1", "run.started"))
    events.append_event(str(tmp_path), "p", _ev("r1", "document.extracted", "d1"))
    got = events.read_events(str(tmp_path), "p")
    assert [e.action for e in got] == ["run.started", "document.extracted"]


def test_events_filter_by_run(tmp_path):
    create_project(str(tmp_path), "P")
    events.append_event(str(tmp_path), "p", _ev("r1", "run.started"))
    events.append_event(str(tmp_path), "p", _ev("r2", "run.started"))
    assert len(events.read_events(str(tmp_path), "p", run_id="r2")) == 1


def test_read_events_empty_when_absent(tmp_path):
    create_project(str(tmp_path), "P")
    assert events.read_events(str(tmp_path), "p") == []


def test_appending_never_rewrites_earlier_lines(tmp_path):
    create_project(str(tmp_path), "P")
    events.append_event(str(tmp_path), "p", _ev("r1", "run.started"))
    first_line = open(events.layout.events_path(str(tmp_path), "p"),
                      encoding="utf-8").readline()
    events.append_event(str(tmp_path), "p", _ev("r1", "run.finished"))
    assert open(events.layout.events_path(str(tmp_path), "p"),
                encoding="utf-8").readline() == first_line


def test_run_ids_are_unique():
    assert events.new_run_id() != events.new_run_id()
