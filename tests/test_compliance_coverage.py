"""INV-10: `unanswered` means the vendor did not state it.

The coverage percentage is what spec section 7 reads as an extraction metric,
so a cell that is `unanswered` because our own unit table could not read a unit
the requirements themselves state is measuring the wrong thing. Two refusals
look identical in the data and are opposites in meaning:

    "unrecognised unit '%'"                we cannot read the corpus  -> defect
    "cannot compare 'barg' with 'bar'"     two different quantities   -> correct

One row per unit the live corpus states in a requirement, with the vendor
answer it was actually compared against on 2026-07-31.
"""
import pytest

from procurement.compliance import evaluate_project
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (RequirementRecord, RequirementSet,
                                      VendorFacts, req_id_for)

NOW = "2026-07-31T00:00:00+00:00"

_CORPUS = [
    ("sustained_overload_current", ">=", 110.0, "%", 300.0, None, "pass"),
    ("warranty_period", "==", 12.0, "months", 12.0, "Months", "pass"),
    ("noise_limit", "<=", 85.0, "dBA", 85.0, "dB(A) at 1m", "pass"),
    ("fuel_gas_pressure", ">=", 2.76, "barg", 3.5, "barg", "pass"),
    ("generator_breaker_rating", ">=", 1250.0, "Amp", 1250.0, "Amp", "pass"),
    ("particulate_matter_limit", "<=", 10.0, "mg/Nm3", 10.0, "mg/Nm3", "pass"),
    ("altitude", "<=", 1000.0, "m", 900.0, None, "pass"),
    ("short_circuit_withstand_time", "==", 3.0, "s", 3.0, None, "pass"),
    ("battery_charger_input_voltage", "==", 230.0, "VAC", 230.0, "V", "pass"),
    ("rated_capacity", "==", 525.0, "kW", 550.0, "kW@ 55 Deg C", "fail"),
    ("winding_insulation_class", "==", "H", "", "Class H", "", "pass"),
    # refusals that must survive, each for a stated physical reason
    ("heater_rating", "<=", 10.0, "kW", 220.0, "V", "unanswered"),
    ("preservation_duration", ">=", 6.0, "months", 26.0, "weeks", "unanswered"),
    # a percentage against an absolute: no unit table can reconcile these,
    # because the base the percentage is of is never stated
    ("excitation_rated_current", ">=", 110.0, "%", 957.0, "A", "unanswered"),
]


def _clause(index: int) -> str:
    return f"{index}.1"


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)

    requirements, facts = [], []
    for i, (param, op, value, unit, fact_value, fact_unit, _) in enumerate(_CORPUS):
        clause = _clause(i)
        requirements.append(RequirementRecord(
            req_id=req_id_for("d1", clause), clause_ref=clause,
            text=f"{param} {op} {value} {unit}", checkability="auto",
            parameter=param, operator=op, value=value, unit=unit,
            source_doc_id="d1"))
        facts.append({"fact_id": f"f-{i}", "parameter": param,
                      "value": fact_value, "unit": fact_unit,
                      "verbatim": f"{fact_value} {fact_unit}", "doc_id": "d9"})
    snapshots.save_requirements(root, "p", RequirementSet(requirements=requirements))
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI", technical=facts))
    return root


@pytest.mark.parametrize("param,op,value,unit,fact_value,fact_unit,expected", _CORPUS)
def test_each_live_unit_reaches_its_expected_verdict(
        tmp_path, param, op, value, unit, fact_value, fact_unit, expected):
    root = _project(tmp_path)
    cells = {c.req_id: c for c in evaluate_project(root, "p", now=NOW)}
    index = [row[0] for row in _CORPUS].index(param)
    cell = cells[req_id_for("d1", _clause(index))]
    assert cell.verdict == expected, cell.rationale


def test_no_cell_is_unanswered_merely_because_a_unit_was_unrecognised(tmp_path):
    # INV-10, over both loaded snapshots at once
    root = _project(tmp_path)
    evaluate_project(root, "p", now=NOW)
    reqs = snapshots.load_requirements(root, "p").requirements
    stated = {r.unit for r in reqs if r.checkability == "auto"}
    for cell in snapshots.load_compliance(root, "p"):
        assert "unrecognised unit" not in cell.rationale, (
            f"{cell.rationale} - but the requirements state {sorted(stated)}")


def test_a_surviving_refusal_always_says_why(tmp_path):
    root = _project(tmp_path)
    evaluate_project(root, "p", now=NOW)
    for cell in snapshots.load_compliance(root, "p"):
        if cell.verdict != "unanswered":
            continue
        assert any(reason in cell.rationale for reason in (
            "no vendor document stated", "different quantities",
            "molar mass", "is not a number", "states no value")), cell.rationale


def test_only_the_deliberate_refusals_are_unanswered(tmp_path):
    root = _project(tmp_path)
    cells = evaluate_project(root, "p", now=NOW)
    unanswered = sorted(c.rationale for c in cells if c.verdict == "unanswered")
    assert len(unanswered) == 3, unanswered


def test_a_parameter_no_vendor_document_states_is_still_unanswered(tmp_path):
    """The metric's one true reading. Without this row the suite could be
    satisfied by a build that never returns `unanswered` at all."""
    root = _project(tmp_path)
    facts = snapshots.load_facts(root, "p", "KERUI")
    facts.technical = [f for f in facts.technical
                       if f["parameter"] != "warranty_period"]
    snapshots.save_facts(root, "p", facts)

    cells = {c.req_id: c for c in evaluate_project(root, "p", now=NOW)}
    index = [row[0] for row in _CORPUS].index("warranty_period")
    cell = cells[req_id_for("d1", _clause(index))]
    assert cell.verdict == "unanswered"
    assert "no vendor document stated" in cell.rationale
