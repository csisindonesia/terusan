"""GDELT: reading Indonesia out of the world's quarter-hour.

The archives are built here in the layout RAW gives them, events beside
mentions, because the mentions reader finds its events by that layout.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from terusan_pipelines.extract import GdeltExtractor, Landed
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.gdelt import EVENT_COLUMNS, MENTION_COLUMNS
from terusan_pipelines.extract.runner import DEFAULT_EXTRACTORS

STAMP = "20260923113000"
EARLIER = "20260923104500"


def event(event_id: str, **fields: str) -> list[str]:
    row = dict.fromkeys(EVENT_COLUMNS, "")
    row.update(GlobalEventID=event_id, **fields)
    return [row[c] for c in EVENT_COLUMNS]


def mention(event_id: str, event_stamp: str) -> list[str]:
    row = dict.fromkeys(MENTION_COLUMNS, "")
    row.update(GlobalEventID=event_id, EventTimeDate=event_stamp, MentionSourceName="kompas.com")
    return [row[c] for c in MENTION_COLUMNS]


def write_archive(path: Path, member: str, rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, "\n".join("\t".join(r) for r in rows) + "\n")
    path.write_bytes(buffer.getvalue())
    return path


def landed(path: Path, kind: str) -> Landed:
    return Landed(
        path=path,
        document_id=f"doc_{path.stem}",
        content_hash="0" * 64,
        source_slug="gdelt-events",
        dataset=f"gdelt-{'events' if kind == 'export' else kind}",
        extra={"kind": kind},
    )


def events_archive(root: Path, stamp: str, rows: list[list[str]]) -> Path:
    return write_archive(
        root / "gdelt-events" / "year=2026" / f"doc_{stamp}" / f"{stamp}.export.csv.zip",
        f"{stamp}.export.CSV",
        rows,
    )


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "raw" / "news" / "gdelt-events"
    events_archive(
        root,
        STAMP,
        [
            event("1", Actor1CountryCode="IDN"),
            event("2", ActionGeo_CountryCode="ID"),
            event("3", Actor1CountryCode="USA", ActionGeo_CountryCode="IS"),
        ],
    )
    events_archive(root, EARLIER, [event("9", Actor2Geo_CountryCode="ID")])
    return root


def test_only_indonesian_events_are_kept(source_root: Path) -> None:
    path = next((source_root / "gdelt-events").rglob(f"{STAMP}*.zip"))

    rows = list(GdeltExtractor().extract(landed(path, "export")))

    assert [r["columns"]["GlobalEventID"] for r in rows] == ["1", "2"]
    assert rows[0]["dataset"] == "gdelt-events"


def test_mentions_follow_their_events_across_quarter_hours(source_root: Path) -> None:
    path = write_archive(
        source_root / "gdelt-mentions" / "year=2026" / "doc_m" / f"{STAMP}.mentions.csv.zip",
        f"{STAMP}.mentions.CSV",
        [
            mention("1", STAMP),
            mention("3", STAMP),
            mention("9", EARLIER),
            # An event from a quarter-hour that was never landed.
            mention("7", "20260923100000"),
        ],
    )

    rows = list(GdeltExtractor().extract(landed(path, "mentions")))

    assert [r["columns"]["GlobalEventID"] for r in rows] == ["1", "9"]


def test_a_quarter_hour_with_nothing_indonesian_is_not_a_failure(tmp_path: Path) -> None:
    root = tmp_path / "raw" / "news" / "gdelt-events"
    path = events_archive(root, STAMP, [event("3", Actor1CountryCode="USA")])

    assert list(GdeltExtractor().extract(landed(path, "export"))) == []


def test_a_reshaped_file_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "raw" / "news" / "gdelt-events"
    path = events_archive(root, STAMP, [["1", "20260923"]])

    with pytest.raises(ExtractionError, match="columns"):
        list(GdeltExtractor().extract(landed(path, "export")))


def test_gdelt_is_read_before_the_generic_archive_reader(source_root: Path) -> None:
    path = next((source_root / "gdelt-events").rglob(f"{STAMP}*.zip"))
    first = next(e for e in DEFAULT_EXTRACTORS if e.handles(landed(path, "export")))

    assert isinstance(first, GdeltExtractor)
