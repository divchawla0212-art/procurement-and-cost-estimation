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
    (25.0, "°C", 25.0), (77.0, "°F", 25.0), (25.0, "℃", 25.0),
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


# --- identical units, and the condition a unit is sometimes printed with -----

@pytest.mark.parametrize("unit,expected,condition", [
    ("dB(A) at 1m", "dB(A)", "at 1m"),
    ("kW@ 55 Deg C", "kW", "@ 55 Deg C"),
    ("mg/Nm3@3% O2", "mg/Nm3", "@3% O2"),
    ("mg/Nm3, To 3% O2", "mg/Nm3", ", To 3% O2"),
    ("kW", "kW", ""),
    ("kg.m2", "kg.m2", ""),          # a dot is not a condition
    ("", "", ""),
    (None, "", ""),
])
def test_a_measurement_condition_is_split_off_the_unit(unit, expected, condition):
    from procurement.units import _strip_qualifier
    got_unit, got_condition = _strip_qualifier(unit)
    assert got_unit == expected
    assert got_condition.strip() == condition.strip()


def test_identical_units_compare_without_any_conversion():
    # both sides say mg/Nm3, so no molar mass is needed and refusing is absurd
    ok, why = compare("<=", 10.0, "mg/Nm3", 10.0, "mg/Nm3",
                      parameter="particulate_matter_limit")
    assert ok is True and "mg/nm3" in why.lower()


def test_identical_units_still_compare_when_the_unit_has_no_family():
    ok, _ = compare(">=", 1250.0, "Amp", 1250.0, "Amp",
                    parameter="generator_breaker_rating")
    assert ok is True


def test_identical_units_do_not_turn_a_real_shortfall_into_a_pass():
    # the shortcut must skip the conversion, not the comparison
    ok, _ = compare(">=", 1250.0, "Amp", 800.0, "Amp",
                    parameter="generator_breaker_rating")
    assert ok is False


def test_a_condition_is_ignored_for_comparison_and_named_in_the_rationale():
    ok, why = compare("<=", 85.0, "dB(A)", 85.0, "dB(A) at 1m",
                      parameter="noise_limit")
    assert ok is True
    assert "at 1m" in why           # the reader is told what was dropped


def test_stripping_a_condition_never_unifies_two_different_quantities():
    # kW@55degC folds to kW, which is still not volts
    with pytest.raises(Unconvertible):
        compare("<=", 10.0, "kW", 220.0, "V", parameter="heater_rating")


def test_a_condition_is_stripped_before_the_family_is_decided():
    # "kW@ 55 Deg C" must read as power, not as an unrecognised unit
    ok, _ = compare(">=", 0.5, "MW", 550.0, "kW@ 55 Deg C",
                    parameter="continuous_rating")
    assert ok is True


def test_a_gauge_pressure_never_compares_against_an_absolute_one():
    # barg and bar differ by one atmosphere; treating them as equal would
    # misjudge a fuel gas requirement
    with pytest.raises(Unconvertible):
        compare(">=", 2.76, "barg", 2.76, "bar", parameter="fuel_gas_pressure")


def test_between_also_skips_conversion_for_identical_units():
    ok, _ = compare("between", [2.76, 4.14], "barg", 3.5, "barg",
                    parameter="fuel_gas_pressure")
    assert ok is True


def test_between_on_identical_units_still_rejects_a_value_outside():
    ok, _ = compare("between", [2.76, 4.14], "barg", 5.0, "barg",
                    parameter="fuel_gas_pressure")
    assert ok is False


# --- the families the real RFQ corpus states --------------------------------

@pytest.mark.parametrize("value,unit,expected,canonical", [
    (110.0, "%", 110.0, "%"),
    (12.0, "months", 12.0, "months"),
    (1.0, "years", 12.0, "months"),
    (1000.0, "m", 1000.0, "m"),
    (1000.0, "mm", 1.0, "m"),
    (3.0, "s", 3.0, "s"),
    (3.0, "ms", 0.003, "s"),
    (1250.0, "Amp", 1250.0, "a"),
    (1.0, "kA", 1000.0, "a"),
    (230.0, "VAC", 230.0, "v"),
    (85.0, "dBA", 85.0, "db(a)"),
    (2.76, "barg", 276.0, "kpag"),
    (28000.0, "kg", 28000.0, "kg"),
])
def test_the_new_families_scale_to_their_canonical_unit(value, unit, expected,
                                                       canonical):
    got, got_canonical = to_canonical(value, unit)
    assert got == pytest.approx(expected, rel=1e-6)
    assert got_canonical == canonical


