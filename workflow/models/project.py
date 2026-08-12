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


def new_item_id() -> str:
    return f"itm_{uuid4().hex[:8]}"


class Item(BaseModel):
    """A procurement item within a project. `item_type` references the shared
    catalogue so cross-project questions do not rely on free-text matching."""

    id: str = Field(default_factory=new_item_id)
    project_id: str
    item_type: str
    description: str
    qty: float
    uom: str
    discipline: str
    estimated_value_aed: int
    required_on_site: date | None = None
    is_long_lead: bool = False
