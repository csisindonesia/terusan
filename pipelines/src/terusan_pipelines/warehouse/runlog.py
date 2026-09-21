"""The run journal: what ran, when, and how it ended (program.md §39, §50).

PostgreSQL is where run history belongs (§48), and `catalog.runs` writes it
there. This writes the same history into the lake as well, for one reason: the
catalog is optional. A laptop with no Postgres running records nothing, and
"nothing recorded" and "nothing ran" look identical three weeks later — which
is precisely the failure run history exists to catch.

So the journal follows the rule the source registry already follows: an
operational question a reader can ask of the serving layer must not depend on a
database being up. One Parquet part per run under

    silver/pipeline_runs/date=2026-09-19/part-<run>-00000.parquet

which is a small file per run by design — a run is a single row, and waiting to
batch them would lose exactly the runs that died. `terusan warehouse compact
silver pipeline_runs` folds a month of them together when the count starts to
matter.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import structlog

from ..storage import Layer, StorageResolver
from .writer import ParquetWriter

log = structlog.get_logger(__name__)

#: Where the journal lives. Silver rather than Gold: it sits beside the source
#: registry and the document catalogue, which are the other things the serving
#: layer answers about the warehouse rather than out of it.
DATASET = "pipeline_runs"
LAYER = Layer.SILVER

#: Declared rather than inferred. A run row is written one at a time, so an
#: absent optional field would otherwise change a column's type from file to
#: file and a reader unioning them would get nulls where values are.
SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string(), nullable=False),
        # The pipeline that ran: `ingest-bps`, `extract-bronze`,
        # `normalize-bi-food-prices`. Same slug the catalog's
        # `pipeline_definitions` uses, so the two histories line up.
        pa.field("pipeline", pa.string(), nullable=False),
        # ingest | extract | normalize. What kind of work it was, for a reader
        # asking "did the scraper run" rather than "did this pipeline run".
        pa.field("kind", pa.string(), nullable=False),
        # succeeded | failed. A run that never finished writes nothing at all,
        # which is why `started_at` is recorded and staleness is derivable.
        pa.field("status", pa.string(), nullable=False),
        pa.field("trigger", pa.string(), nullable=False),
        # What the run touched. Null where the stage has no opinion: extraction
        # reads every source at once and names none of them.
        pa.field("source_id", pa.string()),
        pa.field("dataset", pa.string()),
        pa.field("indicator_id", pa.string()),
        pa.field("started_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("finished_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("duration_seconds", pa.float64(), nullable=False),
        # Counted the same way at every stage: what came in, what went out.
        # `records_out` is the number a reader checks — an ingestion that
        # succeeded and landed nothing is the quiet failure.
        pa.field("records_in", pa.int64(), nullable=False),
        pa.field("records_out", pa.int64(), nullable=False),
        pa.field("bytes_written", pa.int64(), nullable=False),
        pa.field("error_message", pa.string()),
        # Stage-specific counters as JSON text rather than a struct: each stage
        # counts different things, and a struct would make every new counter a
        # schema change that older files no longer match.
        pa.field("detail", pa.string()),
        pa.field("pipeline_version", pa.string()),
        pa.field("parser_version", pa.string()),
        # A dry run lands nothing. Recorded so its zero does not read as a
        # failed ingestion.
        pa.field("dry_run", pa.bool_(), nullable=False),
        # How it was started and where, which is the other half of an audit:
        # a scheduled run and someone's terminal produce the same row
        # otherwise.
        pa.field("command", pa.string()),
        pa.field("host", pa.string()),
        pa.field("storage_profile", pa.string()),
        # Partition key. A day of runs is the unit anyone reads.
        pa.field("date", pa.string(), nullable=False),
    ]
)

#: How much of a traceback is worth keeping in a column. The whole thing is in
#: the process log; this is here so the portal can show why a run failed
#: without anyone opening a log file.
MAX_ERROR_CHARS = 2000


@dataclass(slots=True)
class RunRecord:
    """One journal row, as a stage reports it."""

    pipeline: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime

    trigger: str = "manual"
    source_id: str | None = None
    dataset: str | None = None
    indicator_id: str | None = None

    records_in: int = 0
    records_out: int = 0
    bytes_written: int = 0

    error_message: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    pipeline_version: str | None = None
    parser_version: str | None = None
    dry_run: bool = False

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def duration_seconds(self) -> float:
        return max((self.finished_at - self.started_at).total_seconds(), 0.0)

    def row(self) -> dict[str, Any]:
        """The record as one Parquet row."""
        started = _utc(self.started_at)
        error = self.error_message
        if error and len(error) > MAX_ERROR_CHARS:
            error = error[:MAX_ERROR_CHARS] + "… (truncated)"
        return {
            "run_id": self.run_id,
            "pipeline": self.pipeline,
            "kind": self.kind,
            "status": self.status,
            "trigger": self.trigger,
            "source_id": self.source_id,
            "dataset": self.dataset,
            "indicator_id": self.indicator_id,
            "started_at": started,
            "finished_at": _utc(self.finished_at),
            "duration_seconds": self.duration_seconds,
            "records_in": int(self.records_in),
            "records_out": int(self.records_out),
            "bytes_written": int(self.bytes_written),
            "error_message": error,
            "detail": json.dumps(self.detail, default=str) if self.detail else None,
            "pipeline_version": self.pipeline_version,
            "parser_version": self.parser_version,
            "dry_run": self.dry_run,
            "command": _command(),
            "host": socket.gethostname(),
            "storage_profile": os.getenv("STORAGE_PROFILE", "local"),
            "date": started.strftime("%Y-%m-%d"),
        }


class RunLog:
    """Appends runs to the journal in the lake."""

    def __init__(
        self, resolver: StorageResolver | None = None, *, writer: ParquetWriter | None = None
    ) -> None:
        self._resolver = resolver or StorageResolver()
        self._writer = writer or ParquetWriter(self._resolver)

    def record(self, record: RunRecord) -> str | None:
        """Write one run. Returns the part written, or None if it could not be.

        A journal that cannot be written must not take the pipeline down with
        it: the run already happened, and failing here would turn a recorded
        success into an unrecorded failure. The reason is logged instead.
        """
        table = pa.Table.from_pylist([record.row()], schema=SCHEMA)
        try:
            result = self._writer.write(LAYER, DATASET, table, partition_by=["date"])
        except Exception as exc:  # noqa: BLE001 — see the docstring
            log.warning(
                "runlog.unwritable",
                pipeline=record.pipeline,
                status=record.status,
                error=str(exc),
            )
            return None
        log.info(
            "runlog.recorded",
            pipeline=record.pipeline,
            status=record.status,
            records_out=record.records_out,
        )
        return result.paths[0] if result.paths else None


def read_runs(
    resolver: StorageResolver | None = None,
    *,
    limit: int = 20,
    pipeline: str | None = None,
    source_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Recent runs, newest first. The terminal's half of the portal's Logs tab."""
    from pathlib import Path

    from .query import warehouse

    resolver = resolver or StorageResolver()
    if not any(Path(resolver.resolve(LAYER, DATASET)).rglob("*.parquet")):
        return []

    where: list[str] = []
    params: list[Any] = []
    if pipeline:
        where.append("pipeline = ?")
        params.append(pipeline)
    if source_id:
        where.append("source_id = ?")
        params.append(source_id)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    # The instants come back as epoch milliseconds and are turned into UTC
    # datetimes here. DuckDB hands a TIMESTAMP WITH TIME ZONE to Python through
    # pytz, which is not a dependency of this project, and rendering one as text
    # would silently apply the reader's local timezone instead.
    with warehouse(resolver) as db:
        relation = db.query(
            f"SELECT * REPLACE (epoch_ms(started_at) AS started_at, "
            f"epoch_ms(finished_at) AS finished_at) "
            f"FROM {db.source(LAYER, DATASET)} {clause} "
            f"ORDER BY started_at DESC LIMIT {int(limit)}",
            params or None,
        )
        columns = relation.columns
        runs = [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]

    for run in runs:
        for column in ("started_at", "finished_at"):
            run[column] = datetime.fromtimestamp(run[column] / 1000, UTC)
    return runs


def _utc(value: datetime) -> datetime:
    """A timezone-aware UTC instant. A naive one is read as local time."""
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _command() -> str:
    """The invocation, shortened to the part that says what was asked for."""
    argv = list(sys.argv)
    if argv:
        argv[0] = os.path.basename(argv[0])
    return " ".join(argv)[:500]


def now() -> datetime:
    return datetime.now(UTC)