def test_a_year_is_twelve_months_but_a_month_is_not_any_number_of_hours():
    # exact, so it converts
    assert to_canonical(2.0, "years")[0] == pytest.approx(24.0)
    # not exact, so it refuses rather than approximating
    with pytest.raises(Unconvertible):
        compare("<=", 12.0, "months", 8760.0, "h", parameter="warranty_period")


def test_weeks_never_silently_become_months():
    # 4.348 weeks per month is an average, not a conversion, and a warranty
    # period is not an average
    with pytest.raises(Unconvertible):
        compare(">=", 6.0, "months", 26.0, "weeks", parameter="preservation")


def test_a_percentage_is_not_dimensionless():
    # a dimensionless count must never compare against a percentage
    with pytest.raises(Unconvertible):
        compare(">=", 3.0, "", 110.0, "%", parameter="black_starts")


def test_a_gauge_pressure_refusal_says_the_two_are_different_quantities():
    # not merely "unrecognised unit 'barg'" - INV-10's whole distinction
    with pytest.raises(Unconvertible) as exc:
        compare(">=", 2.76, "barg", 2.76, "bar", parameter="fuel_gas_pressure")
    assert "different quantities" in str(exc.value)


def test_the_live_percentage_requirement_now_compares():
    # sustained_overload_current >= 110 %, vendor states the number bare
    ok, why = compare(">=", 110.0, "%", 300.0, None,
                      parameter="sustained_overload_current")
    assert ok is True and "assumed" in why.lower()


def test_the_live_warranty_requirement_now_compares():
    ok, _ = compare("==", 12.0, "months", 12.0, "Months",
                    parameter="warranty_period")
    assert ok is True


def test_the_live_noise_requirement_compares_across_two_spellings():
    # "dBA" and "dB(A) at 1m" are one unit printed two ways
    ok, why = compare("<=", 85.0, "dBA", 85.0, "dB(A) at 1m",
                      parameter="noise_limit")
    assert ok is True and "at 1m" in why


# --- equality between two values that are not numbers -----------------------

def test_a_class_requirement_matches_the_vendor_stating_it_with_context():
    ok, why = compare("==", "H", "", "Class H", "", parameter="insulation_class")
    assert ok is True and "Class H" in why


def test_a_class_requirement_fails_a_different_class():
    ok, _ = compare("==", "H", "", "Class F", "", parameter="insulation_class")
    assert ok is False


def test_a_non_numeric_equality_needs_every_requirement_token():
    # "Class H Rise" asks for more than "Class H" states; a substring rule
    # would read the vendor's answer as satisfying it
    ok, _ = compare("==", "Class H Rise", "", "Class H", "",
                    parameter="insulation_class")
    assert ok is False


def test_a_non_numeric_equality_is_case_and_punctuation_insensitive():
    assert compare("==", "IP 55", "", "ip55", "", parameter="ip_rating")[0] is True


def test_a_non_numeric_equality_rejects_a_different_rating():
    # "IP 55" is not satisfied by "IP 23"
    assert compare("==", "IP 55", "", "IP 23", "", parameter="ip_rating")[0] is False


def test_an_empty_requirement_value_raises_rather_than_matching_everything():
    with pytest.raises(Unconvertible):
        compare("==", "   ", "", "Class H", "", parameter="insulation_class")


def test_a_numeric_equality_is_unaffected_by_the_token_path():
    assert compare("==", 50, "Hz", 50.0, "Hz", parameter="frequency")[0] is True
    assert compare("==", 50, "Hz", 60.0, "Hz", parameter="frequency")[0] is False


def test_a_non_numeric_requirement_on_an_ordering_operator_still_raises():
    # ">= H" has no meaning; only equality has a non-numeric reading
    with pytest.raises(Unconvertible) as exc:
        compare(">=", "H", "", "Class H", "", parameter="insulation_class")
    assert "requirement" in str(exc.value) and "not a number" in str(exc.value)


def test_a_non_numeric_vendor_value_on_an_ordering_operator_names_the_vendor():
    with pytest.raises(Unconvertible) as exc:
        compare("<=", 50.0, "ppm", "as per standard", "ppm",
                parameter="h2s_tolerance")
    assert "vendor" in str(exc.value) and "not a number" in str(exc.value)
