from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ProjectStatus = Literal["Active", "On Hold", "Closed"]


def new_project_id() -> str:
    return f"prj_{uuid4().hex[:8]}"


class Project(BaseModel):
    """A project is the top-level container. `name` is user-editable at any
    time; `id` never changes, and every reference elsewhere binds to `id`."""

    id: str = Field(default_factory=new_project_id)
    name: str
    code: str
    client: str
    location: str
    live_period_start: date
    live_period_end: date
    currency: str = "AED"
    status: ProjectStatus = "Active"
