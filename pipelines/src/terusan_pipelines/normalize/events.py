"""Bronze's holiday decrees into Silver events.

Bronze holds every version of every decree: 2020's calendar is there five
times, as signed in 2019 and as amended four times through the pandemic. An
amendment restates the whole annex, so a year's calendar is the annex of the
latest decree for it, and the earlier versions are history rather than fact.

Each decree lists days; a reader thinks in holidays. Consecutive days under one
holiday and one kind become one event — Idul Fitri's two days of national
holiday are one, the cuti bersama either side of it another — so "prices
around Lebaran" has one start date to be read against. Days more than a week
apart stay separate events: Isra Mikraj fell in both January and December
2027.

The name the decree printed is matched to a key in
`reference/events/holidays.csv`. Where OCR could not read the name at all, the
key is taken from another version of the same year's calendar that names the
same date, or failing that from the national holiday the cuti bersama sits
beside — cuti bersama exist only to bridge one.

Ramadan is not decreed. It is derived as the thirty days before Idul Fitri and
marked approximate: 1 Ramadan is the Minister of Religious Affairs' to fix, and
the month runs twenty-nine days or thirty.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from .reference import REFERENCE_ROOT, _aliases, _rows

log = structlog.get_logger(__name__)

HOLIDAYS_CSV = REFERENCE_ROOT / "events" / "holidays.csv"

SOURCE_ID = "menpan-hari-libur"
CATEGORY = "holiday"
NATIONAL = "libur_nasional"
COLLECTIVE = "cuti_bersama"
RAMADAN = "ramadan"

#: Days apart beyond which two dates under one holiday are two events.
SAME_EVENT_GAP = 7

#: How far a cuti bersama may sit from the holiday it bridges.
BRIDGE_DAYS = 7

RAMADAN_DAYS = 30


@dataclass(frozen=True, slots=True)
class Holiday:
    key: str
    name: str
    name_en: str
    religion: str | None
    calendar: str
    match: tuple[str, ...]


def load_holidays(path: Path | None = None) -> list[Holiday]:
    """The holiday registry, in the order its rows are to be tried."""
    return [
        Holiday(
            key=row["key"].strip(),
            name=row["name"].strip(),
            name_en=row["name_en"].strip(),
            religion=row["religion"].strip() or None,
            calendar=row["calendar"].strip(),
            match=tuple(word.lower() for word in _aliases(row.get("match"))),
        )
        for row in _rows(path or HOLIDAYS_CSV)
        if (row.get("key") or "").strip()
    ]


def classify(printed: str, holidays: list[Holiday]) -> str | None:
    """The key of the holiday a decree's name is, or None."""
    name = re.sub(r"\s+", " ", printed.lower())
    for holiday in holidays:
        if any(word in name for word in holiday.match):
            return holiday.key
    return None


@dataclass(slots=True)
class _Day:
    year: int
    day: date
    kind: str
    printed: str
    key: str | None
    decree_id: str
    enacted: date | None
    decree_title: str
    provenance: dict[str, Any]


def _days(records: Iterable[dict[str, Any]], holidays: list[Holiday]) -> list[_Day]:
    days = []
    for record in records:
        columns = dict(record.get("columns") or {})
        try:
            day = date.fromisoformat(columns["date"])
            year = int(columns["year"])
        except (KeyError, ValueError):
            continue
        enacted = columns.get("decree_enacted") or ""
        printed = (columns.get("name") or "").strip(" |—-.'")
        days.append(
            _Day(
                year=year,
                day=day,
                kind=columns.get("kind") or NATIONAL,
                printed=printed,
                key=classify(printed, holidays) if printed else None,
                decree_id=columns.get("decree_id") or "",
                enacted=date.fromisoformat(enacted) if enacted else None,
                decree_title=columns.get("decree_title") or "",
                provenance={
                    field: record.get(field)
                    for field in (
                        "source_url",
                        "document_id",
                        "content_hash",
                        "raw_path",
                        "retrieved_at",
                        "pipeline_version",
                    )
                },
            )
        )
    return days


def latest_calendars(days: list[_Day]) -> dict[int, list[_Day]]:
    """Each year's days as its latest decree lists them."""
    latest: dict[int, tuple[date, int]] = {}
    for d in days:
        rank = (d.enacted or date.min, int(d.decree_id or 0))
        if d.year not in latest or rank > latest[d.year]:
            latest[d.year] = rank
    return {
        year: [
            d
            for d in days
            if d.year == year and (d.enacted or date.min, int(d.decree_id or 0)) == rank
        ]
        for year, rank in latest.items()
    }


