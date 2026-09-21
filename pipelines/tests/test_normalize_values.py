"""Reading numbers out of published statistics.

Getting these wrong is the worst failure in the pipeline: a value off by a
thousand looks entirely plausible in a chart and nothing downstream catches it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from terusan_pipelines.normalize import NumberFormat, ValueStatus, parse_value

# ---- the two conventions --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Both separators present: the last one is the decimal point, whichever
        # convention the source uses. Settled by the data, not by assumption.
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1.234.567,89", "1234567.89"),
        ("1,234,567.89", "1234567.89"),
        # One separator, not a group of three: must be a decimal fraction.
        ("1,5", "1.5"),
        ("1.5", "1.5"),
        ("0,25", "0.25"),
        # Repeated separator can only be grouping.
        ("1.234.567", "1234567"),
        ("1,234,567", "1234567"),
        # No separator at all.
        ("1234", "1234"),
        ("0", "0"),
    ],
)
def test_unambiguous_numbers_parse_exactly(raw, expected):
    parsed = parse_value(raw)
    assert parsed.value == Decimal(expected)
    assert parsed.unambiguous


def test_a_single_separator_with_three_digits_is_ambiguous():
    """`1.234` is one thousand two hundred, or one point two three four."""
    parsed = parse_value("1.234")
    assert not parsed.unambiguous


@pytest.mark.parametrize(
    ("number_format", "expected"),
    [(NumberFormat.INDONESIAN, "1234"), (NumberFormat.ANGLO, "1.234")],
)
def test_an_explicit_format_settles_the_ambiguous_case(number_format, expected):
    parsed = parse_value("1.234", number_format)
    assert parsed.value == Decimal(expected)
    assert parsed.unambiguous


def test_auto_reads_the_ambiguous_case_as_grouping_and_says_so():
    """The commoner intent in published tables, but flagged so it can be excluded."""
    parsed = parse_value("1.234", NumberFormat.AUTO)
    assert parsed.value == Decimal("1234")
    assert not parsed.unambiguous


def test_decimal_not_float():
    """Summing a million float-parsed figures drifts inexplicably."""
    assert isinstance(parse_value("1.234,56").value, Decimal)
    assert parse_value("0,1").value + parse_value("0,2").value == Decimal("0.3")


# ---- values that are not numbers -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("-", ValueStatus.MISSING),
        ("—", ValueStatus.MISSING),
        ("...", ValueStatus.MISSING),
        ("…", ValueStatus.MISSING),
        ("", ValueStatus.MISSING),
        ("n/a", ValueStatus.NOT_APPLICABLE),
        ("n.a.", ValueStatus.NOT_APPLICABLE),
        ("tidak ada", ValueStatus.NOT_APPLICABLE),
        ("x", ValueStatus.SUPPRESSED),
        ("rahasia", ValueStatus.SUPPRESSED),
        ("abc", ValueStatus.UNPARSEABLE),
    ],
)
def test_non_numeric_cells_keep_their_meaning(raw, status):
    """ "Not collected" and "collected and zero" are different facts."""
    parsed = parse_value(raw)
    assert parsed.status is status
    assert parsed.value is None
    assert not parsed.ok


def test_zero_is_a_value_not_a_gap():
    parsed = parse_value("0")
    assert parsed.ok
    assert parsed.value == Decimal("0")


def test_a_suppressed_figure_is_not_a_missing_one():
    assert parse_value("x").status is not parse_value("-").status


# ---- qualifiers and signs -------------------------------------------------


@pytest.mark.parametrize("raw", ["1234*", "1234**", "1234 p", "1234r"])
def test_provisional_markers_are_recorded_not_discarded(raw):
    parsed = parse_value(raw)
    assert parsed.value == Decimal("1234")
    assert parsed.status is ValueStatus.PROVISIONAL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-1,5", "-1.5"), ("−1,5", "-1.5"), ("(1.234,5)", "-1234.5"), ("+2,5", "2.5")],
)
def test_signs_including_accounting_brackets(raw, expected):
    assert parse_value(raw).value == Decimal(expected)


def test_unicode_minus_from_pdfs_is_handled():
    """PDF text extraction yields U+2212, not ASCII hyphen."""
    assert parse_value("−2,5").value == Decimal("-2.5")


# ---- units in the cell ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value", "unit"),
    [
        ("12,5%", "12.5", "%"),
        ("1.234,5 ton", "1234.5", "ton"),
        ("5,2 persen", "5.2", "persen"),
        ("(2,5)%", "-2.5", "%"),
    ],
)
def test_a_unit_in_the_cell_is_split_out(raw, value, unit):
    """A percentage read as an absolute is a silent error."""
    parsed = parse_value(raw)
    assert parsed.value == Decimal(value)
    assert parsed.unit == unit


def test_a_bare_unit_with_no_number_does_not_become_a_value():
    assert parse_value("ton").status is ValueStatus.UNPARSEABLE


# ---- whitespace and stray characters --------------------------------------


@pytest.mark.parametrize("raw", ["  1.234,56  ", "1 234,56", "1 234,56"])
def test_whitespace_and_non_breaking_spaces_are_tolerated(raw):
    assert parse_value(raw).value == Decimal("1234.56")


def test_the_raw_text_is_always_kept():
    """Whatever the outcome, the source cell stays visible in Silver."""
    assert parse_value("wat").raw == "wat"
    assert parse_value("1.234,56").raw == "1.234,56"


# ---- scientific notation --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # DJPK's APBD export writes its largest figures with an exponent.
        ("4.7909816610247E+14", Decimal("479098166102470")),
        ("1.0887460909906E+14", Decimal("108874609099060")),
        ("-1.5E+3", Decimal("-1500")),
        ("1.5e3", Decimal("1500")),
        # The brackets are an accounting negative, exponent or not.
        ("(1.5E+3)", Decimal("-1500")),
    ],
)
def test_exponential_figures_parse_exactly(raw, expected):
    """479 trillion rupiah, not a parse failure — and not a float, which would
    lose the low digits of a figure this size."""
    parsed = parse_value(raw)
    assert parsed.status is ValueStatus.OK
    assert parsed.value == expected


@pytest.mark.parametrize("number_format", [NumberFormat.INDONESIAN, NumberFormat.ANGLO])
def test_an_exponent_reads_the_same_under_either_convention(number_format):
    """A literal with an exponent has no grouping to disambiguate, so the dot
    is a decimal point whichever convention the source writes in — the reading
    must not depend on the flag."""
    assert parse_value("4.79E+14", number_format).value == Decimal("479000000000000")


def test_grouped_numbers_are_still_read_by_convention():
    """The exponent path must not swallow ordinary Indonesian numbers."""
    assert parse_value("1.234,56", NumberFormat.INDONESIAN).value == Decimal("1234.56")
    assert parse_value("1,234.56", NumberFormat.ANGLO).value == Decimal("1234.56")
