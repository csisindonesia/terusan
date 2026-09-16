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

from ..storage import Layer
from .connection import CatalogUnavailable, connect, is_available
from .datasets import DatasetStats, next_version, record_version, upsert_dataset
from .runs import definition_for_source, record_run, upsert_definition

if TYPE_CHECKING:
    from ..extract import ExtractionResult
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


@contextmanager
def reporter(url: str | None = None, *, required: bool = False) -> Iterator[Reporter]:
    """Yield a reporter, falling back to a null one when there is no catalog.

    `required` turns an unreachable catalog into an error, for scheduled runs
    where losing the history silently is worse than failing loudly.
    """
    try:
        available = is_available(url)
    except CatalogUnavailable:
        available = False

    if available:
        yield CatalogReporter(url)
        return

    if required:
        raise CatalogUnavailable(
            "the catalog is required for this run but did not answer; run history would be lost"
        )
    log.warning("catalog.unavailable", detail="run history will not be recorded")
    yield NullReporter()
