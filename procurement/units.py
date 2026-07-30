"""Unit normalisation for compliance arithmetic.

The model reads; this module decides what two printed quantities mean in one
unit. Anything it cannot convert honestly raises `Unconvertible` with a
reason, and compliance.py turns that into `unanswered` — never a silent pass
and never a fail. Failing a vendor on arithmetic nobody can audit is the one
outcome here that would make a compliance review indefensible.
"""
import math
import re

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_TOKENS = re.compile(r"[^a-z0-9]+")


class Unconvertible(Exception):
    """Carries the reason, which is stored verbatim in the verdict rationale."""


# canonical unit per family; every alias maps to (family, factor) where
# canonical_value = raw * factor. Temperature is affine and handled apart.
_SCALAR = {
    "w": ("power", 0.001), "kw": ("power", 1.0), "mw": ("power", 1000.0),
    # apparent power is deliberately its own family: kW <-> kVA needs a power
    # factor no datasheet states in a form this module could read.
    "kva": ("apparent_power", 1.0), "mva": ("apparent_power", 1000.0),
    "pa": ("pressure", 0.001), "kpa": ("pressure", 1.0),
    "mpa": ("pressure", 1000.0), "bar": ("pressure", 100.0),
    "mbar": ("pressure", 0.1), "psi": ("pressure", 6.894757),
    "kg/cm2": ("pressure", 98.0665),
    "ppm": ("concentration", 1.0),          # mg/nm3 handled by molar mass
    "hz": ("frequency", 1.0), "khz": ("frequency", 1000.0),
    "v": ("voltage", 1.0), "kv": ("voltage", 1000.0),
    "kg/h": ("mass_flow", 1.0), "t/h": ("mass_flow", 1000.0),
    "nm3/h": ("volume_flow", 1.0), "m3/h": ("volume_flow", 1.0),
    "": ("dimensionless", 1.0),
}
_CANONICAL = {"power": "kw", "apparent_power": "kva", "pressure": "kpa",
              "concentration": "ppm", "frequency": "hz", "voltage": "v",
              "mass_flow": "kg/h", "volume_flow": "nm3/h",
              "temperature": "degc", "dimensionless": ""}

_ALIASES = {
    "°c": "degc", "degc": "degc", "deg c": "degc", "celsius": "degc", "c": "degc",
    "°f": "degf", "degf": "degf", "deg f": "degf", "fahrenheit": "degf", "f": "degf",
    "k": "k", "kelvin": "k",
    "mg/nm³": "mg/nm3", "mg/nm^3": "mg/nm3", "mg·nm⁻³": "mg/nm3",
    "mg/nm3": "mg/nm3", "mgnm3": "mg/nm3", "mg/m3": "mg/nm3",
    "kg/cm²": "kg/cm2", "nm³/h": "nm3/h", "m³/h": "m3/h",
    "kilowatt": "kw", "megawatt": "mw", "volts": "v", "hertz": "hz",
    "parts per million": "ppm", "ppmv": "ppm", "vppm": "ppm",
}

# Molar mass (g/mol) per substance, and the molar volume of an ideal gas at
# 0 degC / 101.325 kPa. mg/Nm3 = ppm * M / 22.414.
_MOLAR_MASS = {"h2s": 34.081, "no2": 46.0055, "nox": 46.0055, "so2": 64.066,
               "co2": 44.009, "co": 28.010, "ch4": 16.043, "o2": 31.998,
               "nh3": 17.031}
_MOLAR_VOLUME = 22.414


# A unit is sometimes printed with the condition it was measured under:
# "dB(A) at 1m", "kW@ 55 Deg C", "mg/Nm3, To 3% O2". The condition is not part
# of the unit, but it is not noise either - it is dropped for the comparison
# and quoted in the rationale, so a reviewer sees exactly what was ignored.
_QUALIFIER = re.compile(r"\s*(?:@|,|\bat\b).*$", re.IGNORECASE)


def _strip_qualifier(unit: str | None) -> tuple[str, str]:
    """Split a printed unit into (unit, the condition it was measured under)."""
    text = (unit or "").strip()
    match = _QUALIFIER.search(text)
    if match is None:
        return text, ""
    return text[:match.start()].strip(), match.group(0).strip()


