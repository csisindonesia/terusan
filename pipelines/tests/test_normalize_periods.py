"""Reading the time a figure refers to."""

from __future__ import annotations

import itertools
from datetime import date

import pytest

from terusan_pipelines.normalize import (
    Resolution,
    UnparseablePeriod,
    parse_period,
    try_parse_period,
)


@pytest.mark.parametrize(
    ("raw", "label", "start", "end", "resolution"),
    [
        ("2026", "2026", date(2026, 1, 1), date(2026, 12, 31), Resolution.ANNUAL),
        ("2026-01", "2026-01", date(2026, 1, 1), date(2026, 1, 31), Resolution.MONTHLY),
        ("2026-02", "2026-02", date(2026, 2, 1), date(2026, 2, 28), Resolution.MONTHLY),
        ("2026-Q1", "2026-Q1", date(2026, 1, 1), date(2026, 3, 31), Resolution.QUARTERLY),
        ("2026-Q4", "2026-Q4", date(2026, 10, 1), date(2026, 12, 31), Resolution.QUARTERLY),
        ("2026-S1", "2026-S1", date(2026, 1, 1), date(2026, 6, 30), Resolution.SEMIANNUAL),
        ("2026-01-15", "2026-01-15", date(2026, 1, 15), date(2026, 1, 15), Resolution.DAILY),
    ],
)
def test_iso_style_labels(raw, label, start, end, resolution):
    period = parse_period(raw)
    assert (period.label, period.start, period.end, period.resolution) == (
        label,
        start,
        end,
        resolution,
    )


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("Januari 2026", "2026-01"),
        ("januari 2026", "2026-01"),
        ("Jan 2026", "2026-01"),
        ("Pebruari 2026", "2026-02"),
        ("Maret 2026", "2026-03"),
        ("Mei 2026", "2026-05"),
        ("Agustus 2026", "2026-08"),
        ("Oktober 2026", "2026-10"),
        ("Nopember 2026", "2026-11"),
        ("Desember 2026", "2026-12"),
        ("Des 2026", "2026-12"),
    ],
)
def test_indonesian_month_names(raw, label):
    """Including the older spellings that still appear in BPS releases."""
    assert parse_period(raw).label == label


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("Triwulan I 2026", "2026-Q1"),
        ("Triwulan IV 2026", "2026-Q4"),
        ("triwulan ii 2026", "2026-Q2"),
        ("TW1 2026", "2026-Q1"),
        ("TW 3 2026", "2026-Q3"),
        ("Kuartal II 2026", "2026-Q2"),
        ("Q2 2026", "2026-Q2"),
        ("2026Q3", "2026-Q3"),
    ],
)
def test_quarter_labels_including_roman_numerals(raw, label):
    assert parse_period(raw).label == label


@pytest.mark.parametrize(
    ("raw", "label"), [("Semester I 2026", "2026-S1"), ("Semester II 2026", "2026-S2")]
)
def test_semester_labels(raw, label):
    assert parse_period(raw).label == label


@pytest.mark.parametrize("raw", ["2026M01", "2026-M01", "2026m1"])
def test_statistical_month_notation(raw):
    assert parse_period(raw).label == "2026-01"


def test_day_first_dates():
    assert parse_period("15/01/2026").start == date(2026, 1, 15)


def test_february_in_a_leap_year():
    assert parse_period("2024-02").end == date(2024, 2, 29)


def test_english_month_names_also_work():
    assert parse_period("January 2026").label == "2026-01"


# ---- refusing to guess ----------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["", "   ", "not a period", "Jumlah", "Total", "2026-13", "Triwulan V 2026"]
)
def test_unrecognised_labels_raise(raw):
    """A misread period files a figure under the wrong year, invisibly."""
    with pytest.raises(UnparseablePeriod):
        parse_period(raw)


def test_try_parse_returns_none_for_callers_that_expect_misses():
    assert try_parse_period("Jumlah") is None
    assert try_parse_period("2026-01") is not None


# ---- bounds ---------------------------------------------------------------


def test_bounds_are_inclusive():
    """Read far more often in a SQL console than in code."""
    period = parse_period("2026-01")
    assert date(2026, 1, 1) in period
    assert date(2026, 1, 31) in period
    assert date(2026, 2, 1) not in period


def test_quarters_tile_the_year_without_gaps_or_overlap():
    quarters = [parse_period(f"2026-Q{q}") for q in (1, 2, 3, 4)]
    assert quarters[0].start == date(2026, 1, 1)
    assert quarters[-1].end == date(2026, 12, 31)
    for earlier, later in itertools.pairwise(quarters):
        assert (later.start - earlier.end).days == 1


def test_months_tile_the_year_without_gaps():
    months = [parse_period(f"2026-{m:02d}") for m in range(1, 13)]
    for earlier, later in itertools.pairwise(months):
        assert (later.start - earlier.end).days == 1


def test_an_iso_timestamp_is_read_as_its_day():
    """BMKG dates each earthquake to the second. A day is the finest period
    Silver holds, and refusing the timestamp would lose the event."""
    period = parse_period("2026-09-21T23:48:13+00:00")

    assert period.label == "2026-09-21"
    assert period.resolution is Resolution.DAILY


def test_a_timestamp_with_a_space_or_a_zulu_zone_reads_the_same():
    assert parse_period("2026-09-21 23:48:13").label == "2026-09-21"
    assert parse_period("2026-09-21T23:48Z").label == "2026-09-21"


def test_an_impossible_date_in_a_timestamp_is_still_refused():
    """Dropping the time must not mean accepting anything shaped like one."""
    with pytest.raises(UnparseablePeriod):
        parse_period("2026-13-45T00:00:00Z")
