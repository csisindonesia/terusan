"""Walking RAW and writing Bronze.

Extraction is where a corpus goes wrong one document at a time: a malformed
PDF, an encoding nobody anticipated, a portal that served an error page with a
200. So a failure here is recorded against the document and the walk
continues. A run that stops at the first bad file cannot process a corpus.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..storage import Layer, StorageResolver
from ..warehouse import (
    BRONZE_DOCUMENTS,
    BRONZE_RECORDS,
    ParquetWriter,
    Warehouse,
    table_from_rows,
)
from .base import PARSER_VERSION, ExtractionError, Extractor, Landed
from .documents import HtmlExtractor, PdfExtractor, TextExtractor
from .tabular import CsvExtractor, JsonExtractor
from .worldbank import WorldBankExtractor

if TYPE_CHECKING:
    from ..catalog import Reporter

log = structlog.get_logger(__name__)

#: Bumped when the extraction pipeline itself changes shape.
PIPELINE_VERSION = "1"

#: Order matters: the first extractor claiming an artifact wins. Source-specific
#: readers come first — a source publishing its own envelope is not readable by
#: a generic one — then formats, then the catch-all text reader.
DEFAULT_EXTRACTORS: tuple[Extractor, ...] = (
    WorldBankExtractor(),
    PdfExtractor(),
    HtmlExtractor(),
    JsonExtractor(),
    CsvExtractor(),
    TextExtractor(),
)

#: Bronze table name to Arrow schema.
TARGET_SCHEMAS = {"documents": BRONZE_DOCUMENTS, "records": BRONZE_RECORDS}


@dataclass(slots=True)
class ExtractionResult:
    documents_seen: int = 0
    documents_extracted: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    #: Already present in Bronze at this parser version, so not re-extracted.
    documents_unchanged: int = 0
    rows_written: int = 0
    files_written: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    #: Artifacts no extractor claimed, keyed by path. Usually a format nobody
    #: has written a reader for yet, which is worth seeing rather than
    #: silently dropping.
    unhandled: dict[str, str] = field(default_factory=dict)


def walk_raw(resolver: StorageResolver, *segments: str) -> Iterator[Landed]:
    """Yield every landed artifact under a RAW subtree.

    Driven by the sidecars rather than by the content files, so anything landed
    without provenance is invisible here — which is the correct outcome: a
    document with no recorded origin cannot be admitted to Bronze.
    """
    root = Path(resolver.resolve(Layer.RAW, *segments))
    if not root.is_dir():
        return
    for metadata_path in sorted(root.rglob("metadata.json")):
        try:
            yield Landed.from_metadata(metadata_path)
        except (ExtractionError, KeyError, ValueError) as exc:
            log.warning("extract.bad_sidecar", path=str(metadata_path), error=str(exc))


class ExtractionRunner:
    """Reads RAW into Bronze."""

    def __init__(
        self,
        resolver: StorageResolver,
        extractors: tuple[Extractor, ...] = DEFAULT_EXTRACTORS,
        *,
        writer: ParquetWriter | None = None,
        reporter: Reporter | None = None,
        trigger: str = "manual",
    ) -> None:
        self._resolver = resolver
        self._extractors = extractors
        self._writer = writer or ParquetWriter(resolver)
        self._reporter = reporter
        self._trigger = trigger

    def run(
        self,
        *segments: str,
        run_id: str | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        reprocess: bool = False,
    ) -> ExtractionResult:
        """Extract a RAW subtree into Bronze.

        Idempotent: a document already in Bronze at the current parser version
        is left alone, so a second run over an unchanged corpus writes nothing.
        Without that, every run would append another copy of every row and
        Bronze would grow by a full corpus each time it was refreshed.

        `reprocess` forces re-extraction — what to use after improving a parser,
        alongside bumping `PARSER_VERSION`.

        Rows are collected per target table and written once at the end, so a
        run over ten thousand small documents produces two Parquet files rather
        than ten thousand (program.md §47).
        """
        result = ExtractionResult()
        raw_root = self._resolver.resolve(Layer.RAW)
        batches: dict[str, list[dict]] = {name: [] for name in TARGET_SCHEMAS}
        already = set() if reprocess else self._already_extracted()

        for landed in walk_raw(self._resolver, *segments):
            result.documents_seen += 1

            if landed.document_id in already:
                result.documents_unchanged += 1
                continue

            extractor = self._extractor_for(landed)

            if extractor is None:
                result.documents_skipped += 1
                result.unhandled[str(landed.path)] = landed.path.suffix or "no suffix"
                continue

            try:
                provenance = landed.provenance(raw_root, PIPELINE_VERSION)
                rows = [{**provenance, **row} for row in extractor.extract(landed)]
            except ExtractionError as exc:
                result.documents_failed += 1
                result.failures[str(landed.path)] = str(exc)
                log.warning("extract.failed", path=str(landed.path), error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the walk
                result.documents_failed += 1
                result.failures[str(landed.path)] = f"{type(exc).__name__}: {exc}"
                log.warning("extract.failed", path=str(landed.path), error=str(exc))
                continue

            batches[extractor.target].extend(rows)
            result.documents_extracted += 1
            result.rows_written += len(rows)

            if limit is not None and result.documents_extracted >= limit:
                break

        if not dry_run:
            for target, rows in batches.items():
                if not rows:
                    continue
                written = self._write(target, rows, run_id)
                result.files_written += written

        if not dry_run and self._reporter is not None:
            try:
                self._reporter.extraction_run(result, self._trigger)
            except Exception as exc:  # noqa: BLE001 - Bronze is already written
                log.warning("catalog.report_failed", error=str(exc))

        log.info(
            "extract.finished",
            seen=result.documents_seen,
            extracted=result.documents_extracted,
            unchanged=result.documents_unchanged,
            skipped=result.documents_skipped,
            failed=result.documents_failed,
            rows=result.rows_written,
        )
        return result

    def _already_extracted(self) -> set[str]:
        """Document ids already in Bronze at the current parser version.

        Read from Bronze itself rather than from a side table: Bronze is the
        thing being kept consistent, and a separate ledger could disagree with
        it after a partial write.
        """
        seen: set[str] = set()
        for target in TARGET_SCHEMAS:
            root = Path(self._resolver.resolve(Layer.BRONZE, target))
            if not any(root.rglob("*.parquet")):
                continue
            pattern = self._resolver.glob(Layer.BRONZE, target)
            with Warehouse(self._resolver) as wh:
                rows = wh.query(
                    "SELECT DISTINCT document_id FROM "
                    f"read_parquet('{pattern}', union_by_name=true) "
                    "WHERE parser_version = ?",
                    [PARSER_VERSION],
                ).fetchall()
            seen.update(row[0] for row in rows)
        return seen

    def _extractor_for(self, landed: Landed) -> Extractor | None:
        for extractor in self._extractors:
            if extractor.handles(landed):
                return extractor
        return None

    def _write(self, target: str, rows: list[dict], run_id: str | None) -> int:
        table = table_from_rows(rows, TARGET_SCHEMAS[target])
        written = self._writer.write(
            Layer.BRONZE,
            target,
            table,
            # Source and retrieval date both prune: most Bronze reads are
            # "this source" or "what arrived since". Neither is an identifier
            # (program.md §46).
            partition_by=["source_id"],
            run_id=run_id,
        )
        return written.files