def _fold(unit: str | None) -> str:
    """Fold a printed unit onto one spelling. Spaced aliases ("deg C") are
    tried before the space-stripped form, so both spellings resolve."""
    spaced, _condition = _strip_qualifier(unit)
    spaced = spaced.lower()
    if spaced in _ALIASES:
        return _ALIASES[spaced]
    squeezed = spaced.replace(" ", "")
    return _ALIASES.get(squeezed, squeezed)


def _same_unit(req_unit, fact_unit) -> bool:
    """True when both sides print the same unit, however it is spelled."""
    return _fold(req_unit) == _fold(fact_unit)


def to_number(value) -> float | None:
    """Leading quantity of a printed value, or None. Never 0.0 as a fallback:
    a zero would compare as a real bound and fail a compliant vendor."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = _NUMBER.search(value)
    if match is None:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _pure_number(value) -> float | None:
    """The value only if it is *entirely* a number — "60" but not "ISO 8528".

    `to_number` reads a leading quantity out of prose, which is right for a
    datasheet cell and wrong for list membership: matching "API 8528" against
    "ISO 8528" on their shared digits would accept a different standard.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _NUMBER.fullmatch(text) is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _substance(parameter: str | None) -> str | None:
    """The measured substance named by the parameter, as a whole token.

    Substring matching would read "continuous_rating" as carbon monoxide and
    convert with the wrong molar mass — a plausible number that is wrong by
    tens of percent, which is exactly the failure this module exists to
    prevent. An unrecognised parameter raises upstream instead.
    """
    for token in _TOKENS.split((parameter or "").lower()):
        if token in _MOLAR_MASS:
            return token
    return None


def to_canonical(value: float, unit: str | None,
                 parameter: str | None = None) -> tuple[float, str]:
    """Return (value in the family's canonical unit, canonical unit name)."""
    u = _fold(unit)
    if u == "degc":
        return float(value), "degc"
    if u == "degf":
        return (float(value) - 32.0) * 5.0 / 9.0, "degc"
    if u == "k":
        return float(value) - 273.15, "degc"
    if u == "mg/nm3":
        token = _substance(parameter)
        if token is None:
            raise Unconvertible(
                f"cannot convert mg/Nm3 to ppm for {parameter!r}: the molar "
                "mass of the measured substance is unknown")
        return float(value) * _MOLAR_VOLUME / _MOLAR_MASS[token], "ppm"
    if u in _SCALAR:
        family, factor = _SCALAR[u]
        return float(value) * factor, _CANONICAL[family]
    raise Unconvertible(f"unrecognised unit {unit!r}")


def _family(unit: str | None, parameter: str | None) -> str:
    u = _fold(unit)
    if u in ("degc", "degf", "k"):
        return "temperature"
    if u == "mg/nm3":
        return "concentration"
    if u in _SCALAR:
        return _SCALAR[u][0]
    raise Unconvertible(f"unrecognised unit {unit!r}")


def _assume_unit(req_unit, fact_unit) -> tuple[object, str]:
    """Datasheets routinely print the number in a column headed by the unit.
    The assumption is stated in the rationale, never made silently."""
    if fact_unit is None or str(fact_unit).strip() == "":
        if req_unit not in (None, ""):
            return req_unit, f" (vendor unit not stated; assumed {req_unit})"
    return fact_unit, ""


def _require_same_family(req_unit, fact_unit, parameter: str | None) -> None:
    lhs_family = _family(req_unit, parameter)
    rhs_family = _family(fact_unit, parameter)
    if lhs_family == rhs_family:
        return
    if {lhs_family, rhs_family} == {"power", "apparent_power"}:
        raise Unconvertible(
            "cannot compare kW with kVA: the conversion needs a power "
            "factor, which the documents do not state")
    raise Unconvertible(
        f"cannot compare {req_unit!r} with {fact_unit!r}: different "
        f"quantities ({lhs_family} vs {rhs_family})")


