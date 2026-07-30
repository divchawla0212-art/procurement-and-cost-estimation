from pydantic import BaseModel
from shared.provenance import ProvenanceRef


class OptionalItem(BaseModel):
    description: str
    qty: float | None = None
    unit_price: float | None = None
    total: float | None = None
    category: str = "other"
    included_in_base: bool = False


class BidExtraction(BaseModel):
    currency: str = ""
    base_price: float = 0.0
    # Nullable, unlike base_price: these are new, so nothing depends on them
    # defaulting to a number, and None is what "the quotation did not break
    # the base scope into units" actually means. base_price keeps its 0.0
    # default only because normalize.py multiplies it — see the open
    # escalation in the phase ledger.
    base_qty: float | None = None
    base_unit_price: float | None = None
    vat_included: bool = False
    vat_rate: float = 0.0
    freight_amount: float = 0.0
    freight_included: bool = False
    discount_pct: float = 0.0
    optional_items: list[OptionalItem] = []
    delivery_time: str | None = None
    delivery_terms: str | None = None
    payment_terms: str | None = None
    engine_make: str | None = None
    notes: str | None = None


class VendorBid(BidExtraction):
    vendor: str
    source_document: str | None = None
    provenance: ProvenanceRef | None = None
    extraction_status: str = "ok"


class NormalizationAdjustment(BaseModel):
    kind: str
    description: str
    from_value: float
    to_value: float
    delta: float


class NormalizedBid(BaseModel):
    vendor: str
    normalized_currency: str
    normalized_total: float | None
    adjustments: list[NormalizationAdjustment] = []
    extraction_status: str = "ok"


class ComparisonRow(BaseModel):
    vendor: str
    currency: str
    raw_base_price: float | None
    normalized_total: float | None
    delivery_terms: str | None
    delivery_time: str | None
    payment_terms: str | None
    engine_make: str | None
    extraction_status: str


class ComparisonTable(BaseModel):
    target_currency: str
    rows: list[ComparisonRow] = []


class Project(BaseModel):
    name: str
    slug: str
    created_at: str
    target_currency: str = "USD"
    fx_rates: dict[str, float] = {}
    vendors: list[str] = []
    requirements_file: str | None = None
    store_version: int = 1
    generation: int = 0
    status: str = "new"
