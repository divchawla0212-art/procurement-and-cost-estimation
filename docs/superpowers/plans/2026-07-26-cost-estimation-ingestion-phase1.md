# Cost Estimation Ingestion (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the cost-estimation documents in `./data/cost-estimation-data` into one validated, provenance-tracked, structured `cost_dataset.json` of work packages, cost items, rate build-ups, and resource rates.

**Architecture:** Deterministic-Excel-first ingestion — `openpyxl` reads every number; a swappable, multi-provider `LLMClient` (Anthropic live-first; OpenAI/Gemini/mock) is used only to normalize messy/merged/bilingual sheet headers into canonical column roles. Cost-domain Pydantic models carry provenance on every figure. Reconciliation validates item arithmetic and work-package rollups against the workbooks' own Summary sheets.

**Tech Stack:** Python 3.12, `pydantic` v2, `openpyxl`, `anthropic` SDK, `pytest`, `pyyaml`.

## Global Constraints

- Python 3.12; `pydantic` v2 (use `model_config`, `model_dump`, `model_validate`).
- Client-agnostic: no client/project/site name in code, schema, or config. `./data/cost-estimation-data` is sample data only.
- Numbers are read deterministically via `openpyxl`; the LLM never emits or transforms a monetary or quantity value.
- Every derived figure carries a `ProvenanceRef` (document_path, sheet, cell).
- LLM provider is config-selected via `LLM_PROVIDER` (`anthropic|openai|gemini|mock`), `LLM_MODEL`, `*_API_KEY`. Switching providers = one env var, no code change.
- LLM-dependent tests run against `MockLLMClient`; live-provider tests skip when the relevant `*_API_KEY` is absent.
- Money/quantity equality checks use tolerance `abs(a - b) <= 0.01 * max(1, abs(b))`.
- TDD: write the failing test first, watch it fail, minimal implementation, watch it pass, commit.

---

### Task 1: Project scaffolding + shared ProvenanceRef

**Files:**
- Create: `pyproject.toml`
- Create: `shared/__init__.py`
- Create: `shared/provenance.py`
- Create: `cost_estimation/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Produces: `ProvenanceRef(document_path: str, sheet: str | None = None, cell: str | None = None, extractor: str, prompt_version: str | None = None)` — a frozen pydantic model reused by every later task.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "cost-estimation"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.6", "openpyxl>=3.1", "pyyaml>=6.0", "anthropic>=0.39"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
cost-est = "cost_estimation.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package files**

Create `shared/__init__.py`, `cost_estimation/__init__.py`, `tests/__init__.py` as empty files.

- [ ] **Step 3: Write the failing test**

```python
# tests/test_provenance.py
import pytest
from pydantic import ValidationError
from shared.provenance import ProvenanceRef


def test_provenance_holds_cell_location():
    ref = ProvenanceRef(document_path="a.xlsx", sheet="S1", cell="K12", extractor="xlsx")
    assert ref.cell == "K12"
    assert ref.prompt_version is None


def test_provenance_is_frozen():
    ref = ProvenanceRef(document_path="a.xlsx", extractor="xlsx")
    with pytest.raises(ValidationError):
        ref.cell = "A1"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.provenance'`

- [ ] **Step 5: Write minimal implementation**

```python
# shared/provenance.py
from pydantic import BaseModel, ConfigDict


class ProvenanceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_path: str
    sheet: str | None = None
    cell: str | None = None
    extractor: str
    prompt_version: str | None = None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_provenance.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml shared/ cost_estimation/__init__.py tests/__init__.py tests/test_provenance.py
git commit -m "feat: project scaffolding + shared ProvenanceRef"
```

---

### Task 2: LLM interface + mock adapter

**Files:**
- Create: `shared/llm/__init__.py`
- Create: `shared/llm/interface.py`
- Create: `shared/llm/mock_client.py`
- Test: `tests/test_mock_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class LLMClient(Protocol)` with attribute `supports_vision: bool` and method `classify_structure(self, prompt: str, output_schema: type[BaseModel], context_text: str, images: list | None = None) -> dict`.
  - `MockLLMClient(response: dict, supports_vision: bool = True)` — returns `response` (a copy) for any `classify_structure` call and records the last call on `self.last_call` (a dict with keys `prompt`, `context_text`).

- [ ] **Step 1: Create `shared/llm/__init__.py`** (empty file)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_mock_client.py
from pydantic import BaseModel
from shared.llm.mock_client import MockLLMClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_mock_returns_configured_response():
    canned = {"header_row": 7, "columns": {"code": 2, "quantity": 6}}
    client = MockLLMClient(response=canned)
    out = client.classify_structure("map this", _Layout, "row preview")
    assert out == canned
    assert _Layout.model_validate(out).header_row == 7


def test_mock_records_last_call():
    client = MockLLMClient(response={})
    client.classify_structure("PROMPT", _Layout, "PREVIEW")
    assert client.last_call["prompt"] == "PROMPT"
    assert client.last_call["context_text"] == "PREVIEW"


def test_mock_returns_a_copy():
    canned = {"columns": {"code": 2}}
    client = MockLLMClient(response=canned)
    out = client.classify_structure("p", _Layout, "c")
    out["columns"]["code"] = 999
    assert canned["columns"]["code"] == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_mock_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.llm.mock_client'`

- [ ] **Step 4: Write minimal implementation**

```python
# shared/llm/interface.py
from typing import Protocol, runtime_checkable
from pydantic import BaseModel


@runtime_checkable
class LLMClient(Protocol):
    supports_vision: bool

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict: ...
```

```python
# shared/llm/mock_client.py
import copy
from pydantic import BaseModel


class MockLLMClient:
    def __init__(self, response: dict, supports_vision: bool = True):
        self._response = response
        self.supports_vision = supports_vision
        self.last_call: dict | None = None

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict:
        self.last_call = {"prompt": prompt, "context_text": context_text}
        return copy.deepcopy(self._response)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_mock_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add shared/llm/ tests/test_mock_client.py
git commit -m "feat: LLMClient protocol + mock adapter"
```

