from enum import Enum
from pydantic import BaseModel
from shared.provenance import ProvenanceRef


class DocType(str, Enum):
    SCHEDULE_OF_PRICES = "schedule_of_prices"
    COSTING_WORKBOOK = "costing_workbook"
    PROPOSAL_TEMPLATE = "proposal_template"
    SCOPE_OF_WORK = "scope_of_work"


class Document(BaseModel):
    path: str
    doc_type: DocType
    discipline: str | None = None
    area: str | None = None
    revision: str | None = None
    extraction_status: str = "ok"


class Crew(BaseModel):
    role: str
    count: int


class ResourceRate(BaseModel):
    manhour_rate: float
    currency: str = "USD"
    crew: list[Crew] = []


class RateBuildUp(BaseModel):
    manhours: float | None = None
    manhour_rate: float | None = None
    equipment: float = 0.0
    material_supply: float = 0.0
    consumables: float = 0.0
    installation: float = 0.0
    unit_price: float
    provenance: ProvenanceRef


class CostItem(BaseModel):
    code: str
    description: str
    uom: str | None = None
    quantity: float | None = None
    rate_buildup: RateBuildUp | None = None
    total: float | None = None
    priced: bool = False
    provenance: ProvenanceRef


class SummaryRollup(BaseModel):
    total_manhours: float = 0.0
    materials: float = 0.0
    consumables: float = 0.0
    installation: float = 0.0
    total_value: float = 0.0


class WorkPackage(BaseModel):
    name: str
    discipline: str | None = None
    area: str | None = None
    cost_items: list[CostItem] = []
    summary_rollup: SummaryRollup | None = None


class CostDataset(BaseModel):
    documents: list[Document] = []
    work_packages: list[WorkPackage] = []
    resource_rates: list[ResourceRate] = []
