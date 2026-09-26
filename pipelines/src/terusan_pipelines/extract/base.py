"""Turning landed files into Bronze rows.

An extractor reads one artifact out of RAW and produces machine-readable
records. It does not clean, reconcile or type them — Bronze may be imperfect
and partially normalized (program.md §6); deciding what a value means is
Silver's job.

The split matters because extraction is the step most likely to be wrong and
re-run. RAW keeps the original, so a better parser can be pointed at the same
bytes without going back to a portal that has since changed.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

#: Bumped when an extractor's output changes in a way that would alter Bronze.
#: Recorded per row so a partition can be traced to the code that produced it
#: and selectively reprocessed (program.md §17).
#:
#: Extraction is idempotent on (document_id, parser_version), so bumping this is
#: what makes an improved parser actually run again. Forgetting to bump it
#: leaves the old rows in place and the change simply does not take — which is
#: how the World Bank GDP series kept a `gdp_usd` column after the extractor had
#: been generalised to emit `value`.
#:
#: 2: World Bank value column renamed from `gdp_usd` to `value`, so one
#:    extractor could serve every series.
#: 3: Workbooks and archives are read rather than skipped, and Bank Indonesia's
#:    surveys go through their own parsers instead of the generic cell reader.
#: 4: SpreadsheetML is read rather than skipped, and it carries the landing
#:    metadata onto each row — DJPK's APBD export names no fiscal year inside
#:    the file, so rows extracted before this have no period at all.
#: 5: Trading Economics indicator pages are read as records — latest, previous,
#:    all-time high and low, and the release calendar — instead of landing as
#:    one blob of prose through the generic HTML reader.
#: 6: Trading Economics pages that carry no summary block — the manufacturing
#:    PMI among them — are read from their own row in the category table, and
#:    the credit rating page's agencies, outlooks and dates are read at all.
#: 7: Trading Economics rows were meant to carry the country the page is for,
#:    without which they cannot be resolved to a place in Silver. The bump
#:    landed; the column did not, so 7 holds nothing 6 did not.
#: 8: Trading Economics rows carry that country, and the index lands under a
#:    dataset a reader would recognise. Extraction is idempotent on document
#:    and parser version, so the renamed dataset needs this bump to be read at
#:    all: the bytes are the ones already extracted under the old name.
#: 11: HDX's two districts called `Banjar` are told apart by their GADM code.
#:     GADM gives Kabupaten Banjar and Kota Banjar the same name, and with the
#:     code no longer in the label they resolved to one place — which Silver
#:     refuses, so the source normalized to nothing at all.
#: 10: HDX's movement distribution names its district by the name GADM gives
#:     it, without the GADM code folded into the label. The code is already
#:     its own column, and the geography registry now holds the regencies and
#:     cities the names belong to — so the label resolves instead of being
#:     preserved unresolved.
#: 9: FRED's series downloads are read as observations — the figure, the date
#:    restated at the series' own frequency, and the title, units and
#:    frequency the search listing carried — instead of landing through the
#:    generic CSV reader, which names the value column after the series and so
#:    cannot be mapped.
#: 12: PIHPS food prices are read by their own extractor, which carries the
#:     province and the market type — traditional, modern, wholesale,
#:     farmgate — onto every row. Both were query parameters rather than
#:     columns, so rows extracted before this cannot say which place or
#:     which market they describe, and all four markets' prices for a food
#:     were indistinguishable from four readings of one series.
#: 13: PIHPS rows name the indicator they belong to and what it is called,
#:     so the four markets are split and named by `normalize-each` — which
#:     is what publishes the indicators table. Without it the series have
#:     figures and no name, and a reader searching for food prices finds
#:     nothing.
#: 14: BNPB's disaster tables are read from the CKAN datastore pages that
#:     carry them, instead of landing through the generic JSON reader — which
#:     sees CKAN's envelope as one object and makes a whole table of province
#:     figures into a single Bronze row.
#: 15: BNPB's province-by-hazard impact tables are read as one record per
#:     province and hazard, naming the series each figure belongs to. Read as
#:     cells they were a row of nine numbers meaning nine different things, and
#:     neither the measure — which is in the resource's title — nor the year —
#:     which is in the CKAN dataset, not the file — was on the row at all.
#: 16: BNPB's Papua province codes are corrected to BPS's. BNPB numbers the
#:     six provinces created in 2022 in an order of its own, so every one of
#:     them resolved to a neighbour rather than failing — Papua Tengah's
#:     casualties were filed under Papua.
#: 17: Yahoo's daily bars are dated by the exchange's own clock rather than by
#:     UTC. Foreign exchange sits on Europe/London and is stamped at local
#:     midnight, which under British Summer Time is 23:00 UTC the day before —
#:     so every summer rate was dated a day early and a trading week ran from
#:     Sunday to Thursday. No other Yahoo instrument moves: Jakarta and New
#:     York are both stamped at an hour that already falls on the session's
#:     own date. The running quote Yahoo appends for a session it is still
#:     pricing folds into that session's bar, which it duplicated — Silver
#:     refuses a period holding two figures, so the pair failed to normalize
#:     at all once both readings landed on the same date.
#: 18: News pages collected by `news-monitoring` are read into the corpus and
#:     coded against an issue profile, instead of landing through the generic
#:     HTML reader — which sees an article as one blob of prose and carries
#:     neither the outlet, nor the publication date, nor anything coded.
#: 19: The news crawl reads everything an outlet published and keeps only what
#:     is about the issue, so each run also lands a tally of what it read. The
#:     tallies are the denominator every rate needs: without them a rise in
#:     incidents cannot be told from a crawl that reached further.
#: 20: A province a report names is trusted over the one a regency's parent
#:     implies, and a place name inside a longer place name no longer matches
#:     twice. Every regency of the six Papua provinces is filed in the
#:     geography reference under Papua Barat, so following the parent put
#:     Nabire, Mimika, Jayawijaya and Asmat there — two-fifths of the coded
#:     incidents, in a province holding none of them.
#: 21: Two corrections to what the news monitor records.
#:
#:     A regency whose parent province is one the geography reference gets
#:     wrong no longer implies a province at all, where the report does not
#:     name one. Fifteen incidents in Papua's highlands were being filed in
#:     Papua Barat, which holds none of those districts.
#:
#:     And the gate now refuses follow-up reporting. Asked whether an article
#:     reports collective violence, the classifier said yes to "investigators
#:     will take statements" and "police have examined 23 witnesses" — true
#:     of the article, and wrong for a dataset of incidents, which would have
#:     counted one brawl again every time it reached court. Those score 0.08
#:     under the revised wording, against 0.96 before, and a real incident is
#:     unmoved at 0.96.
#: 22: DJPK's APBD exports are read by their own extractor — one record per
#:     headline line, budget and realisation apart, naming the government and
#:     the fiscal month the landing record carries — instead of as SpreadsheetML
#:     cells that say neither. GDELT's archives are read at all: the archive
#:     reader claimed them and found no workbook.
PARSER_VERSION = "22"


class ExtractionError(Exception):
    """An artifact could not be extracted.

    Carries the RAW path, because a failure that does not say which document
    failed is not actionable across a corpus of hundreds of thousands.
    """

    def __init__(self, raw_path: str, message: str) -> None:
        super().__init__(f"{raw_path}: {message}")
        self.raw_path = raw_path


@dataclass(frozen=True, slots=True)
class Landed:
    """One artifact as found in RAW, with its provenance sidecar.

    Built by reading a `metadata.json` written at landing time, so extraction
    inherits provenance rather than re-deriving it.
    """

    path: Path
    document_id: str
    content_hash: str
    source_slug: str
    source_type: str | None = None
    #: Who issued the material, as the source registry names them. Carried
    #: because it is the document's publisher, which is not the same fact as
    #: where we collected it from.
    source_organization: str | None = None
    source_url: str | None = None
    media_type: str | None = None
    original_filename: str | None = None
    dataset: str | None = None
    #: The RAW partition the artifact landed under — ("edition=2025",). How a
    #: publisher's own editions are told apart, which the filename often does
    #: not say.
    partition: tuple[str, ...] = ()
    size_bytes: int | None = None
    published_at: date | None = None
    retrieved_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_metadata(cls, metadata_path: Path) -> Landed:
        """Read a landed artifact from its sidecar."""
        record = json.loads(metadata_path.read_text())
        directory = metadata_path.parent
        filename = record.get("original_filename")
        content = directory / filename if filename else _sole_content_file(directory)

        return cls(
            path=content,
            document_id=record["document_id"],
            content_hash=record["content_hash"],
            source_slug=record["source"]["slug"],
            source_type=record["source"].get("source_type"),
            source_organization=record["source"].get("organization"),
            source_url=record.get("source_url"),
            media_type=record.get("media_type"),
            original_filename=filename,
            dataset=record.get("dataset"),
            partition=tuple(record.get("partition") or ()),
            size_bytes=record.get("size_bytes"),
            published_at=_parse_date(record.get("published_at")),
            retrieved_at=_parse_datetime(record.get("retrieved_at")),
            extra=record.get("extra", {}),
        )

    def provenance(self, raw_root: str, pipeline_version: str) -> dict[str, Any]:
        """The provenance columns every Bronze row carries (program.md §6)."""
        return {
            "document_id": self.document_id,
            "source_id": self.source_slug,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "content_hash": self.content_hash,
            # Stored relative to the lake root so Bronze survives the lake
            # moving between NAS and object storage (program.md §45.4).
            "raw_path": str(self.path).removeprefix(raw_root).lstrip("/"),
            "original_filename": self.original_filename,
            "media_type": self.media_type,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "processed_at": datetime.now(UTC),
            "parser_version": PARSER_VERSION,
            "pipeline_version": pipeline_version,
        }


class Extractor(ABC):
    """Reads one landed artifact into Bronze rows."""

    #: Which Bronze table the rows belong in: "documents" or "records".
    target: str = "documents"

    @abstractmethod
    def handles(self, landed: Landed) -> bool:
        """Whether this extractor can read the artifact."""

    @abstractmethod
    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        """Yield Bronze rows, without the provenance columns.

        The runner merges provenance in, so an extractor cannot get it wrong
        or leave it out.
        """


def _sole_content_file(directory: Path) -> Path:
    """Find the artifact in a landing directory that is not the sidecar."""
    candidates = [p for p in directory.iterdir() if p.is_file() and p.name != "metadata.json"]
    if len(candidates) != 1:
        raise ExtractionError(
            str(directory), f"expected exactly one content file, found {len(candidates)}"
        )
    return candidates[0]


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