def _align(req_values: list[float], fact_value: float, req_unit, fact_unit,
           parameter: str | None) -> tuple[list[float], float, str, str]:
    """Bring both sides into one unit. Returns (req_values, fact_value,
    unit name, condition note).

    Identical units are the whole point: two numbers already printed in the
    same unit need no arithmetic, so no conversion is entitled to refuse them.
    `mg/Nm3` against `mg/Nm3` was being declined for an unknown molar mass it
    never needed.
    """
    conditions = [c for c in (_strip_qualifier(req_unit)[1],
                              _strip_qualifier(fact_unit)[1]) if c]
    note = (f" (ignoring {', '.join(repr(c) for c in conditions)})"
            if conditions else "")
    if _same_unit(req_unit, fact_unit):
        return list(req_values), float(fact_value), _fold(req_unit), note

    _require_same_family(req_unit, fact_unit, parameter)
    aligned, canonical = [], ""
    for value in req_values:
        converted, canonical = to_canonical(value, req_unit, parameter)
        aligned.append(converted)
    fact_c, _ = to_canonical(fact_value, fact_unit, parameter)
    return aligned, fact_c, canonical, note


def _members(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip().lower() for v in value]
    return [v.strip().lower() for v in str(value).split(",") if v.strip()]


def compare(operator: str, req_value, req_unit, fact_value, fact_unit,
            parameter: str | None = None) -> tuple[bool, str]:
    """Return (satisfied, rationale). Raises Unconvertible with a reason the
    caller stores verbatim on an `unanswered` verdict."""
    if operator == "in":
        allowed = _members(req_value)
        got = str(fact_value).strip().lower()
        numeric = _pure_number(fact_value)
        if numeric is not None:
            # numeric membership only between values that are wholly numbers;
            # a labelled member ("ISO 8528") is matched as a label, below
            if any(n is not None and math.isclose(n, numeric, rel_tol=1e-9)
                   for n in (_pure_number(a) for a in allowed)):
                return True, f"{fact_value} is one of {allowed}"
        return got in allowed, f"{fact_value} against allowed {allowed}"

    if operator == "between":
        # A stated range, not a set of permitted values. `in` cannot express
        # this: read "5-58 degC" as membership and a vendor offering 55 degC
        # is failed for meeting the requirement.
        bounds = req_value if isinstance(req_value, (list, tuple)) else None
        if bounds is None or len(bounds) != 2:
            raise Unconvertible(
                f"a range needs exactly two bounds, got {req_value!r}")
        low, high = to_number(bounds[0]), to_number(bounds[1])
        got = to_number(fact_value)
        if low is None or high is None:
            raise Unconvertible(f"range bounds {req_value!r} are not numbers")
        if got is None:
            raise Unconvertible(f"vendor value {fact_value!r} is not a number")

        fact_unit, note = _assume_unit(req_unit, fact_unit)
        (low_c, high_c), got_c, canonical, condition = _align(
            [low, high], got, req_unit, fact_unit, parameter)
        note += condition
        if low_c > high_c:                      # printed high-to-low
            low_c, high_c = high_c, low_c
        inside = ((got_c >= low_c or math.isclose(got_c, low_c, rel_tol=1e-9))
                  and (got_c <= high_c or math.isclose(got_c, high_c, rel_tol=1e-9)))
        return inside, (f"{fact_value} {fact_unit or ''} = {got_c:g} {canonical} "
                        f"within {low_c:g}..{high_c:g} {canonical}{note}").strip()

    if operator not in (">=", "<=", "=="):
        raise Unconvertible(f"unsupported operator {operator!r}")

    lhs = to_number(req_value)
    rhs = to_number(fact_value)
    if lhs is None:
        raise Unconvertible(f"requirement value {req_value!r} is not a number")
    if rhs is None:
        if operator == "==":
            same = str(req_value).strip().lower() == str(fact_value).strip().lower()
            return same, f"{fact_value!r} against required {req_value!r}"
        raise Unconvertible(f"vendor value {fact_value!r} is not a number")

    fact_unit, note = _assume_unit(req_unit, fact_unit)
    (lhs_c,), rhs_c, canonical, condition = _align(
        [lhs], rhs, req_unit, fact_unit, parameter)
    note += condition
    if operator == ">=":
        ok = rhs_c >= lhs_c or math.isclose(rhs_c, lhs_c, rel_tol=1e-9)
    elif operator == "<=":
        ok = rhs_c <= lhs_c or math.isclose(rhs_c, lhs_c, rel_tol=1e-9)
    else:
        ok = math.isclose(rhs_c, lhs_c, rel_tol=1e-9, abs_tol=1e-9)
    return ok, (f"{fact_value} {fact_unit or ''} = {rhs_c:g} {canonical} "
                f"{operator} {lhs_c:g} {canonical}{note}").strip()
