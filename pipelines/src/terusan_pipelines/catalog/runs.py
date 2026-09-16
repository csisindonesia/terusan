"""Recording what ran, when, and what it produced (program.md §39).

A run row is written before the work starts, not after. A process killed
mid-run would otherwise leave no trace at all — and an ingestion that silently
stopped running three weeks ago is the failure mode this table exists to catch.
The row starts as `running` and is closed out in a `finally`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import structlog

from ..sources import SourceMeta
from ..storage import Layer

log = structlog.get_logger(__name__)

_UPSERT_DEFINITION = """
INSERT INTO pipeline_definitions (
    slug, name, source_id, target_layer, definition, schedule, enabled
)
VALUES (
    %(slug)s, %(name)s, %(source_id)s, %(target_layer)s,
    %(definition)s, %(schedule)s, %(enabled)s
)
ON CONFLICT (slug) DO UPDATE SET
    name         = EXCLUDED.name,
    source_id    = EXCLUDED.source_id,
    target_layer = EXCLUDED.target_layer,
    definition   = EXCLUDED.definition,
    schedule     = EXCLUDED.schedule,
    enabled      = EXCLUDED.enabled,
    updated_at   = now()
RETURNING id
"""

_OPEN_RUN = """
INSERT INTO pipeline_runs (
    pipeline_definition_id, status, trigger, pipeline_version, parser_version, started_at
)
VALUES (
    %(definition_id)s, 'running', %(trigger)s,
    %(pipeline_version)s, %(parser_version)s, now()
)
RETURNING id
"""

_CLOSE_RUN = """
UPDATE pipeline_runs SET
    status        = %(status)s,
    records_in    = %(records_in)s,
    records_out   = %(records_out)s,
    bytes_written = %(bytes_written)s,
    error_message = %(error_message)s,
    log_path      = %(log_path)s,
    finished_at   = now()
WHERE id = %(run_id)s
"""


@dataclass(slots=True)
class RunHandle:
    """An open run row, to be closed out when the work finishes."""

    run_id: UUID
    definition_id: UUID
    records_in: int = 0
    records_out: int = 0
    bytes_written: int = 0
    log_path: str | None = None
    error: str | None = None

    def fail(self, message: str) -> None:
        """Mark this run failed without raising.

        Most pipeline failures here are already caught and turned into a
        result — a broken scraper is an expected outcome, not an exception —
        so the run needs a way to be recorded as failed without unwinding a
        stack that is not unwinding.
        """
        self.error = message


def upsert_definition(
    connection: psycopg.Connection,
    *,
    slug: str,
    name: str,
    target_layer: Layer,
    source_slug: str | None = None,
    definition: dict[str, Any] | None = None,
    schedule: str | None = None,
    enabled: bool = True,
) -> UUID:
    """Ensure a pipeline definition exists, returning its id.

    The definition body is stored alongside the runs so a run can be read
    against the definition that produced it rather than the current one
    (program.md §38).
    """
    source_id = None
    if source_slug:
        row = connection.execute(
            "SELECT id FROM sources WHERE slug = %s", (source_slug,)
        ).fetchone()
        source_id = row["id"] if row else None

    row = connection.execute(
        _UPSERT_DEFINITION,
        {
            "slug": slug,
            "name": name,
            "source_id": source_id,
            "target_layer": str(target_layer),
            "definition": json.dumps(definition or {}),
            "schedule": schedule,
            "enabled": enabled,
        },
    ).fetchone()
    assert row is not None
    return row["id"]


def definition_for_source(connection: psycopg.Connection, meta: SourceMeta) -> UUID:
    """The ingestion pipeline belonging to one source.

    Every source is a pipeline: it has a schedule, it produces runs, and it
    lands in a layer. Modelling it as one means run history is uniform across
    acquisition and transformation rather than split into two shapes.
    """
    return upsert_definition(
        connection,
        slug=f"ingest-{meta.slug}",
        name=f"Ingest — {meta.name}",
        target_layer=Layer.RAW,
        source_slug=meta.slug,
        definition={
            "kind": "ingest",
            "source": meta.slug,
            "collection_method": str(meta.collection_method),
            "max_requests_per_second": meta.max_requests_per_second,
        },
        schedule=meta.schedule,
        enabled=meta.active,
    )


@contextmanager
def record_run(
    connection: psycopg.Connection,
    definition_id: UUID,
    *,
    trigger: str = "manual",
    pipeline_version: str | None = None,
    parser_version: str | None = None,
) -> Iterator[RunHandle]:
    """Open a run row, close it out however the block ends.

    Commits the opening insert immediately so the `running` row is visible to
    anything watching while the work is still in flight. A crash then leaves a
    run stuck in `running`, which is the honest record — better than no row at
    all, and detectable by age.
    """
    row = connection.execute(
        _OPEN_RUN,
        {
            "definition_id": definition_id,
            "trigger": trigger,
            "pipeline_version": pipeline_version,
            "parser_version": parser_version,
        },
    ).fetchone()
    assert row is not None
    connection.commit()

    handle = RunHandle(run_id=row["id"], definition_id=definition_id)
    raised: str | None = None

    try:
        yield handle
    except Exception as exc:
        raised = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        error = raised or handle.error
        connection.execute(
            _CLOSE_RUN,
            {
                "run_id": handle.run_id,
                "status": "failed" if error else "succeeded",
                "records_in": handle.records_in,
                "records_out": handle.records_out,
                "bytes_written": handle.bytes_written,
                "error_message": error,
                "log_path": handle.log_path,
            },
        )
        connection.commit()
        log.info(
            "catalog.run_recorded",
            run_id=str(handle.run_id),
            status="failed" if error else "succeeded",
        )


def fail_run(connection: psycopg.Connection, run_id: UUID, message: str) -> None:
    """Mark an open run failed without raising.

    For a run whose failure was already captured as a result rather than an
    exception, which is how the source runner reports a broken scraper.
    """
    connection.execute(
        _CLOSE_RUN,
        {
            "run_id": run_id,
            "status": "failed",
            "records_in": 0,
            "records_out": 0,
            "bytes_written": 0,
            "error_message": message,
            "log_path": None,
        },
    )


def recent_runs(
    connection: psycopg.Connection, *, limit: int = 20, slug: str | None = None
) -> list[dict[str, Any]]:
    """Run history, newest first."""
    sql = """
        SELECT r.id, d.slug, r.status, r.trigger, r.records_out, r.bytes_written,
               r.error_message, r.started_at, r.finished_at
        FROM pipeline_runs r
        JOIN pipeline_definitions d ON d.id = r.pipeline_definition_id
    """
    params: list[Any] = []
    if slug:
        sql += " WHERE d.slug = %s"
        params.append(slug)
    sql += " ORDER BY r.created_at DESC LIMIT %s"
    params.append(limit)
    return connection.execute(sql, params).fetchall()


def stale_runs(connection: psycopg.Connection, *, hours: int = 6) -> list[dict[str, Any]]:
    """Runs still marked running well past any plausible duration.

    These are processes that died without closing their row. Worth alerting on:
    the alternative is an ingestion that stopped weeks ago and nobody noticed.
    """
    return connection.execute(
        """
        SELECT r.id, d.slug, r.started_at
        FROM pipeline_runs r
        JOIN pipeline_definitions d ON d.id = r.pipeline_definition_id
        WHERE r.status = 'running' AND r.started_at < now() - make_interval(hours => %s)
        ORDER BY r.started_at
        """,
        (hours,),
    ).fetchall()


def now() -> datetime:
    return datetime.now(UTC)
