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


def _tokens(value) -> set[str]:
    return {t for t in _NOISE.split(str(value).lower()) if t}


def _stated_matches(required, stated) -> bool:
    """True when every token of the required value appears in the stated one.

    Subset, not equality: a datasheet prints "Class F / Class B rise" for a
    clause requiring "Class F", and equality would fail every real vendor.

    The limitation the subset carries, and why `_negations_in` guards it: a
    subset is blind to the sentence the tokens sit in, so "Class B rise (Class F
    insulation not offered)" is a superset of {class, f} and reads here as a
    match. Every caller must therefore run `_negations_in` before turning a
    match into a `pass`, and downgrade to `review` when it finds anything.
    """
    req_tokens = _tokens(required)
    got_tokens = _tokens(stated)
    return bool(req_tokens) and req_tokens <= got_tokens


# Words a stated value uses to say the thing is not offered. Deliberately short
# and literal - every entry has to be a word that reverses the sentence it sits
# in. A wrong entry costs a reviewer one row to clear; a missing one clears a
# vendor on a string diff, which is the failure this tier exists not to make.
_NEGATIONS = frozenset({"not", "no", "none", "nil", "without", "excluded",
                        "excluding", "except", "unavailable"})

# "n/a" tokenizes to {"n", "a"}, and "n" alone is far too common a token to
# treat as a word, so the joined form is matched on the raw string instead.
_NOT_APPLICABLE = re.compile(r"\bn\s*[/.\-]?\s*a\b")


def _negations_in(required, stated) -> list[str]:
    """The negation words the vendor's value carries and the requirement does not.

    Subtracting the requirement's own tokens is what lets a clause requiring
    "no asbestos" be satisfied by a vendor stating "No asbestos used": the "no"
    there is the clause's own word echoed back, not a refusal.

    Broad on purpose - any negation anywhere in the value, not just one next to
    the matched tokens. A positional rule over tokenized prose would be a
    confident guess, and the two mistakes are not equal: a false trigger costs
    a `review` row, a miss books an award against a vendor who said no.
    """
    req_tokens = _tokens(required) if required is not None else set()
    found = (_tokens(stated) & _NEGATIONS) - req_tokens
    if (_NOT_APPLICABLE.search(str(stated).lower())
            and not (required is not None
                     and _NOT_APPLICABLE.search(str(required).lower()))):
        found = found | {"n/a"}
    return sorted(found)


def _states_something(value) -> bool:
    """False for `None` and for a value that tokenizes to nothing (blank or
    whitespace-only). A fact naming the parameter with no real value is not
    evidence: it must not satisfy a presence requirement, and it must not be
    handed to `_stated_matches` where `str(None)` tokenizes to `{"none"}` and
    would manufacture a `review` verdict out of blank evidence."""
    return value is not None and bool(_tokens(value))


def _reading_key(value, unit, parameter: str | None):
    """The identity of a reading for comparison purposes.

    Numeric only when the value is *entirely* a number: `units.to_number` reads
    a leading quantity out of prose, which would key "ISO 8528" as 8528 and
    merge it with "API 8528". Unit conversion is attempted so that one
    quantity printed in two units is one reading; an unrecognised or absent
    unit is not an error here, it just means the text is the best identity
    available.
    """
    number = units.pure_number(value)
    if number is not None:
        try:
            canonical, canonical_unit = units.to_canonical(number, unit, parameter)
            return ("num", round(canonical, 9), canonical_unit)
        except units.Unconvertible:
            pass
    # Squeeze internal whitespace too, the same idiom units._fold uses for
    # unit strings, so "IP 55" and "IP55" fold to one key. This is formatting
    # tolerance only: it does not touch punctuation, so "±10" and "±10%" -
    # different quantities in some parameter families - stay apart.
    return ("txt", str(value).strip().lower().replace(" ", ""))


def _readings(facts: list[dict], parameter: str | None) -> list[list[dict]]:
    """Facts stating `parameter`, grouped into distinct readings.

    Groups are returned in first-appearance order and members in document
    order, so a caller citing `group[0]` cites the first the document
    printed. Blank evidence is filtered by `_states_something` before
    grouping: a fact naming the parameter with no value is not a reading, and
    counting it would manufacture a multiplicity out of nothing.
    """
    want = _norm(parameter)
    groups: dict[tuple, list[dict]] = {}
    for fact in facts:
        if _norm(fact.get("parameter")) != want:
            continue
        if not _states_something(fact.get("value")):
            continue
        key = _reading_key(fact.get("value"), fact.get("unit"), parameter)
        groups.setdefault(key, []).append(fact)
    return list(groups.values())


def _cite(groups: list[list[dict]]) -> str:
    """One printable clause per distinct reading, with its source document."""
    return "; ".join(
        f"{g[0].get('value')!r}{' ' + str(g[0].get('unit')) if g[0].get('unit') else ''}"
        f" (doc {g[0].get('doc_id')})" for g in groups)


