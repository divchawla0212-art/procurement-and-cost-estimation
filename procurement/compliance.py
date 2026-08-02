"""(requirements, facts, deviations) -> one verdict per requirement x vendor.

`unanswered` is never `fail`. Missing evidence means the pipeline did not read
enough, not that the vendor failed; collapsing the two is how a compliance
review becomes indefensible. It doubles as the extraction-coverage metric.

The matrix is recomputed wholesale on every call. There is no incremental
path, so there is no way for a verdict to outlive the requirement or the fact
it was computed from.
"""
import hashlib
import re
from datetime import datetime, timezone

from procurement import units
from procurement.project import load_project
from procurement.store import snapshots
from procurement.store.models import ComplianceResult, RequirementSet

VERDICTS = ("pass", "fail", "deviation", "unanswered", "review")

_NOISE = re.compile(r"[^a-z0-9]+")
_MAX_CANDIDATES = 5


def _norm(text: str | None) -> str:
    return _NOISE.sub("", (text or "").lower())


def _norm_clause(ref: str | None) -> str:
    return _NOISE.sub("", (ref or "").lower().replace("clause", ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def vocabulary(reqset: RequirementSet) -> list[str]:
    """The parameter names the datasheet pass will be asked to look for.

    Both checked tiers contribute. Omitting `stated` would mean never asking a
    datasheet for `generator_insulation_class`, so every stated row would land
    on `unanswered` and the tier would measure nothing.
    """
    return sorted({r.parameter for r in reqset.requirements
                   if r.checkability in ("auto", "stated")
                   and r.parameter and not r.withdrawn})


def vocabulary_sha(params: list[str]) -> str:
    """Fingerprint of the vocabulary, part of the datasheet cache key. Sorted
    so a reordering is not a change; an empty vocabulary still hashes."""
    joined = "\n".join(sorted(params))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _stated_matches(required, stated) -> bool:
    """True when every token of the required value appears in the stated one.

    Subset, not equality: a datasheet prints "Class F / Class B rise" for a
    clause requiring "Class F", and equality would fail every real vendor.
    """
    req_tokens = {t for t in _NOISE.split(str(required).lower()) if t}
    got_tokens = {t for t in _NOISE.split(str(stated).lower()) if t}
    return bool(req_tokens) and req_tokens <= got_tokens


def evaluate(requirement, facts: list[dict], deviations: list[dict],
             vendor: str, now: str) -> ComplianceResult:
    """One cell. Verdict order is the whole design; see the module docstring
    and the plan's Task 6 for why each step precedes the next."""
    def result(verdict, rationale, fact=None):
        return ComplianceResult(
            req_id=requirement.req_id, vendor=vendor, verdict=verdict,
            fact_id=(fact or {}).get("fact_id"), doc_id=(fact or {}).get("doc_id"),
            rationale=rationale, evaluated_at=now)

    want_clause = _norm_clause(requirement.clause_ref)
    deviated = next((d for d in deviations
                     if _norm_clause(d.get("clause_ref")) == want_clause
                     and d.get("disposition") == "deviate"), None)
    if deviated is not None:
        # The vendor has said in writing that it does not comply. A `comply`
        # disposition earns nothing: an assertion of compliance is not
        # evidence of it.
        return result("deviation",
                      f"vendor declared a deviation: {deviated.get('statement')}")

    if requirement.checkability == "stated":
        want = _norm(requirement.parameter)
        fact = next((f for f in facts if _norm(f.get("parameter")) == want), None)
        if fact is None:
            # the same rule `auto` follows: not having read the answer is not
            # the vendor having answered wrongly
            return result("unanswered",
                          f"no vendor document stated {requirement.parameter!r}")
        got = fact.get("value")
        if requirement.value is None:
            return result("pass",
                          f"vendor states {requirement.parameter} = {got!r}", fact)
        if _stated_matches(requirement.value, got):
            return result("pass",
                          f"vendor states {requirement.parameter} = {got!r}, "
                          f"which carries the required {requirement.value!r}", fact)
        # never `fail`: "Class H" is better insulation than "Class F", and a
        # token comparison cannot know that. Cite both and let a human decide.
        return result("review",
                      f"required {requirement.parameter} = {requirement.value!r}; "
                      f"vendor states {got!r}", fact)

    if requirement.checkability != "auto":
        candidates = [f"{f.get('parameter')}={f.get('value')} {f.get('unit') or ''}".strip()
                      for f in facts[:_MAX_CANDIDATES]]
        statements = [d.get("statement") for d in deviations
                      if _norm_clause(d.get("clause_ref")) == want_clause]
        return result("review",
                      f"human judgement required: {requirement.text}"
                      + (f" | candidate facts: {candidates}" if candidates else "")
                      + (f" | vendor statements: {statements}" if statements else ""))

    want = _norm(requirement.parameter)
    fact = next((f for f in facts if _norm(f.get("parameter")) == want), None)
    if fact is None:
        # Never `fail`: the pipeline not having read the answer is not the
        # vendor having answered wrongly.
        return result("unanswered",
                      f"no vendor document stated {requirement.parameter!r}")

    try:
        ok, why = units.compare(requirement.operator, requirement.value,
                                requirement.unit, fact.get("value"),
                                fact.get("unit"), parameter=requirement.parameter)
    except units.Unconvertible as exc:
        # The evidence was found; only the comparison could not be made. Cite
        # the fact so a human can finish the check by hand.
        return result("unanswered", str(exc), fact)
    return result("pass" if ok else "fail", why, fact)


def evaluate_project(root: str, slug: str, now: str | None = None
                     ) -> list[ComplianceResult]:
    """Recompute and store the whole requirement x vendor matrix."""
    now = now or _now()
    reqset = snapshots.load_requirements(root, slug)
    live = [r for r in reqset.requirements if not r.withdrawn]
    # Vendors come from the project, not from the facts directory: a vendor
    # whose extraction failed entirely must still show a column of
    # `unanswered`, not vanish from the matrix.
    vendors = load_project(root, slug).vendors

    out: list[ComplianceResult] = []
    for vendor in vendors:
        stored = snapshots.load_facts(root, slug, vendor)
        facts = list(stored.technical) if stored else []
        deviations = list(stored.deviations) if stored else []
        for requirement in live:
            out.append(evaluate(requirement, facts, deviations, vendor, now))
    snapshots.save_compliance(root, slug, out)
    return out
