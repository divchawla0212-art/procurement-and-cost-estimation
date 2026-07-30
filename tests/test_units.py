import pytest

from procurement.units import Unconvertible, compare, to_canonical, to_number


@pytest.mark.parametrize("raw,expected", [
    (50, 50.0), (50.5, 50.5), ("50", 50.0), ("50.5", 50.5), (" 550 ", 550.0),
    ("550 kW", 550.0), (">= 550", 550.0), ("1,200", 1200.0), ("-5", -5.0),
])
def test_to_number_reads_a_leading_quantity(raw, expected):
    assert to_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "as per standard", [], {}])
def test_to_number_returns_none_rather_than_zero(raw):
    # coercing an unparseable value to 0.0 would silently fail every vendor
    assert to_number(raw) is None


@pytest.mark.parametrize("value,unit,expected", [
    (1.0, "MW", 1000.0), (1000.0, "W", 1.0), (550.0, "kW", 550.0),
    (1.0, "bar", 100.0), (1.0, "MPa", 1000.0), (100.0, "kPa", 100.0),
    (1.0, "psi", 6.894757), (1.0, "kg/cm2", 98.0665),
    (50.0, "Hz", 50.0), (11.0, "kV", 11000.0), (400.0, "V", 400.0),
])
def test_to_canonical_scales_within_a_family(value, unit, expected):
    got, _ = to_canonical(value, unit)
    assert got == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("value,unit,expected", [
    (25.0, "degC", 25.0), (77.0, "degF", 25.0), (298.15, "K", 25.0),
    (25.0, "°C", 25.0), (77.0, "°F", 25.0),
])
def test_temperature_conversion_is_affine_not_scalar(value, unit, expected):
    got, canonical = to_canonical(value, unit)
    assert got == pytest.approx(expected, rel=1e-6) and canonical == "degc"


def test_a_dimensionless_bound_is_its_own_family():
    assert to_canonical(3.0, "")[0] == 3.0
    assert to_canonical(3.0, None)[0] == 3.0


def test_ppm_to_mg_per_nm3_uses_the_substance_molar_mass():
    # H2S: 50 ppm * 34.081 / 22.414 = 76.02 mg/Nm3
    got, canonical = to_canonical(76.02, "mg/Nm3", parameter="h2s_tolerance")
    assert got == pytest.approx(50.0, rel=1e-3) and canonical == "ppm"


@pytest.mark.parametrize("unit", ["mg/Nm3", "mg/nm³", "mg/Nm^3", "mg·Nm⁻³"])
def test_mg_per_nm3_spellings_all_fold_to_one_unit(unit):
    got, _ = to_canonical(76.02, unit, parameter="h2s_tolerance")
    assert got == pytest.approx(50.0, rel=1e-3)


def test_the_substance_comes_from_the_parameter_name_not_a_guess():
    # NOx has a different molar mass; the same reading must not convert alike
    h2s, _ = to_canonical(76.02, "mg/Nm3", parameter="h2s_tolerance")
    nox, _ = to_canonical(76.02, "mg/Nm3", parameter="nox_emission")
    assert h2s != pytest.approx(nox, rel=1e-3)


def test_an_unknown_substance_raises_rather_than_guessing_a_molar_mass():
    with pytest.raises(Unconvertible) as exc:
        to_canonical(76.02, "mg/Nm3", parameter="mystery_gas")
    assert "molar mass" in str(exc.value).lower()


def test_a_substance_name_is_a_whole_token_not_a_substring():
    # "continuous_rating" contains "co"; reading that as carbon monoxide would
    # produce a plausible number from the wrong molar mass, which is the one
    # failure this module exists to prevent.
    with pytest.raises(Unconvertible) as exc:
        to_canonical(76.02, "mg/Nm3", parameter="continuous_rating")
    assert "molar mass" in str(exc.value).lower()


def test_an_unrecognised_unit_raises_with_the_unit_named():
    with pytest.raises(Unconvertible) as exc:
        to_canonical(1.0, "furlongs")
    assert "furlongs" in str(exc.value)


def test_kva_never_converts_to_kw():
    with pytest.raises(Unconvertible) as exc:
        compare(">=", 500.0, "kW", 625.0, "kVA", parameter="continuous_rating")
    assert "power factor" in str(exc.value).lower()


