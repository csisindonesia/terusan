"""GDELT's quarter-hour archives into Bronze records, for Indonesia.

GDELT publishes the world in each file: a quarter-hour of events is a thousand
rows, of which perhaps five involve Indonesia. The source lands the file whole,
because that is what GDELT published (see `sources/gdelt/events.py`), and this
reader is where the country is chosen.

Both archives are tab-separated with no header row. The column names below are
GDELT 2.0's codebook names, kept as GDELT spells them so a reader can take a
column back to the codebook without translating it.

**Events.** An event is Indonesian when either actor is (`IDN`, CAMEO's ISO
code) or when any of its three locations is in Indonesia (`ID`, GDELT's FIPS
code for the geography). Either alone misses something: a foreign ministry
commenting on Papua has no Indonesian actor, and a protest in Jakarta coded
from a wire story may carry no actor country at all.

**Mentions.** A mention row names an event by id and carries no country of its
own, so it is kept when the event it mentions was kept. The event may have
been first coded in an earlier quarter-hour — `EventTimeDate` says which — so
the Indonesian ids are read from every landed events archive the mentions file
points at, not only its own quarter-hour. An event whose archive was never
landed (the source collects one file in four) cannot be recognised, and its
mentions are passed over: that is a gap in collection, not a reason to keep
the world's mentions.

An archive with nothing Indonesian in it yields no rows and is not an error.
Most mentions files are exactly that.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .base import ExtractionError, Extractor, Landed
from .tabular import decode

SOURCE_SLUG = "gdelt-events"

ARCHIVE_SUFFIXES = {".zip"}

#: CAMEO's actor country, which is ISO 3166 alpha-3.
ACTOR_COUNTRY = "IDN"

#: GDELT's geography country, which is FIPS 10-4 — not ISO, and not `IDN`.
GEO_COUNTRY = "ID"

_ACTOR_FIELDS = (
    "Code",
    "Name",
    "CountryCode",
    "KnownGroupCode",
    "EthnicCode",
    "Religion1Code",
    "Religion2Code",
    "Type1Code",
    "Type2Code",
    "Type3Code",
)

_GEO_FIELDS = (
    "Type",
    "FullName",
    "CountryCode",
    "ADM1Code",
    "ADM2Code",
    "Lat",
    "Long",
    "FeatureID",
)

#: GDELT 2.0 event table, in file order.
EVENT_COLUMNS: tuple[str, ...] = (
    "GlobalEventID",
    "Day",
    "MonthYear",
    "Year",
    "FractionDate",
    *(f"Actor1{name}" for name in _ACTOR_FIELDS),
    *(f"Actor2{name}" for name in _ACTOR_FIELDS),
    "IsRootEvent",
    "EventCode",
    "EventBaseCode",
    "EventRootCode",
    "QuadClass",
    "GoldsteinScale",
    "NumMentions",
    "NumSources",
    "NumArticles",
    "AvgTone",
    *(f"Actor1Geo_{name}" for name in _GEO_FIELDS),
    *(f"Actor2Geo_{name}" for name in _GEO_FIELDS),
    *(f"ActionGeo_{name}" for name in _GEO_FIELDS),
    "DATEADDED",
    "SOURCEURL",
)

#: GDELT 2.0 mentions table, in file order.
MENTION_COLUMNS: tuple[str, ...] = (
    "GlobalEventID",
    "EventTimeDate",
    "MentionTimeDate",
    "MentionType",
    "MentionSourceName",
    "MentionIdentifier",
    "SentenceID",
    "Actor1CharOffset",
    "Actor2CharOffset",
    "ActionCharOffset",
    "InRawText",
    "Confidence",
    "MentionDocLen",
    "MentionDocTone",
    "MentionDocTranslationInfo",
    "Extras",
)

_ACTOR_COUNTRY_COLUMNS = ("Actor1CountryCode", "Actor2CountryCode")
_GEO_COUNTRY_COLUMNS = (
    "Actor1Geo_CountryCode",
    "Actor2Geo_CountryCode",
    "ActionGeo_CountryCode",
)


def _read_archive(path: Path) -> list[list[str]]:
    """The rows of the one tab-separated file inside a GDELT archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(path.read_bytes()))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(str(path), f"unreadable archive: {exc}") from exc

    with archive:
        members = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
        ]
        if not members:
            raise ExtractionError(str(path), "archive holds no CSV")
        text = decode(archive.read(members[0]))

    # GDELT does not quote; a stray quote in a name must not swallow the rows
    # after it.
    return list(csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))


def _named(row: list[str], columns: tuple[str, ...], path: Path) -> dict[str, str]:
    if len(row) != len(columns):
        raise ExtractionError(
            str(path),
            f"row has {len(row)} columns where GDELT 2.0 has {len(columns)}; "
            "the file format has changed and the column list must be re-read",
        )
    return dict(zip(columns, row, strict=True))


def is_indonesian(event: dict[str, str]) -> bool:
    return any(event.get(c) == ACTOR_COUNTRY for c in _ACTOR_COUNTRY_COLUMNS) or any(
        event.get(c) == GEO_COUNTRY for c in _GEO_COUNTRY_COLUMNS
    )


def indonesian_event_ids(path: Path) -> set[str]:
    """The ids of the Indonesian events in one events archive."""
    ids: set[str] = set()
    for row in _read_archive(path):
        if not row:
            continue
        event = _named(row, EVENT_COLUMNS, path)
        if is_indonesian(event):
            ids.add(event["GlobalEventID"])
    return ids


def _events_archives(landed: Landed) -> dict[str, Path]:
    """Every landed events archive of this source, by quarter-hour stamp.

    RAW is `<source>/<dataset>/<partition>/<document>/<file>`, so the events
    dataset sits beside the mentions one, three levels up from the file.
    """
    source_root = landed.path.parents[3]
    found: dict[str, Path] = {}
    for path in source_root.glob("gdelt-events/*/*/*.zip"):
        found.setdefault(path.name.split(".")[0], path)
    return found


class GdeltExtractor(Extractor):
    """One Bronze record per Indonesian event, or per mention of one."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in ARCHIVE_SUFFIXES

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        kind = str((landed.extra or {}).get("kind") or "")
        if not kind:
            name = landed.path.name.lower()
            kind = next((k for k in ("export", "mentions", "gkg") if f".{k}." in name), "")

        if kind == "export":
            rows = self._events(landed)
        elif kind == "mentions":
            rows = self._mentions(landed)
        else:
            # The knowledge graph is landed only on request and nothing reads
            # it yet; saying so beats guessing at its columns.
            raise ExtractionError(
                str(landed.path), f"no reader for GDELT {kind or 'unknown'} files"
            )

        for number, columns in enumerate(rows, start=1):
            yield {
                "dataset": landed.dataset or f"gdelt-{kind}",
                "row_number": number,
                "columns": columns,
            }

    def _events(self, landed: Landed) -> Iterable[dict[str, str]]:
        for row in _read_archive(landed.path):
            if not row:
                continue
            event = _named(row, EVENT_COLUMNS, landed.path)
            if is_indonesian(event):
                yield event

    def _mentions(self, landed: Landed) -> Iterable[dict[str, str]]:
        mentions = [
            _named(row, MENTION_COLUMNS, landed.path) for row in _read_archive(landed.path) if row
        ]
        archives = _events_archives(landed)
        wanted: set[str] = set()
        for stamp in {m["EventTimeDate"] for m in mentions}:
            if stamp in archives:
                wanted |= indonesian_event_ids(archives[stamp])

        for mention in mentions:
            if mention["GlobalEventID"] in wanted:
                yield mention
