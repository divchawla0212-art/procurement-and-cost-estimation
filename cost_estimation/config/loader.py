from pathlib import Path
import yaml
from pydantic import BaseModel

CANONICAL_ROLES = [
    "code", "description", "uom", "quantity", "labour", "equipment",
    "material", "consumables", "installation", "unit_price", "total",
]


class DisciplineConfig(BaseModel):
    column_roles: dict[str, list[str]]
    disciplines: dict[str, str]
    areas: list[str]
    currency_default: str
    manhour_rate_default: float


def load_config(path: str | None = None) -> DisciplineConfig:
    if path is None:
        path = str(Path(__file__).parent / "disciplines" / "default.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return DisciplineConfig.model_validate(data)
