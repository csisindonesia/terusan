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
PARSER_VERSION = "2"


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
    source_url: str | None = None
    media_type: str | None = None
    original_filename: str | None = None
    dataset: str | None = None
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
            source_url=record.get("source_url"),
            media_type=record.get("media_type"),
            original_filename=filename,
            dataset=record.get("dataset"),
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
