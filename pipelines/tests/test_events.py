"""Silver events: a year's calendar is its latest decree, days become holidays,
and what OCR could not name is named from what it could.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from terusan_pipelines.normalize.events import (
    build_events,
    classify,
    load_holidays,
)
from terusan_pipelines.normalize.schema import SILVER_EVENTS
from terusan_pipelines.warehouse import table_from_rows

NOW = datetime(2026, 9, 27, tzinfo=UTC)


@pytest.fixture(scope="module")
def holidays() -> list[Any]:
    return load_holidays()


def day(
    when: str,
    name: str,
    kind: str = "libur_nasional",
    decree: str = "2131",
    enacted: str = "2026-09-15",
) -> dict[str, Any]:
    return {
        "document_id": f"doc_{decree}",
        "source_url": f"https://data-jdih.menpan.go.id/dokumen/{decree}.pdf",
        "columns": {
            "date": when,
            "year": when[:4],
            "kind": kind,
            "name": name,
            "decree_id": decree,
            "decree_enacted": enacted,
            "decree_title": f"Keputusan Bersama {decree}",
        },
    }


@pytest.mark.parametrize(
    ("printed", "key"),
    [
        ("Hari Raya Idul Fitri 1441 Hijrivah", "idul_fitri"),
        ("Idul Fitri 1448 Hijriah", "idul_fitri"),
        ("Pengganti Cuti Bersama Hari Raya Idul Fitri 1441 Hijriah", "idul_fitri"),
        ("Tahun Baru Imlek 2571 Konegzili", "tahun_baru_imlek"),
        ("1 Muharam Tahun Baru Islam 1449 Hjjriah", "tahun_baru_islam"),
        ("Tahun Baru 2027 Masehi", "tahun_baru_masehi"),
        ("Kenaikan Isa Al Masih", "kenaikan_yesus_kristus"),
        ("Wafat Isa Al Masih/ Wafat Yesus Kristus", "wafat_yesus_kristus"),
        ("| Kelahiran Yesus Kristus", "natal"),
        ("Proklamasi Kemerdekaan", "kemerdekaan"),
        ("Isra Mikrai Nabi Muhammad SAW", "isra_mikraj"),
        ("Hari Suci Nyepi (Tahun Baru Saka 1949)", "nyepi"),
        ("tidak terbaca", None),
    ],
)
def test_printed_names_resolve_to_one_key_across_the_years(
    holidays: list[Any], printed: str, key: str | None
) -> None:
    assert classify(printed, holidays) == key


def test_the_latest_decree_for_a_year_is_its_calendar(holidays: list[Any]) -> None:
    records = [
        # As signed: Lebaran's cuti bersama in May.
        day("2020-05-22", "Hari Raya Idul Fitri", "cuti_bersama", "910", "2019-08-27"),
        day("2020-05-24", "Hari Raya Idul Fitri", decree="910", enacted="2019-08-27"),
        # As amended in the pandemic: moved to December.
        day("2020-05-24", "Hari Raya Idul Fitri", decree="1152", enacted="2020-12-01"),
        day(
            "2020-12-31",
            "Pengganti Cuti Bersama Hari Raya Idul Fitri",
            "cuti_bersama",
            "1152",
            "2020-12-01",
        ),
    ]
    events = build_events(records, holidays, NOW)
    collective = [e for e in events if e["kind"] == "cuti_bersama"]
    assert [e["start_date"] for e in collective] == [date(2020, 12, 31)]
    assert {e["decree_id"] for e in events if not e["approximate"]} == {"1152"}


def test_consecutive_days_are_one_event_and_distant_ones_two(holidays: list[Any]) -> None:
    records = [
        day("2027-03-10", "Idul Fitri 1448 Hijriah"),
        day("2027-03-11", "Idul Fitri 1448 Hijriah"),
        # Cuti bersama either side, skipping the weekend: one event.
        day("2027-03-09", "Idul Fitri 1448 Hijriah", "cuti_bersama"),
        day("2027-03-12", "Idul Fitri 1448 Hijriah", "cuti_bersama"),
        day("2027-03-15", "Idul Fitri 1448 Hijriah", "cuti_bersama"),
        # Isra Mikraj falls twice in 2027.
        day("2027-01-05", "Isra Mikraj Nabi Muhammad S.A.W. 1448 Hijriah"),
        day("2027-12-26", "Isra Mikraj Nabi Muhammad S.A.W. 1449 Hijriah"),
    ]
    events = {
        (e["kind"], e["key"], e["start_date"]): e for e in build_events(records, holidays, NOW)
    }

    fitri = events[("libur_nasional", "idul_fitri", date(2027, 3, 10))]
    assert fitri["end_date"] == date(2027, 3, 11)
    assert fitri["religion"] == "Islam" and fitri["calendar"] == "hijri"

    leave = events[("cuti_bersama", "idul_fitri", date(2027, 3, 9))]
    assert leave["dates"] == [date(2027, 3, 9), date(2027, 3, 12), date(2027, 3, 15)]

    assert ("libur_nasional", "isra_mikraj", date(2027, 1, 5)) in events
    assert ("libur_nasional", "isra_mikraj", date(2027, 12, 26)) in events


def test_unread_names_are_taken_from_another_version_or_the_holiday_bridged(
    holidays: list[Any],
) -> None:
    records = [
        # The final version's names were not read.
        day("2021-05-13", "", decree="1252", enacted="2021-06-18"),
        day("2021-05-12", "", "cuti_bersama", "1252", "2021-06-18"),
        # An earlier version named the holiday, not the cuti bersama.
        day("2021-05-13", "Hari Raya Idul Fitri 1442 Hijriah", decree="1186", enacted="2021-02-22"),
    ]
    events = build_events(records, holidays, NOW)
    keyed = {(e["kind"], e["start_date"]): e["key"] for e in events}
    assert keyed[("libur_nasional", date(2021, 5, 13))] == "idul_fitri"
    # Cuti bersama bridge the holiday beside them.
    assert keyed[("cuti_bersama", date(2021, 5, 12))] == "idul_fitri"


def test_ramadan_is_derived_from_idul_fitri_and_marked_approximate(holidays: list[Any]) -> None:
    events = build_events(
        [day("2024-04-10", "Hari Raya Idul Fitri 1445 Hijriah"), day("2024-04-11", "Idul Fitri")],
        holidays,
        NOW,
    )
    ramadan = next(e for e in events if e["key"] == "ramadan")
    assert ramadan["approximate"] is True
    assert ramadan["end_date"] == date(2024, 4, 9)
    assert ramadan["start_date"] == date(2024, 3, 11)
    assert len(ramadan["dates"]) == 30


def test_rows_fit_the_silver_schema(holidays: list[Any]) -> None:
    events = build_events([day("2027-01-01", "Tahun Baru 2027 Masehi")], holidays, NOW)
    table = table_from_rows(events, SILVER_EVENTS)
    assert table.num_rows == 1
    assert len({e["event_id"] for e in events}) == len(events)