def evaluate(requirement, facts: list[dict], deviations: list[dict],
             vendor: str, now: str) -> ComplianceResult:
    """One cell. Verdict order is the whole design; see the module docstring
    and the plan's Task 6 for why each step precedes the next."""
    def result(verdict, rationale, fact=None, candidates=()):
        # Every current call site passes candidates built from disjoint
        # groups, so a duplicate can't arise today - but de-duplicating here,
        # order-preserving, means a future caller can't silently store a
        # repeated id.
        candidate_ids = list(dict.fromkeys(
            c.get("fact_id") for c in candidates if c.get("fact_id")))
        return ComplianceResult(
            req_id=requirement.req_id, vendor=vendor, verdict=verdict,
            fact_id=(fact or {}).get("fact_id"), doc_id=(fact or {}).get("doc_id"),
            candidate_fact_ids=candidate_ids,
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
        groups = _readings(facts, requirement.parameter)
        if not groups:
            # the same rule `auto` follows: not having read the answer is not
            # the vendor having answered wrongly
            return result("unanswered",
                          f"no vendor document stated {requirement.parameter!r}")
        if requirement.value is not None and len(groups) > 1:
            # Two different stated values disagree about the thing asked. Which
            # one governs is not in the documents, and a token comparison that
            # picked one would be deciding on print order.
            flat = [f for g in groups for f in g]
            return result("review",
                          f"required {requirement.parameter} = "
                          f"{requirement.value!r}; vendor states more than one "
                          f"value: {_cite(groups)} — decide which governs",
                          groups[0][0], flat)
        fact = groups[0][0]
        candidates = groups[0] if len(groups[0]) > 1 else ()
        got = fact.get("value")
        if requirement.value is None or _stated_matches(requirement.value, got):
            # Everything that reaches here would once have been a `pass`. A
            # token subset cannot read the sentence its tokens sit in, so a
            # value that negates or excludes arrives looking exactly like one
            # that satisfies - and a wrong `pass` lands in the `matched` group,
            # where a reviewer looks least. `review`, never `fail`: the words
            # may be qualifying something else entirely, and blaming a vendor
            # on a string diff is the thing this tier exists not to do.
            if requirement.value is None:
                # Every reading, not just the first: a vendor stating a parameter
                # twice could otherwise hide a refusal behind a compliant-looking
                # first print, which is the negation guard's own defect restated.
                for group in groups:
                    negations = _negations_in(requirement.text, group[0].get("value"))
                    if negations:
                        flat = [f for g in groups for f in g]
                        return result("review",
                                      f"{requirement.parameter} must be stated; "
                                      f"vendor states {group[0].get('value')!r}, "
                                      f"which reads as a refusal "
                                      f"({', '.join(negations)}) — read it before "
                                      f"accepting", group[0], flat)
                # No negations in any group, proceed to pass
            else:
                # Value-matching: check only first group for negations
                negations = _negations_in(requirement.value, got)
                if negations:
                    return result("review",
                                  f"required {requirement.parameter} = "
                                  f"{requirement.value!r}; vendor states {got!r}, "
                                  f"which carries the required tokens but also "
                                  f"{', '.join(negations)} — read it before "
                                  f"accepting", fact, candidates)
            if requirement.value is None:
                return result("pass",
                              f"vendor states {requirement.parameter} = {got!r}",
                              fact, candidates)
            return result("pass",
                          f"vendor states {requirement.parameter} = {got!r}, "
                          f"which carries the required {requirement.value!r}",
                          fact, candidates)
        # never `fail`: "Class H" is better insulation than "Class F", and a
        # token comparison cannot know that. Cite both and let a human decide.
        return result("review",
                      f"required {requirement.parameter} = {requirement.value!r}; "
                      f"vendor states {got!r}", fact, candidates)

    if requirement.checkability != "auto":
        candidates = [f"{f.get('parameter')}={f.get('value')} {f.get('unit') or ''}".strip()
                      for f in facts[:_MAX_CANDIDATES]]
        statements = [d.get("statement") for d in deviations
                      if _norm_clause(d.get("clause_ref")) == want_clause]
        return result("review",
                      f"human judgement required: {requirement.text}"
                      + (f" | candidate facts: {candidates}" if candidates else "")
                      + (f" | vendor statements: {statements}" if statements else ""))

    groups = _readings(facts, requirement.parameter)
    if not groups:
        # Never `fail`: the pipeline not having read the answer is not the
        # vendor having answered wrongly.
        return result("unanswered",
                      f"no vendor document stated {requirement.parameter!r}")
    if len(groups) > 1:
        # The parameter is stated more than once with genuinely different
        # values. Which one governs is not in the documents, and picking one
        # to compare would be deciding the vendor's award on print order.
        flat = [f for g in groups for f in g]
        return result("review",
                      f"{requirement.parameter} is stated more than once with "
                      f"different values: {_cite(groups)} — decide which "
                      f"governs before comparing against {requirement.value!r}",
                      groups[0][0], flat)
    fact = groups[0][0]
    candidates = groups[0] if len(groups[0]) > 1 else ()

    try:
        ok, why = units.compare(requirement.operator, requirement.value,
                                requirement.unit, fact.get("value"),
                                fact.get("unit"), parameter=requirement.parameter)
    except units.Unconvertible as exc:
        # The evidence was found; only the comparison could not be made. Cite
        # the fact so a human can finish the check by hand.
        return result("unanswered", str(exc), fact, candidates)
    return result("pass" if ok else "fail", why, fact, candidates)


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
