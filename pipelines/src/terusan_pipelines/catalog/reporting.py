"""Writing pipeline results into the catalog.

Kept apart from the runners so acquisition and extraction still work with no
database. The lake is the system of record for data; the catalog records what
happened to it. Losing the catalog should cost history, not ingestion.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol

import psycopg
import structlog

from ..storage import Layer, StorageResolver
from ..warehouse.runlog import RunLog, RunRecord, now
from .connection import CatalogUnavailable, connect, is_available
from .datasets import DatasetStats, next_version, record_version, upsert_dataset
from .runs import definition_for_source, record_run, upsert_definition

if TYPE_CHECKING:
    from ..extract import ExtractionResult
    from ..normalize import SilverResult
    from ..sources import RunResult, SourceMeta

log = structlog.get_logger(__name__)

#: Bronze tables the extractor writes, and what to call them in the catalog.
BRONZE_DATASETS = {
    "documents": "Bronze — extracted documents",
    "records": "Bronze — extracted records",
}


class Reporter(Protocol):
    """What a runner needs from the catalog, so it can be given nothing."""

    def source_run(self, meta: SourceMeta, result: RunResult, trigger: str = ...) -> None: ...

    def extraction_run(self, result: ExtractionResult, trigger: str = ...) -> None: ...

    def normalize_run(
        self,
        result: SilverResult,
        *,
        source_id: str | None = ...,
        dataset: str | None = ...,
        trigger: str = ...,
        error: str | None = ...,
    ) -> None: ...


class NullReporter:
    """Records nothing.

    The default, so a pipeline runs on a laptop with no database. Every call is
    a no-op rather than an error: a missing catalog is a degraded mode, not a
    failure of the ingestion itself.
    """

    def source_run(self, meta: SourceMeta, result: RunResult, trigger: str = "manual") -> None:
        return None

    def extraction_run(self, result: ExtractionResult, trigger: str = "manual") -> None:
        return None

    def normalize_run(
        self,
        result: SilverResult,
        *,
        source_id: str | None = None,
        dataset: str | None = None,
        trigger: str = "manual",
        error: str | None = None,
    ) -> None:
        return None


class CatalogReporter:
    """Writes runs and dataset versions into PostgreSQL."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url

    def source_run(self, meta: SourceMeta, result: RunResult, trigger: str = "manual") -> None:
        """Record one source's acquisition run (program.md §39)."""
        with connect(self._url) as connection:
            definition_id = definition_for_source(connection, meta)
            with record_run(connection, definition_id, trigger=trigger) as handle:
                handle.records_in = result.artifacts_seen
                handle.records_out = result.artifacts_landed
                handle.bytes_written = result.bytes_written
                # The runner already caught this and turned it into a result,
                # so the run is marked failed rather than raised.
                if result.error_message:
                    handle.fail(result.error_message)

    def extraction_run(self, result: ExtractionResult, trigger: str = "manual") -> None:
        """Record an extraction run and the Bronze versions it produced."""
        from ..extract import PIPELINE_VERSION
        from ..extract.base import PARSER_VERSION

        with connect(self._url) as connection:
            definition_id = upsert_definition(
                connection,
                slug="extract-bronze",
                name="Extract RAW into Bronze",
                target_layer=Layer.BRONZE,
                definition={"kind": "extract", "parser_version": PARSER_VERSION},
            )
            with record_run(
                connection,
                definition_id,
                trigger=trigger,
                pipeline_version=PIPELINE_VERSION,
                parser_version=PARSER_VERSION,
            ) as handle:
                handle.records_in = result.documents_seen
                handle.records_out = result.rows_written
                if result.documents_failed:
                    handle.fail(
                        f"{result.documents_failed} of {result.documents_seen} "
                        "documents failed extraction"
                    )

            # Only version the Bronze datasets when something actually landed.
            # An idempotent no-op run should not manufacture a version that
            # says nothing changed (program.md §40).
            if result.rows_written:
                self._version_bronze(connection, result)

    def normalize_run(
        self,
        result: SilverResult,
        *,
        source_id: str | None = None,
        dataset: str | None = None,
        trigger: str = "manual",
        error: str | None = None,
    ) -> None:
        """Record one series' normalization run (program.md §39)."""
        from ..normalize import PIPELINE_VERSION

        with connect(self._url) as connection:
            definition_id = upsert_definition(
                connection,
                slug=f"normalize-{result.slug or result.indicator_id}",
                name=f"Normalize — {result.slug or result.indicator_id}",
                target_layer=Layer.SILVER,
                source_slug=source_id,
                definition={
                    "kind": "normalize",
                    "indicator": result.indicator_id,
                    "dataset": dataset,
                },
            )
            with record_run(
                connection,
                definition_id,
                trigger=trigger,
                pipeline_version=PIPELINE_VERSION,
            ) as handle:
                handle.records_in = result.stats.rows_in
                handle.records_out = result.observations
                handle.bytes_written = result.bytes_written
                failure = error or _silent_normalization(result)
                if failure:
                    handle.fail(failure)

    @staticmethod
    def _version_bronze(connection: psycopg.Connection, result: ExtractionResult) -> None:
        for target, title in BRONZE_DATASETS.items():
            dataset_id = upsert_dataset(
                connection,
                slug=f"bronze-{target}",
                title=title,
                layer=Layer.BRONZE,
                storage_path=target,
                partition_keys=["source_id"],
                tags=["bronze"],
            )
            record_version(
                connection,
                dataset_id,
                version=next_version(connection, dataset_id),
                change_kinds=["new_observations"],
                changelog=(
                    f"Extracted {result.documents_extracted} documents "
                    f"into {result.rows_written} rows"
                ),
                stats=DatasetStats(row_count=result.rows_written),
            )