---

### Task 3: Anthropic adapter

**Files:**
- Create: `shared/llm/anthropic_client.py`
- Test: `tests/test_anthropic_client.py`

**Interfaces:**
- Consumes: `LLMClient` protocol (Task 2).
- Produces: `AnthropicClient(model: str = "claude-opus-4-8", api_key: str | None = None)` implementing `classify_structure`; attribute `supports_vision = True`. Uses Anthropic tool-use: builds one tool whose `input_schema` is `output_schema.model_json_schema()` and returns the tool-use `input` dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_anthropic_client.py
import os
import pytest
from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.anthropic_client import AnthropicClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_anthropic_client_satisfies_protocol():
    client = AnthropicClient(api_key="dummy")
    assert isinstance(client, LLMClient)
    assert client.supports_vision is True


def test_build_tool_uses_schema():
    client = AnthropicClient(api_key="dummy")
    tool = client._build_tool(_Layout)
    assert tool["name"] == "emit_structure"
    assert "header_row" in tool["input_schema"]["properties"]


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_live_classify_structure_returns_dict():
    client = AnthropicClient()
    out = client.classify_structure(
        prompt="Return header_row=1 and empty columns.",
        output_schema=_Layout,
        context_text="col A | col B",
    )
    assert isinstance(out, dict)
    assert "header_row" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_anthropic_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.llm.anthropic_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/llm/anthropic_client.py
import os
from pydantic import BaseModel
import anthropic