def _fill_unnamed(calendar: list[_Day], every_version: list[_Day]) -> None:
    """Keys for days whose names OCR could not read."""
    named_elsewhere = {(d.day, d.kind): d.key for d in every_version if d.key}
    named_any_kind = {d.day: d.key for d in every_version if d.key}
    for d in calendar:
        if d.key:
            continue
        d.key = named_elsewhere.get((d.day, d.kind)) or named_any_kind.get(d.day)
        if d.key is None and d.kind == COLLECTIVE:
            # The holiday it bridges: the nearest national holiday that week.
            nearest = sorted(
                (abs((n.day - d.day).days), n.key)
                for n in calendar
                if n.kind == NATIONAL and n.key and abs((n.day - d.day).days) <= BRIDGE_DAYS
            )
            d.key = nearest[0][1] if nearest else None
        if d.key is None:
            log.warning("events.unnamed_day", date=d.day.isoformat(), kind=d.kind)


def _event_id(kind: str, key: str, start: date) -> str:
    digest = hashlib.sha1(f"{CATEGORY}:{kind}:{key}:{start}".encode()).hexdigest()
    return f"ev_{digest[:16]}"


def build_events(
    records: Iterable[dict[str, Any]],
    holidays: list[Holiday],
    processed_at: datetime,
) -> list[dict[str, Any]]:
    """Silver event rows from Bronze holiday records."""
    registry = {h.key: h for h in holidays}
    every_version = _days(records, holidays)
    rows: list[dict[str, Any]] = []

    for year, calendar in sorted(latest_calendars(every_version).items()):
        _fill_unnamed(calendar, every_version)
        grouped: dict[tuple[str, str], list[_Day]] = defaultdict(list)
        for d in calendar:
            if d.key:
                grouped[(d.kind, d.key)].append(d)

        for (kind, key), group in grouped.items():
            group.sort(key=lambda d: d.day)
            runs: list[list[_Day]] = [[group[0]]]
            for d in group[1:]:
                if d.day == runs[-1][-1].day:
                    continue  # the same day read twice
                if (d.day - runs[-1][-1].day).days > SAME_EVENT_GAP:
                    runs.append([d])
                else:
                    runs[-1].append(d)
            for run in runs:
                rows.append(_row(kind, registry[key], run, year, processed_at))

    rows.extend(_ramadan(rows, registry, processed_at))
    rows.sort(key=lambda r: (r["start_date"], r["kind"], r["key"]))
    return rows


def _row(
    kind: str, holiday: Holiday, run: list[_Day], year: int, processed_at: datetime
) -> dict[str, Any]:
    first = run[0]
    printed = next((d.printed for d in run if d.printed), None)
    return {
        "event_id": _event_id(kind, holiday.key, first.day),
        "category": CATEGORY,
        "kind": kind,
        "key": holiday.key,
        "name": holiday.name,
        "name_en": holiday.name_en,
        "name_printed": printed,
        "religion": holiday.religion,
        "calendar": holiday.calendar,
        "year": year,
        "start_date": first.day,
        "end_date": run[-1].day,
        "dates": [d.day for d in run],
        "geo_id": "IDN",
        "approximate": False,
        "basis": first.decree_title or None,
        "decree_id": first.decree_id or None,
        "decree_enacted": first.enacted,
        "source_id": SOURCE_ID,
        "dataset_id": None,
        "processed_at": processed_at,
        **first.provenance,
    }


def _ramadan(
    rows: list[dict[str, Any]], registry: dict[str, Holiday], processed_at: datetime
) -> list[dict[str, Any]]:
    """The month before each Idul Fitri, approximately."""
    holiday = registry.get(RAMADAN)
    if holiday is None:
        return []
    derived = []
    for row in rows:
        if row["key"] != "idul_fitri" or row["kind"] != NATIONAL:
            continue
        end = row["start_date"] - timedelta(days=1)
        start = row["start_date"] - timedelta(days=RAMADAN_DAYS)
        derived.append(
            {
                **row,
                "event_id": _event_id(RAMADAN, RAMADAN, start),
                "kind": RAMADAN,
                "key": RAMADAN,
                "name": holiday.name,
                "name_en": holiday.name_en,
                "name_printed": None,
                "religion": holiday.religion,
                "calendar": holiday.calendar,
                "start_date": start,
                "end_date": end,
                "dates": [start + timedelta(days=n) for n in range(RAMADAN_DAYS)],
                "approximate": True,
                "basis": (
                    f"Derived: the {RAMADAN_DAYS} days before Idul Fitri as decreed. "
                    "1 Ramadan is fixed by the Minister of Religious Affairs and may "
                    "fall a day later."
                ),
                "processed_at": processed_at,
            }
        )
    return derived