def _silent_normalization(result: SilverResult) -> str | None:
    """Whether a run that raised nothing still produced nothing.

    A mapping that reads a Bronze table and writes no observations has failed,
    however cleanly it returned: the column moved, the filter matched nothing,
    or the period stopped parsing. Reported as a failure because the alternative
    is a green run and an empty series.
    """
    stats = result.stats
    if stats.rows_in and not stats.observations:
        return (
            f"read {stats.rows_in} Bronze row(s) and produced no observations "
            f"(excluded {stats.skipped_excluded}, no period {stats.skipped_no_period}, "
            f"unparseable {stats.unparseable_values})"
        )
    return None


class JournalReporter:
    """Writes runs into the lake's journal.

    The counterpart to `CatalogReporter`, and the one that always runs: the
    catalog is optional, and an ingestion nobody recorded is indistinguishable
    from one that never happened (see `warehouse.runlog`).
    """

    def __init__(self, resolver: StorageResolver | None = None) -> None:
        self._log = RunLog(resolver)

    def source_run(self, meta: SourceMeta, result: RunResult, trigger: str = "manual") -> None:
        started = result.started_at or now()
        self._log.record(
            RunRecord(
                pipeline=f"ingest-{meta.slug}",
                kind="ingest",
                status="failed" if result.error_message else "succeeded",
                started_at=started,
                finished_at=result.finished_at or now(),
                trigger=trigger,
                source_id=meta.slug,
                records_in=result.artifacts_seen,
                records_out=result.artifacts_landed,
                bytes_written=result.bytes_written,
                error_message=result.error_message,
                dry_run=result.dry_run,
                detail={
                    "artifacts_seen": result.artifacts_seen,
                    "artifacts_landed": result.artifacts_landed,
                    "artifacts_deduplicated": result.artifacts_deduplicated,
                    # Fetched and refused at the door: the bytes were not the
                    # format claimed. A run of these is a source that changed
                    # what it serves, and it succeeds without landing anything.
                    "artifacts_rejected": result.artifacts_rejected,
                    "traceback": result.error_traceback,
                },
            )
        )

    def extraction_run(self, result: ExtractionResult, trigger: str = "manual") -> None:
        from ..extract import PIPELINE_VERSION
        from ..extract.base import PARSER_VERSION

        error = None
        if result.documents_failed:
            error = (
                f"{result.documents_failed} of {result.documents_seen} documents failed extraction"
            )
        self._log.record(
            RunRecord(
                pipeline="extract-bronze",
                kind="extract",
                status="failed" if error else "succeeded",
                started_at=result.started_at or now(),
                finished_at=result.finished_at or now(),
                trigger=trigger,
                records_in=result.documents_seen,
                records_out=result.rows_written,
                error_message=error,
                pipeline_version=PIPELINE_VERSION,
                parser_version=PARSER_VERSION,
                detail={
                    "documents_extracted": result.documents_extracted,
                    "documents_unchanged": result.documents_unchanged,
                    "documents_skipped": result.documents_skipped,
                    "documents_failed": result.documents_failed,
                    "files_written": result.files_written,
                    # Capped: a corpus-wide parser break produces one failure
                    # per document, and the journal is a row, not a log file.
                    "failures": dict(list(result.failures.items())[:20]),
                    "unhandled": len(result.unhandled),
                },
            )
        )

    def normalize_run(
        self,
        result: SilverResult,
        *,
        source_id: str | None = None,
        dataset: str | None = None,
        trigger: str = "manual",
        error: str | None = None,
    ) -> None:
        from ..normalize import PIPELINE_VERSION

        stats = result.stats
        failure = error or _silent_normalization(result)
        self._log.record(
            RunRecord(
                pipeline=f"normalize-{result.slug or result.indicator_id}",
                kind="normalize",
                status="failed" if failure else "succeeded",
                started_at=result.started_at or now(),
                finished_at=result.finished_at or now(),
                trigger=trigger,
                source_id=source_id,
                dataset=dataset,
                indicator_id=result.indicator_id,
                records_in=stats.rows_in,
                records_out=stats.observations,
                bytes_written=result.bytes_written,
                error_message=failure,
                pipeline_version=PIPELINE_VERSION,
                dry_run=result.dry_run,
                detail={
                    "skipped_excluded": stats.skipped_excluded,
                    "skipped_no_period": stats.skipped_no_period,
                    "unresolved_geo": stats.unresolved_geo,
                    "unresolved_commodity": stats.unresolved_commodity,
                    # Read under an assumption that could have gone the other
                    # way — "1.234" as a thousand or as one and a bit.
                    "ambiguous_values": stats.ambiguous_values,
                    "unparseable_values": stats.unparseable_values,
                    "duplicate_rows": stats.duplicate_rows,
                    "revised_rows": stats.revised_rows,
                    "files_written": result.files_written,
                    "slug": result.slug,
                },
            )
        )