class AnthropicClient:
    supports_vision = True

    def __init__(self, model: str = "claude-opus-4-8", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    def _build_tool(self, output_schema: type[BaseModel]) -> dict:
        return {
            "name": "emit_structure",
            "description": "Return the extracted structure.",
            "input_schema": output_schema.model_json_schema(),
        }

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict:
        client = anthropic.Anthropic(api_key=self._api_key)
        tool = self._build_tool(output_schema)
        message = client.messages.create(
            model=self.model,
            max_tokens=1024,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_structure"},
            messages=[{"role": "user", "content": f"{prompt}\n\n{context_text}"}],
        )
        for block in message.content:
            if block.type == "tool_use":
                return dict(block.input)
        raise RuntimeError("Anthropic response contained no tool_use block")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_anthropic_client.py -v`
Expected: PASS (2 passed, 1 skipped when no key)

- [ ] **Step 5: Commit**

```bash
git add shared/llm/anthropic_client.py tests/test_anthropic_client.py
git commit -m "feat: Anthropic LLM adapter (tool-use)"
```

---

### Task 4: OpenAI + Gemini adapters

**Files:**
- Create: `shared/llm/openai_client.py`
- Create: `shared/llm/gemini_client.py`
- Test: `tests/test_other_adapters.py`

**Interfaces:**
- Consumes: `LLMClient` protocol (Task 2).
- Produces:
  - `OpenAIClient(model: str = "gpt-4o", api_key: str | None = None)` with `supports_vision = True`; `classify_structure` maps `output_schema.model_json_schema()` into an OpenAI `response_format` JSON-schema request and returns the parsed dict.
  - `GeminiClient(model: str = "gemini-1.5-pro", api_key: str | None = None)` with `supports_vision = True`; `classify_structure` maps the schema into Gemini `responseSchema` and returns the parsed dict.
  - Both build the request lazily inside `classify_structure` so importing the module needs no SDK/key. A helper `_response_format(output_schema)` (OpenAI) and `_response_schema(output_schema)` (Gemini) are unit-tested without network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_other_adapters.py
from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.openai_client import OpenAIClient
from shared.llm.gemini_client import GeminiClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_adapters_satisfy_protocol():
    assert isinstance(OpenAIClient(api_key="x"), LLMClient)
    assert isinstance(GeminiClient(api_key="x"), LLMClient)


def test_openai_response_format_embeds_schema():
    rf = OpenAIClient(api_key="x")._response_format(_Layout)
    assert rf["type"] == "json_schema"
    assert "header_row" in rf["json_schema"]["schema"]["properties"]


def test_gemini_response_schema_is_schema():
    rs = GeminiClient(api_key="x")._response_schema(_Layout)
    assert "header_row" in rs["properties"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_other_adapters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.llm.openai_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/llm/openai_client.py
import os, json
from pydantic import BaseModel


class OpenAIClient:
    supports_vision = True

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")

    def _response_format(self, output_schema: type[BaseModel]) -> dict:
        return {
            "type": "json_schema",
            "json_schema": {"name": "structure", "schema": output_schema.model_json_schema()},
        }

    def classify_structure(self, prompt, output_schema, context_text, images=None) -> dict:
        from openai import OpenAI
        client = OpenAI(api_key=self._api_key)
        resp = client.chat.completions.create(
            model=self.model,
            response_format=self._response_format(output_schema),
            messages=[{"role": "user", "content": f"{prompt}\n\n{context_text}"}],
        )
        return json.loads(resp.choices[0].message.content)
```

```python
# shared/llm/gemini_client.py
import os, json
from pydantic import BaseModel


class GeminiClient:
    supports_vision = True

    def __init__(self, model: str = "gemini-1.5-pro", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")

    def _response_schema(self, output_schema: type[BaseModel]) -> dict:
        return output_schema.model_json_schema()

    def classify_structure(self, prompt, output_schema, context_text, images=None) -> dict:
        import google.generativeai as genai
        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(
            self.model,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": self._response_schema(output_schema),
            },
        )
        resp = model.generate_content(f"{prompt}\n\n{context_text}")
        return json.loads(resp.text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_other_adapters.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add shared/llm/openai_client.py shared/llm/gemini_client.py tests/test_other_adapters.py
git commit -m "feat: OpenAI + Gemini LLM adapters"
```

---

### Task 5: LLM factory (config-selected)

**Files:**
- Create: `shared/llm/factory.py`
- Test: `tests/test_llm_factory.py`

**Interfaces:**
- Consumes: all adapters (Tasks 2–4).
- Produces: `get_client() -> LLMClient` reading env `LLM_PROVIDER` (default `"mock"`), `LLM_MODEL` (optional). For `mock` it returns `MockLLMClient(response={})`. For `anthropic|openai|gemini` it returns the matching adapter (constructed, no network). Unknown provider raises `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_factory.py
import pytest
from shared.llm.factory import get_client
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient


def test_default_is_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_client(), MockLLMClient)


def test_selects_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    assert isinstance(get_client(), AnthropicClient)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError):
        get_client()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.llm.factory'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/llm/factory.py
import os
from shared.llm.interface import LLMClient
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.openai_client import OpenAIClient
from shared.llm.gemini_client import GeminiClient


def get_client() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    model = os.getenv("LLM_MODEL")
    kwargs = {"model": model} if model else {}
    if provider == "mock":
        return MockLLMClient(response={})
    if provider == "anthropic":
        return AnthropicClient(**kwargs)
    if provider == "openai":
        return OpenAIClient(**kwargs)
    if provider == "gemini":
        return GeminiClient(**kwargs)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm_factory.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add shared/llm/factory.py tests/test_llm_factory.py
git commit -m "feat: config-selected LLM factory"
```

---

### Task 6: Cost-domain Pydantic models

**Files:**
- Create: `cost_estimation/models/__init__.py`
- Create: `cost_estimation/models/schema.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `ProvenanceRef` (Task 1).
- Produces (all pydantic v2 models in `cost_estimation.models.schema`):
  - `class DocType(str, Enum)`: `SCHEDULE_OF_PRICES`, `COSTING_WORKBOOK`, `PROPOSAL_TEMPLATE`, `SCOPE_OF_WORK`.
  - `Document(path: str, doc_type: DocType, discipline: str | None = None, area: str | None = None, revision: str | None = None, extraction_status: str = "ok")`.
  - `Crew(role: str, count: int)`.
  - `ResourceRate(manhour_rate: float, currency: str = "USD", crew: list[Crew] = [])`.
  - `RateBuildUp(manhours: float | None = None, manhour_rate: float | None = None, equipment: float = 0.0, material_supply: float = 0.0, consumables: float = 0.0, installation: float = 0.0, unit_price: float, provenance: ProvenanceRef)`.
  - `CostItem(code: str, description: str, uom: str | None, quantity: float | None, rate_buildup: RateBuildUp | None = None, total: float | None = None, priced: bool = False, provenance: ProvenanceRef)`.
  - `SummaryRollup(total_manhours: float = 0.0, materials: float = 0.0, consumables: float = 0.0, installation: float = 0.0, total_value: float = 0.0)`.
  - `WorkPackage(name: str, discipline: str | None, area: str | None, cost_items: list[CostItem] = [], summary_rollup: SummaryRollup | None = None)`.
  - `CostDataset(documents: list[Document] = [], work_packages: list[WorkPackage] = [], resource_rates: list[ResourceRate] = [])`.

- [ ] **Step 1: Create `cost_estimation/models/__init__.py`** (empty file)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_models.py
from shared.provenance import ProvenanceRef
from cost_estimation.models.schema import (
    DocType, Document, CostItem, RateBuildUp, WorkPackage, CostDataset,
)


def _prov():
    return ProvenanceRef(document_path="c.xlsx", sheet="S", cell="K10", extractor="xlsx")


def test_priced_cost_item_roundtrips():
    item = CostItem(
        code="E.03.03.01.01", description="Conduit", uom="m", quantity=6,
        rate_buildup=RateBuildUp(unit_price=10.0, provenance=_prov()),
        total=60.0, priced=True, provenance=_prov(),
    )
    dumped = item.model_dump()
    assert CostItem.model_validate(dumped).total == 60.0


def test_blank_item_has_no_buildup():
    item = CostItem(code="E.01", description="x", uom="m", quantity=6, provenance=_prov())
    assert item.priced is False
    assert item.rate_buildup is None


def test_dataset_composes():
    ds = CostDataset(
        documents=[Document(path="c.xlsx", doc_type=DocType.COSTING_WORKBOOK)],
        work_packages=[WorkPackage(name="TF Main Elec.", discipline="electrical", area="TF")],
    )
    assert ds.documents[0].doc_type == DocType.COSTING_WORKBOOK
    assert ds.work_packages[0].area == "TF"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.models.schema'`

- [ ] **Step 4: Write minimal implementation**

```python
# cost_estimation/models/schema.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add cost_estimation/models/ tests/test_models.py
git commit -m "feat: cost-domain pydantic schema"
```

---

### Task 7: Discipline config + loader

**Files:**
- Create: `cost_estimation/config/__init__.py`
- Create: `cost_estimation/config/disciplines/default.yaml`
- Create: `cost_estimation/config/loader.py`
- Test: `tests/test_config_loader.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DisciplineConfig` pydantic model: `column_roles: dict[str, list[str]]`, `disciplines: dict[str, str]`, `areas: list[str]`, `currency_default: str`, `manhour_rate_default: float`.
  - `load_config(path: str | None = None) -> DisciplineConfig` — loads the given YAML, or the packaged `disciplines/default.yaml` when `path is None`.
  - `CANONICAL_ROLES: list[str]` = the ordered keys `["code","description","uom","quantity","labour","equipment","material","consumables","installation","unit_price","total"]`.

- [ ] **Step 1: Create `cost_estimation/config/__init__.py`** (empty file)

- [ ] **Step 2: Write `cost_estimation/config/disciplines/default.yaml`**

```yaml
column_roles:
  code: ["item", "item no", "revised item", "item no."]
  description: ["description", "item description"]
  uom: ["uom", "unit"]
  quantity: ["qty", "total quantity", "total quantity (q)"]
  labour: ["unitary manpower", "unitary manpower (labour)", "unitary labour"]
  equipment: ["unitary equipment"]
  material: ["unitary material", "unitary material (supply)", "materials", "material"]
  consumables: ["consumables"]
  installation: ["installation"]
  unit_price: ["unit price", "unit price (b+c+d)"]
  total: ["total", "total erec+mat", "total value", "total value (usd)"]
disciplines:
  "3A": civil
  "3B": steel
  "3C": mechanical
  "3D": electrical
  "3E": instrumentation
  "3F": insulation
  "3G": painting
areas: ["TF", "IA", "JPS", "JD", "IPS"]
currency_default: "USD"
manhour_rate_default: 70.0
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_config_loader.py
from cost_estimation.config.loader import load_config, CANONICAL_ROLES, DisciplineConfig


def test_loads_packaged_default():
    cfg = load_config()
    assert isinstance(cfg, DisciplineConfig)
    assert cfg.disciplines["3D"] == "electrical"
    assert "TF" in cfg.areas
    assert cfg.manhour_rate_default == 70.0


def test_canonical_roles_match_config_keys():
    cfg = load_config()
    assert set(CANONICAL_ROLES) == set(cfg.column_roles.keys())
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_config_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.config.loader'`

- [ ] **Step 5: Write minimal implementation**

```python
# cost_estimation/config/loader.py
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_config_loader.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add cost_estimation/config/ tests/test_config_loader.py
git commit -m "feat: discipline config + loader"
```

---

### Task 8: Document + sheet classifier

**Files:**
- Create: `cost_estimation/ingestion/__init__.py`
- Create: `cost_estimation/ingestion/classifier.py`
- Test: `tests/test_classifier.py`

**Interfaces:**
- Consumes: `DocType` (Task 6), `DisciplineConfig` (Task 7).
- Produces:
  - `classify_document(path: str, config: DisciplineConfig) -> Document` — infers `doc_type` from extension + filename keywords (`schedule of prices`→SCHEDULE_OF_PRICES, `costing`/`unit rates`→COSTING_WORKBOOK, `.docx`→PROPOSAL_TEMPLATE, `sow`/`scope of work`/`.pdf`→SCOPE_OF_WORK); `discipline` from filename keywords matched against `config.disciplines` values; `revision` from regex `rev[.\s-]*([0-9]+)` (case-insensitive).
  - `classify_sheet(sheet_name: str, config: DisciplineConfig) -> tuple[str | None, str | None]` — returns `(discipline, area)`; `area` is the first token in `config.areas` that the sheet name starts with (case-insensitive); `discipline` from the section code prefix (`3A`..`3G`) or a discipline keyword in the sheet name.

- [ ] **Step 1: Create `cost_estimation/ingestion/__init__.py`** (empty file)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_classifier.py
from cost_estimation.config.loader import load_config
from cost_estimation.models.schema import DocType
from cost_estimation.ingestion.classifier import classify_document, classify_sheet

CFG = load_config()


def test_classify_schedule_of_prices():
    doc = classify_document("Section-3 Schedule of Prices- Additional Tie-in Works.xlsx", CFG)
    assert doc.doc_type == DocType.SCHEDULE_OF_PRICES


def test_classify_costing_workbook_with_revision_and_discipline():
    doc = classify_document(
        "GDX-P-26-072 REV-00(1) COSTING A-6 (Electrical BOQs for Unit areas Imp. contractor) Rev.1 RK 20260622.xlsx",
        CFG,
    )
    assert doc.doc_type == DocType.COSTING_WORKBOOK
    assert doc.discipline == "electrical"
    assert doc.revision == "1"


def test_classify_proposal_and_sow():
    assert classify_document("GDX-P-26-072 REV-01 EA T-00935 JEBEL DHANNA.docx", CFG).doc_type == DocType.PROPOSAL_TEMPLATE
    assert classify_document("SOW-30201_50150_LT_HE-H4-000-69-00-002_Rev A.pdf", CFG).doc_type == DocType.SCOPE_OF_WORK


def test_classify_sheet_area_and_discipline():
    disc, area = classify_sheet("TF Main Elec. Equipment", CFG)
    assert area == "TF"
    disc2, area2 = classify_sheet("Sect. 3 D - Electrical", CFG)
    assert disc2 == "electrical"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.ingestion.classifier'`

- [ ] **Step 4: Write minimal implementation**

```python
# cost_estimation/ingestion/classifier.py
import re
import os
from cost_estimation.models.schema import Document, DocType
from cost_estimation.config.loader import DisciplineConfig

_REV = re.compile(r"rev[.\s\-]*([0-9]+)", re.IGNORECASE)
_SECTION = re.compile(r"\b3\s*([A-G])\b", re.IGNORECASE)


def _doc_type(name: str) -> DocType:
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if ext == ".docx":
        return DocType.PROPOSAL_TEMPLATE
    if "schedule of prices" in low:
        return DocType.SCHEDULE_OF_PRICES
    if "costing" in low or "unit rates" in low or "unit rate" in low:
        return DocType.COSTING_WORKBOOK
    if ext == ".pdf" or "sow" in low or "scope of work" in low:
        return DocType.SCOPE_OF_WORK
    return DocType.COSTING_WORKBOOK


def _discipline_from_text(text: str, config: DisciplineConfig) -> str | None:
    low = text.lower()
    for name in config.disciplines.values():
        if name in low:
            return name
    return None


def classify_document(path: str, config: DisciplineConfig) -> Document:
    name = os.path.basename(path)
    rev = _REV.search(name)
    return Document(
        path=path,
        doc_type=_doc_type(name),
        discipline=_discipline_from_text(name, config),
        revision=rev.group(1) if rev else None,
    )


def classify_sheet(sheet_name: str, config: DisciplineConfig) -> tuple[str | None, str | None]:
    name = sheet_name.strip()
    area = None
    for a in config.areas:
        if name.upper().startswith(a.upper()):
            area = a
            break
    discipline = _discipline_from_text(name, config)
    if discipline is None:
        m = _SECTION.search(name)
        if m:
            discipline = config.disciplines.get("3" + m.group(1).upper())
    return discipline, area
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_classifier.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add cost_estimation/ingestion/ tests/test_classifier.py
git commit -m "feat: document + sheet classifier"
```

---

### Task 9: Workbook loader (deterministic value reader)

**Files:**
- Create: `cost_estimation/ingestion/workbook_loader.py`
- Test: `tests/test_workbook_loader.py`
- Test fixture: `tests/fixtures/make_mini_boq.py`

**Interfaces:**
- Consumes: `CostItem`, `RateBuildUp` (Task 6), `ProvenanceRef` (Task 1).
- Produces:
  - `class SheetLayout(BaseModel)`: `header_row: int`, `columns: dict[str, int]` (canonical role → 1-based column index).
  - `read_items(worksheet, layout: SheetLayout, document_path: str, sheet_name: str) -> list[CostItem]` — iterates rows after `header_row`; a row is an item only if the `code` cell is non-empty; reads `quantity`, `unit_price`, `total` (and build-up columns when present); sets `priced=True` and populates `rate_buildup` only when a `unit_price` value is present and non-zero; provenance `cell` uses the `code` column, e.g. `"B14"`.

- [ ] **Step 1: Write the fixture generator**

```python
# tests/fixtures/make_mini_boq.py
"""Create a tiny in-memory-like BOQ workbook for deterministic tests."""
import openpyxl


def make(path: str) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sect. 3 D - Electrical"
    ws["B7"] = "Item"; ws["C7"] = "Description"; ws["E7"] = "UoM"
    ws["F7"] = "Total Quantity (Q)"; ws["K7"] = "Unit Price"; ws["L7"] = "TOTAL"
    # priced row
    ws["B8"] = "E.05.01"; ws["C8"] = "LV cable"; ws["E8"] = "m"
    ws["F8"] = 10; ws["K8"] = 5.0; ws["L8"] = 50.0
    # blank-priced row (qty only)
    ws["B9"] = "E.05.02"; ws["C9"] = "HV cable"; ws["E9"] = "m"
    ws["F9"] = 20; ws["K9"] = 0; ws["L9"] = 0
    # non-item row (header/section, no code)
    ws["C10"] = "SUBTOTAL"
    wb.save(path)
    return path
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_workbook_loader.py
import openpyxl
from tests.fixtures.make_mini_boq import make
from cost_estimation.ingestion.workbook_loader import SheetLayout, read_items


def test_read_items_splits_priced_and_blank(tmp_path):
    path = make(str(tmp_path / "mini.xlsx"))
    ws = openpyxl.load_workbook(path, data_only=True)["Sect. 3 D - Electrical"]
    layout = SheetLayout(header_row=7, columns={"code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12})
    items = read_items(ws, layout, path, "Sect. 3 D - Electrical")

    assert len(items) == 2  # SUBTOTAL row skipped (no code)
    priced = next(i for i in items if i.code == "E.05.01")
    assert priced.priced is True
    assert priced.total == 50.0
    assert priced.rate_buildup.unit_price == 5.0
    assert priced.provenance.cell == "B8"

    blank = next(i for i in items if i.code == "E.05.02")
    assert blank.priced is False
    assert blank.rate_buildup is None
    assert blank.quantity == 20
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_workbook_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.ingestion.workbook_loader'`

- [ ] **Step 4: Write minimal implementation**

```python
# cost_estimation/ingestion/workbook_loader.py
from openpyxl.utils import get_column_letter
from pydantic import BaseModel
from shared.provenance import ProvenanceRef
from cost_estimation.models.schema import CostItem, RateBuildUp


class SheetLayout(BaseModel):
    header_row: int
    columns: dict[str, int]


def _num(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def read_items(worksheet, layout: SheetLayout, document_path: str, sheet_name: str) -> list[CostItem]:
    cols = layout.columns
    code_col = cols.get("code")
    items: list[CostItem] = []
    for row in range(layout.header_row + 1, worksheet.max_row + 1):
        code = worksheet.cell(row, code_col).value if code_col else None
        if code is None or str(code).strip() == "":
            continue

        def cell(role):
            c = cols.get(role)
            return worksheet.cell(row, c).value if c else None

        prov = ProvenanceRef(
            document_path=document_path, sheet=sheet_name,
            cell=f"{get_column_letter(code_col)}{row}", extractor="xlsx",
        )
        unit_price = _num(cell("unit_price"))
        total = _num(cell("total"))
        priced = unit_price is not None and unit_price != 0
        buildup = None
        if priced:
            buildup = RateBuildUp(
                equipment=_num(cell("equipment")) or 0.0,
                material_supply=_num(cell("material")) or 0.0,
                consumables=_num(cell("consumables")) or 0.0,
                installation=_num(cell("installation")) or 0.0,
                unit_price=unit_price,
                provenance=prov,
            )
        items.append(CostItem(
            code=str(code).strip(),
            description=str(cell("description") or "").strip(),
            uom=(str(cell("uom")).strip() if cell("uom") else None),
            quantity=_num(cell("quantity")),
            rate_buildup=buildup,
            total=total,
            priced=priced,
            provenance=prov,
        ))
    return items
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_workbook_loader.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add cost_estimation/ingestion/workbook_loader.py tests/test_workbook_loader.py tests/fixtures/
git commit -m "feat: deterministic workbook value reader"
```

---

### Task 10: Header mapper (LLM structure normalization)

**Files:**
- Create: `cost_estimation/ingestion/header_mapper.py`
- Create: `shared/llm/prompts/header_map_v1.txt`
- Test: `tests/test_header_mapper.py`

**Interfaces:**
- Consumes: `LLMClient` (Task 2), `SheetLayout` (Task 9), `DisciplineConfig` (Task 7).
- Produces:
  - `sheet_preview(worksheet, rows: int = 15, cols: int = 20) -> str` — a plain-text grid preview (`"R<row> | <col_letter>=<value>"` per non-empty cell) of the first `rows` rows.
  - `map_sheet(worksheet, client: LLMClient, config: DisciplineConfig, sheet_name: str) -> SheetLayout` — builds the preview, loads `header_map_v1.txt`, calls `client.classify_structure(prompt, SheetLayout, preview)`, and returns `SheetLayout.model_validate(result)`.

- [ ] **Step 1: Write the prompt file**

```text
# shared/llm/prompts/header_map_v1.txt
You are given a text preview of a Bill-of-Quantities spreadsheet.
Identify the single header row and the 1-based column index for each canonical
role that is present: code, description, uom, quantity, labour, equipment,
material, consumables, installation, unit_price, total.
Return header_row (integer) and columns (a mapping of role -> column index).
Omit roles that are not present. Do NOT read or return any data values.
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_header_mapper.py
import openpyxl
from tests.fixtures.make_mini_boq import make
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.header_mapper import sheet_preview, map_sheet
from shared.llm.mock_client import MockLLMClient


def test_sheet_preview_includes_header_tokens(tmp_path):
    path = make(str(tmp_path / "mini.xlsx"))
    ws = openpyxl.load_workbook(path, data_only=True).active
    preview = sheet_preview(ws)
    assert "Total Quantity (Q)" in preview
    assert "R7" in preview


def test_map_sheet_returns_layout_from_client(tmp_path):
    path = make(str(tmp_path / "mini.xlsx"))
    ws = openpyxl.load_workbook(path, data_only=True).active
    canned = {"header_row": 7, "columns": {"code": 2, "quantity": 6, "unit_price": 11, "total": 12}}
    client = MockLLMClient(response=canned)
    layout = map_sheet(ws, client, load_config(), "Sect. 3 D - Electrical")
    assert layout.header_row == 7
    assert layout.columns["quantity"] == 6
    assert "Sect. 3 D - Electrical" not in client.last_call["prompt"]  # prompt is generic
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_header_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.ingestion.header_mapper'`

- [ ] **Step 4: Write minimal implementation**

```python
# cost_estimation/ingestion/header_mapper.py
from pathlib import Path
from openpyxl.utils import get_column_letter
from shared.llm.interface import LLMClient
from cost_estimation.config.loader import DisciplineConfig
from cost_estimation.ingestion.workbook_loader import SheetLayout

_PROMPT = Path(__file__).parents[2] / "shared" / "llm" / "prompts" / "header_map_v1.txt"


def sheet_preview(worksheet, rows: int = 15, cols: int = 20) -> str:
    lines = []
    for row in range(1, min(rows, worksheet.max_row) + 1):
        cells = []
        for col in range(1, min(cols, worksheet.max_column) + 1):
            value = worksheet.cell(row, col).value
            if value is not None:
                cells.append(f"{get_column_letter(col)}={value}")
        if cells:
            lines.append(f"R{row} | " + " | ".join(cells))
    return "\n".join(lines)


def map_sheet(worksheet, client: LLMClient, config: DisciplineConfig, sheet_name: str) -> SheetLayout:
    prompt = _PROMPT.read_text(encoding="utf-8")
    preview = sheet_preview(worksheet)
    result = client.classify_structure(prompt, SheetLayout, preview)
    return SheetLayout.model_validate(result)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_header_mapper.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add cost_estimation/ingestion/header_mapper.py shared/llm/prompts/ tests/test_header_mapper.py
git commit -m "feat: LLM-assisted header mapper"
```

---

### Task 11: Reconciliation validation

**Files:**
- Create: `cost_estimation/ingestion/reconcile.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `CostItem`, `WorkPackage`, `SummaryRollup` (Task 6).
- Produces:
  - `class Discrepancy(BaseModel)`: `kind: str`, `location: str`, `expected: float`, `actual: float`.
  - `check_item(item: CostItem) -> Discrepancy | None` — for priced items, returns a `Discrepancy(kind="item_total")` when `total` differs from `quantity * unit_price` beyond tolerance; `None` otherwise or when unpriced.
  - `check_rollup(wp: WorkPackage) -> Discrepancy | None` — returns a `Discrepancy(kind="rollup")` when the sum of item `total`s differs from `wp.summary_rollup.total_value` beyond tolerance; `None` when no rollup or within tolerance.
  - `TOL = 0.01` used as `abs(a - b) <= TOL * max(1, abs(b))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reconcile.py
from shared.provenance import ProvenanceRef
from cost_estimation.models.schema import CostItem, RateBuildUp, WorkPackage, SummaryRollup
from cost_estimation.ingestion.reconcile import check_item, check_rollup


def _prov():
    return ProvenanceRef(document_path="c.xlsx", extractor="xlsx")


def _item(qty, price, total):
    return CostItem(code="X", description="d", uom="m", quantity=qty,
                    rate_buildup=RateBuildUp(unit_price=price, provenance=_prov()),
                    total=total, priced=True, provenance=_prov())


def test_item_arithmetic_ok():
    assert check_item(_item(10, 5.0, 50.0)) is None


def test_item_arithmetic_mismatch():
    d = check_item(_item(10, 5.0, 999.0))
    assert d is not None and d.kind == "item_total"
    assert d.expected == 50.0


def test_rollup_matches_summary():
    wp = WorkPackage(name="w", cost_items=[_item(10, 5.0, 50.0), _item(2, 10.0, 20.0)],
                     summary_rollup=SummaryRollup(total_value=70.0))
    assert check_rollup(wp) is None


def test_rollup_mismatch():
    wp = WorkPackage(name="w", cost_items=[_item(10, 5.0, 50.0)],
                     summary_rollup=SummaryRollup(total_value=999.0))
    d = check_rollup(wp)
    assert d is not None and d.kind == "rollup"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.ingestion.reconcile'`

- [ ] **Step 3: Write minimal implementation**

```python
# cost_estimation/ingestion/reconcile.py
from pydantic import BaseModel
from cost_estimation.models.schema import CostItem, WorkPackage

TOL = 0.01


class Discrepancy(BaseModel):
    kind: str
    location: str
    expected: float
    actual: float


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL * max(1.0, abs(b))


def check_item(item: CostItem) -> Discrepancy | None:
    if not item.priced or item.rate_buildup is None or item.quantity is None or item.total is None:
        return None
    expected = item.quantity * item.rate_buildup.unit_price
    if _close(expected, item.total):
        return None
    return Discrepancy(kind="item_total", location=item.code, expected=expected, actual=item.total)


def check_rollup(wp: WorkPackage) -> Discrepancy | None:
    if wp.summary_rollup is None:
        return None
    total = sum(i.total for i in wp.cost_items if i.total is not None)
    expected = wp.summary_rollup.total_value
    if _close(total, expected):
        return None
    return Discrepancy(kind="rollup", location=wp.name, expected=expected, actual=total)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reconcile.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add cost_estimation/ingestion/reconcile.py tests/test_reconcile.py
git commit -m "feat: item + rollup reconciliation"
```

---

### Task 12: Extractor (orchestration)

**Files:**
- Create: `cost_estimation/ingestion/extractor.py`
- Test: `tests/test_extractor.py`

**Interfaces:**
- Consumes: `classify_document`, `classify_sheet` (Task 8), `map_sheet` (Task 10), `read_items` (Task 9), `LLMClient` (Task 2), `DisciplineConfig` (Task 7), models (Task 6).
- Produces:
  - `ingest_workbook(path: str, client: LLMClient, config: DisciplineConfig) -> tuple[Document, list[WorkPackage]]` — classifies the document; for each worksheet whose name is not a summary/cover sheet (name lower-cased not in `{"summary", "cover", "notes"}`), maps its layout via `map_sheet`, reads items via `read_items`, and builds a `WorkPackage` with `(discipline, area)` from `classify_sheet`. Sheets that yield zero items are skipped.
  - `ingest_directory(root: str, client: LLMClient, config: DisciplineConfig) -> CostDataset` — walks `root`, classifies every file; `.xlsx` files that are `COSTING_WORKBOOK` or `SCHEDULE_OF_PRICES` are workbook-ingested; `PROPOSAL_TEMPLATE`/`SCOPE_OF_WORK` are recorded as `Document`s only (indexed, not parsed). Returns a `CostDataset`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extractor.py
import openpyxl
from tests.fixtures.make_mini_boq import make
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.extractor import ingest_workbook
from shared.llm.mock_client import MockLLMClient


def test_ingest_workbook_builds_workpackage(tmp_path):
    path = make(str(tmp_path / "COSTING mini Electrical.xlsx"))
    client = MockLLMClient(response={"header_row": 7, "columns": {
        "code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12}})
    doc, packages = ingest_workbook(path, client, load_config())

    assert doc.doc_type.value == "costing_workbook"
    assert len(packages) == 1
    wp = packages[0]
    assert wp.discipline == "electrical"
    assert len(wp.cost_items) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.ingestion.extractor'`

- [ ] **Step 3: Write minimal implementation**

```python
# cost_estimation/ingestion/extractor.py
import os
import openpyxl
from shared.llm.interface import LLMClient
from cost_estimation.config.loader import DisciplineConfig
from cost_estimation.models.schema import Document, DocType, WorkPackage, CostDataset
from cost_estimation.ingestion.classifier import classify_document, classify_sheet
from cost_estimation.ingestion.header_mapper import map_sheet
from cost_estimation.ingestion.workbook_loader import read_items

_SKIP_SHEETS = {"summary", "cover", "notes"}
_WORKBOOK_TYPES = {DocType.COSTING_WORKBOOK, DocType.SCHEDULE_OF_PRICES}


def ingest_workbook(path: str, client: LLMClient, config: DisciplineConfig) -> tuple[Document, list[WorkPackage]]:
    document = classify_document(path, config)
    # NOTE: not read_only — read_items/sheet_preview use random .cell() access,
    # which is unreliable in openpyxl read-only mode (max_row can be None).
    wb = openpyxl.load_workbook(path, data_only=True)
    packages: list[WorkPackage] = []
    for ws in wb.worksheets:
        if ws.title.strip().lower() in _SKIP_SHEETS:
            continue
        layout = map_sheet(ws, client, config, ws.title)
        items = read_items(ws, layout, path, ws.title)
        if not items:
            continue
        discipline, area = classify_sheet(ws.title, config)
        packages.append(WorkPackage(name=ws.title.strip(), discipline=discipline, area=area, cost_items=items))
    return document, packages


def ingest_directory(root: str, client: LLMClient, config: DisciplineConfig) -> CostDataset:
    dataset = CostDataset()
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if fname.startswith("~$"):
                continue
            path = os.path.join(dirpath, fname)
            doc = classify_document(path, config)
            if doc.doc_type in _WORKBOOK_TYPES and fname.lower().endswith(".xlsx"):
                document, packages = ingest_workbook(path, client, config)
                dataset.documents.append(document)
                dataset.work_packages.extend(packages)
            else:
                dataset.documents.append(doc)
    return dataset
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extractor.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add cost_estimation/ingestion/extractor.py tests/test_extractor.py
git commit -m "feat: ingestion orchestrator"
```

---

### Task 13: CLI + end-to-end dataset output

**Files:**
- Create: `cost_estimation/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ingest_directory` (Task 12), `get_client` (Task 5), `load_config` (Task 7).
- Produces:
  - `run_ingest(root: str, out_path: str) -> CostDataset` — uses `get_client()` and `load_config()`, ingests `root`, writes `dataset.model_dump()` as JSON to `out_path`, returns the dataset.
  - `main(argv: list[str] | None = None) -> int` — argparse CLI: `cost-est ingest <root> --out <path>` (default out `cost_dataset.json`). Returns process exit code `0` on success.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json
from tests.fixtures.make_mini_boq import make
from cost_estimation.cli import run_ingest, main


def test_run_ingest_writes_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")  # deterministic, no key
    # mock returns {} -> header mapper yields no columns -> zero items,
    # so seed the layout by pointing the CLI's client at a canned response:
    from cost_estimation import cli
    from shared.llm.mock_client import MockLLMClient
    monkeypatch.setattr(cli, "get_client", lambda: MockLLMClient(response={
        "header_row": 7, "columns": {"code": 2, "description": 3, "uom": 5,
                                     "quantity": 6, "unit_price": 11, "total": 12}}))

    make(str(tmp_path / "COSTING mini Electrical.xlsx"))
    out = tmp_path / "out.json"
    ds = run_ingest(str(tmp_path), str(out))

    assert out.exists()
    data = json.loads(out.read_text())
    assert len(data["work_packages"]) == 1
    assert len(ds.work_packages[0].cost_items) == 2


def test_main_returns_zero(tmp_path, monkeypatch):
    from cost_estimation import cli
    from shared.llm.mock_client import MockLLMClient
    monkeypatch.setattr(cli, "get_client", lambda: MockLLMClient(response={
        "header_row": 7, "columns": {"code": 2, "quantity": 6, "unit_price": 11, "total": 12}}))
    make(str(tmp_path / "COSTING mini Electrical.xlsx"))
    code = main(["ingest", str(tmp_path), "--out", str(tmp_path / "o.json")])
    assert code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_estimation.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# cost_estimation/cli.py
import argparse
import json
from shared.llm.factory import get_client
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.extractor import ingest_directory
from cost_estimation.models.schema import CostDataset


def run_ingest(root: str, out_path: str) -> CostDataset:
    client = get_client()
    config = load_config()
    dataset = ingest_directory(root, client, config)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(dataset.model_dump(), fh, indent=2, default=str)
    return dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cost-est")
    sub = parser.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("root")
    ing.add_argument("--out", default="cost_dataset.json")
    args = parser.parse_args(argv)
    if args.command == "ingest":
        ds = run_ingest(args.root, args.out)
        print(f"Ingested {len(ds.work_packages)} work packages -> {args.out}")
        return 0
    return 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: all tests pass (live Anthropic test skipped when no key).

- [ ] **Step 6: Commit**

```bash
git add cost_estimation/cli.py tests/test_cli.py
git commit -m "feat: cost-est ingest CLI + JSON dataset output"
```

---

### Task 14: Real-data smoke test + reconciliation on sample fixtures

**Files:**
- Test: `tests/test_real_data.py`

**Interfaces:**
- Consumes: `ingest_workbook` (Task 12), `check_item`, `check_rollup` (Task 11), `load_config` (Task 7).
- Produces: an integration test proving the pipeline runs against the real `./data/cost-estimation-data` workbooks using the mock client with a hand-supplied layout, and that arithmetic reconciliation holds on the priced rows it reads. This test is `skipif` the sample data is absent so CI without the data still passes.

> Note: the real sheets have varied layouts; this test targets the `Sect. 3 D - Electrical` sheet of the Schedule of Prices (header row 7, quantity col F=6) to exercise real parsing without depending on a live LLM. It asserts the pipeline produces items and that any priced item passes `check_item`.

- [ ] **Step 1: Write the test**

```python
# tests/test_real_data.py
import os
import openpyxl
import pytest
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.workbook_loader import SheetLayout, read_items
from cost_estimation.ingestion.reconcile import check_item

DATA = "data/cost-estimation-data/Section-3 Schedule of Prices- Additional Tie-in Works.xlsx"


@pytest.mark.skipif(not os.path.exists(DATA), reason="sample data not present")
def test_real_schedule_of_prices_electrical_parses_and_reconciles():
    ws = openpyxl.load_workbook(DATA, data_only=True)["Sect. 3 D - Electrical"]
    layout = SheetLayout(header_row=7, columns={
        "code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12})
    items = read_items(ws, layout, DATA, "Sect. 3 D - Electrical")

    assert len(items) > 0
    # Blank-priced template: every priced item must still reconcile arithmetically.
    for item in items:
        assert check_item(item) is None
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_real_data.py -v`
Expected: PASS (1 passed) when sample data present, else skipped.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -v`
Expected: all pass (Anthropic-live and real-data tests skip when their prerequisites are absent).

- [ ] **Step 4: Commit**

```bash
git add tests/test_real_data.py
git commit -m "test: real-data smoke + reconciliation on sample Schedule of Prices"
```

---

## Notes for the implementer

- **Run order:** Tasks are dependency-ordered 1→14; implement in sequence.
- **Windows/paths:** tests use `tmp_path` and repo-relative `data/...`; run pytest from the repo root.
- **No network in CI:** only the two guarded tests (`ANTHROPIC_API_KEY`, sample-data presence) touch real providers/data; everything else uses the mock client and generated fixtures.
- **Deferred to later phases (do NOT build here):** rate library, pricing the blank Schedule of Prices, discipline/project roll-up beyond echoing as-stated Summary values, `.docx` proposal generation, SOW method-of-measurement enforcement, resource-rate extraction from the costing Summary sheets (schema exists; population is a later phase).

## Known coverage note (from self-review vs spec §7.2)

Spec §7.2 makes **work-package-sum == workbook Summary rollup** a hard requirement. This plan builds and unit-tests the reconciliation *mechanism* (`check_rollup`, Task 11) and wires item-arithmetic reconciliation on real data (Task 14), but the extractor does **not** yet populate `WorkPackage.summary_rollup` from the real costing-workbook `SUMMARY` sheets, so end-to-end rollup reconciliation on real data is not exercised. The real `SUMMARY` labels (e.g. `TF Cable and Access.`) do not match sheet titles (`TF Cables and Access. `) exactly, so populating them needs a normalized-name match step.

**Options:** (a) add a Task 12.5 that parses each costing workbook's `SUMMARY` sheet into `{normalized_label: SummaryRollup}` and attaches rollups to work packages by normalized-name match, then a real-data rollup reconciliation test; or (b) accept the documented deferral for Phase 1 (mechanism proven, population deferred to the pricing/roll-up phase). Confirm the choice before execution.
