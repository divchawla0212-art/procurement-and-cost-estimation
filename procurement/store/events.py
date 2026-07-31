"""Append-only audit log. Never rewritten, only appended to."""
import json
import os
import uuid

from procurement.store import layout
from procurement.store.models import Event


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def append_event(root: str, slug: str, event: Event) -> None:
    path = layout.events_path(root, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.model_dump(), default=str) + "\n")


def read_events(root: str, slug: str, run_id: str | None = None) -> list[Event]:
    path = layout.events_path(root, slug)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = Event.model_validate(json.loads(line))
            if run_id is None or event.run_id == run_id:
                out.append(event)
    return out