class Fanout:
    """Reports to several reporters, and lets none of them stop the others.

    A catalog that is down must not cost the journal its row, and a lake that
    refuses a write must not cost the catalog its history. Each reporter is
    called in its own try.
    """

    def __init__(self, *reporters: Reporter) -> None:
        self._reporters = [r for r in reporters if r is not None]

    def source_run(self, meta: SourceMeta, result: RunResult, trigger: str = "manual") -> None:
        self._each("source_run", lambda r: r.source_run(meta, result, trigger))

    def extraction_run(self, result: ExtractionResult, trigger: str = "manual") -> None:
        self._each("extraction_run", lambda r: r.extraction_run(result, trigger))

    def normalize_run(
        self,
        result: SilverResult,
        *,
        source_id: str | None = None,
        dataset: str | None = None,
        trigger: str = "manual",
        error: str | None = None,
    ) -> None:
        self._each(
            "normalize_run",
            lambda r: r.normalize_run(
                result, source_id=source_id, dataset=dataset, trigger=trigger, error=error
            ),
        )

    def _each(self, what: str, call) -> None:
        for reporter_ in self._reporters:
            try:
                call(reporter_)
            except Exception as exc:  # noqa: BLE001 - history is not worth the data
                log.warning(
                    "catalog.report_failed",
                    reporter=type(reporter_).__name__,
                    call=what,
                    error=str(exc),
                )


@contextmanager
def reporter(
    url: str | None = None, *, required: bool = False, resolver: StorageResolver | None = None
) -> Iterator[Reporter]:
    """Yield a reporter writing to the journal, and to the catalog when it answers.

    The journal is unconditional: it lives in the lake the run is already
    writing to, so there is no configuration under which a run goes unrecorded.
    `required` turns an unreachable *catalog* into an error, for scheduled runs
    where the Postgres history is expected to be there.
    """
    journal = JournalReporter(resolver)

    try:
        available = is_available(url)
    except CatalogUnavailable:
        available = False

    if available:
        yield Fanout(journal, CatalogReporter(url))
        return

    if required:
        raise CatalogUnavailable(
            "the catalog is required for this run but did not answer; run history would be lost"
        )
    log.warning("catalog.unavailable", detail="run history goes to the lake journal only")
    yield Fanout(journal)