def test_compare_across_units_within_a_family():
    ok, why = compare(">=", 0.5, "MW", 550.0, "kW", parameter="continuous_rating")
    assert ok is True and "550" in why


@pytest.mark.parametrize("op,req,fact,expected", [
    (">=", 50.0, 70.0, True), (">=", 50.0, 40.0, False),
    ("<=", 50.0, 40.0, True), ("<=", 50.0, 70.0, False),
    ("==", 50.0, 50.0, True), ("==", 50.0, 50.5, False),
    (">=", 50.0, 50.0, True), ("<=", 50.0, 50.0, True),
])
def test_the_four_operators(op, req, fact, expected):
    ok, _ = compare(op, req, "ppm", fact, "ppm", parameter="h2s_tolerance")
    assert ok is expected


def test_equality_tolerates_float_representation():
    ok, _ = compare("==", 50, "Hz", 50.0, "Hz", parameter="frequency")
    assert ok is True


def test_in_accepts_a_list_or_a_comma_string():
    assert compare("in", ["50", "60"], "Hz", 60, "Hz", parameter="frequency")[0]
    assert compare("in", "50, 60", "Hz", 60, "Hz", parameter="frequency")[0]
    assert not compare("in", "50, 60", "Hz", 55, "Hz", parameter="frequency")[0]


def test_between_accepts_a_value_inside_the_range():
    # the live failure: the MR states "Temperature 5-58 deg C" and a vendor
    # offering 55 degC was FAILED, because `in` is set membership
    ok, why = compare("between", [5, 58], "degC", 55, "degC",
                      parameter="ambient_design_temp")
    assert ok is True and "55" in why


def test_between_rejects_a_value_outside_the_range():
    ok, _ = compare("between", [5, 58], "degC", 60, "degC",
                    parameter="ambient_design_temp")
    assert ok is False


@pytest.mark.parametrize("edge", [5, 58])
def test_between_is_inclusive_at_both_bounds(edge):
    assert compare("between", [5, 58], "degC", edge, "degC")[0] is True


def test_between_converts_units_before_comparing():
    ok, _ = compare("between", [0.4, 0.6], "MW", 550.0, "kW",
                    parameter="continuous_rating")
    assert ok is True


def test_between_tolerates_bounds_stated_in_either_order():
    assert compare("between", [58, 5], "degC", 55, "degC")[0] is True


@pytest.mark.parametrize("bad", [50, [50], [1, 2, 3], "5-58", None])
def test_between_needs_exactly_two_bounds(bad):
    # one end of a range is a half-stated bound; guessing the other is how a
    # compliant vendor gets failed
    with pytest.raises(Unconvertible):
        compare("between", bad, "degC", 55, "degC")


def test_in_stays_strict_membership_and_never_accepts_a_midpoint():
    # 55 Hz is not permitted just because 50 and 60 are. This is why `in` and
    # `between` have to be different operators rather than one shape-sniffing
    # rule: reading [50, 60] as a range would pass this.
    ok, _ = compare("in", [50, 60], "Hz", 55, "Hz", parameter="frequency")
    assert ok is False


def test_in_matches_non_numeric_members_case_insensitively():
    ok, _ = compare("in", ["API 616", "ISO 8528"], None, "iso 8528", None,
                    parameter="applicable_standard")
    assert ok is True


def test_in_does_not_match_a_label_by_the_number_inside_it():
    # "API 8528" shares its digits with "ISO 8528" and is a different standard
    ok, _ = compare("in", ["API 616", "ISO 8528"], None, "api 8528", None,
                    parameter="applicable_standard")
    assert ok is False


def test_a_non_numeric_vendor_value_on_a_numeric_operator_raises():
    with pytest.raises(Unconvertible) as exc:
        compare(">=", 50.0, "ppm", "as per standard", "ppm",
                parameter="h2s_tolerance")
    assert "not a number" in str(exc.value).lower()


def test_a_missing_vendor_unit_is_assumed_to_match_a_stated_requirement_unit():
    # datasheets routinely print the number in a column headed by the unit
    ok, why = compare(">=", 50.0, "ppm", 70.0, None, parameter="h2s_tolerance")
    assert ok is True and "assumed" in why.lower()


def test_an_unknown_operator_raises_rather_than_defaulting_to_pass():
    with pytest.raises(Unconvertible):
        compare("approximately", 50.0, "ppm", 50.0, "ppm", parameter="x")
