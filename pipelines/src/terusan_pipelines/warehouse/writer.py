"""Writing Parquet into the analytical layers.

Two failure modes this exists to prevent.

One document per file (program.md §47): a corpus of 200,000 regulations
becomes 200,000 Parquet files, each with a footer and metadata block larger
than its payload, and DuckDB spends its time opening files instead of reading
data. So the writer buffers and flushes on a size target.

Partitioning on an identifier (§46): `document_id=...` yields one directory per
row and prunes nothing, since no query filters on a value it does not already
have. Partition keys are therefore checked against the data — a key is good
when few distinct values cover many rows, which is a property of the column,
not of its name. `source_id` looks like an identifier and partitions well;
`document_id` looks the same and does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from ..storage import Layer, StorageResolver, slugify

log = structlog.get_logger(__name__)

#: Flush target. Parquet wants large row groups to make column statistics worth
#: consulting; program.md §47 asks for the hundreds-of-megabytes range where
#: the workload supports it. Uncompressed in-memory size, so on-disk files come
#: out smaller.
DEFAULT_TARGET_BYTES = 256 * 1024 * 1024

#: Rows per row group. Independent of the file target: a row group is the unit
#: DuckDB skips when statistics rule it out.
DEFAULT_ROW_GROUP_SIZE = 128 * 1024

#: Hard ceiling on directories per partition key. Beyond this the filesystem
#: and the object store both start to suffer regardless of the ratio below.
MAX_PARTITION_CARDINALITY = 10_000

#: A key prunes only when each value covers many rows. Above this ratio of
#: distinct values to rows, the partition is closer to one directory per row
#: than to a useful filter.
MAX_PARTITION_RATIO = 0.5

#: Below this many rows the ratio is meaningless — three rows from one source
#: give a ratio of 0.33 that says nothing about the column at scale.
CARDINALITY_SAMPLE_FLOOR = 100


class BadPartitionKey(ValueError):
    """A partition key that would produce one directory per row."""


@dataclass(slots=True)
class WriteResult:
    """What one write produced."""

    dataset: str
    layer: Layer
    paths: list[str] = field(default_factory=list)
    rows: int = 0
    bytes_written: int = 0

    @property
    def files(self) -> int:
        return len(self.paths)


def check_partition_keys(table: pa.Table, keys: list[str]) -> None:
    """Reject partition keys that prune nothing (program.md §46).

    Judged on the data rather than the column name. A name-based rule cannot
    separate `source_id`, which takes a few dozen values across the whole
    warehouse, from `document_id`, which takes one per row.
    """
    for key in keys:
        if key not in table.schema.names:
            raise BadPartitionKey(f"partition key {key!r} is not a column in the schema")

        distinct = len(set(table.column(key).to_pylist()))
        if distinct > MAX_PARTITION_CARDINALITY:
            raise BadPartitionKey(
                f"partition key {key!r} has {distinct} distinct values, over the "
                f"{MAX_PARTITION_CARDINALITY} ceiling; that many directories costs more "
                "than the pruning is worth (program.md §46)"
            )

        if table.num_rows >= CARDINALITY_SAMPLE_FLOOR:
            ratio = distinct / table.num_rows
            if ratio > MAX_PARTITION_RATIO:
                raise BadPartitionKey(
                    f"partition key {key!r} has {distinct} distinct values across "
                    f"{table.num_rows} rows ({ratio:.0%}); it behaves like an identifier "
                    "and prunes nothing (program.md §46)"
                )


class ParquetWriter:
    """Buffers Arrow tables and flushes them as Parquet parts.

    Not thread-safe: give each worker its own writer, or serialise around one.
    """

    def __init__(
        self,
        resolver: StorageResolver,
        *,
        target_bytes: int = DEFAULT_TARGET_BYTES,
        row_group_size: int = DEFAULT_ROW_GROUP_SIZE,
        compression: str = "zstd",
    ) -> None:
        self._resolver = resolver
        self._target_bytes = target_bytes
        self._row_group_size = row_group_size
        self._compression = compression

    def write(
        self,
        layer: Layer,
        dataset: str,
        table: pa.Table,
        *,
        partition_by: list[str] | None = None,
        run_id: str | None = None,
    ) -> WriteResult:
        """Write a table into a layer, partitioned as asked.

        `run_id` disambiguates parts written by concurrent runs. Without it two
        runs writing the same partition would both produce `part-00000` and one
        would overwrite the other.
        """
        keys = partition_by or []
        check_partition_keys(table, keys)

        result = WriteResult(dataset=dataset, layer=layer)
        stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")

        if table.num_rows == 0:
            # Still write the empty file: a missing partition reads as a gap in
            # the data, an empty one reads as "nothing happened here".
            path = self._flush(layer, dataset, table, (), stamp, 0)
            result.paths.append(path)
            result.bytes_written += Path(path).stat().st_size
            return result

        for values, chunk in self._partitions(table, keys):
            for index, part in enumerate(self._split(chunk)):
                path = self._flush(layer, dataset, part, values, stamp, index)
                result.paths.append(path)
                result.rows += part.num_rows
                result.bytes_written += Path(path).stat().st_size

        log.info(
            "parquet.written",
            layer=str(layer),
            dataset=dataset,
            files=result.files,
            rows=result.rows,
            bytes=result.bytes_written,
        )
        return result

    # ---- internals -----------------------------------------------------

    def _partitions(
        self, table: pa.Table, keys: list[str]
    ) -> list[tuple[tuple[str, ...], pa.Table]]:
        """Split a table into one chunk per distinct partition value."""
        if not keys:
            return [((), table)]

        seen: dict[tuple, list[int]] = {}
        key_columns = [table.column(k).to_pylist() for k in keys]
        for row in range(table.num_rows):
            value = tuple(col[row] for col in key_columns)
            seen.setdefault(value, []).append(row)

        chunks = []
        for value, rows in seen.items():
            segments = tuple(
                f"{key}={slugify(str(v)) if v is not None else '__null__'}"
                for key, v in zip(keys, value, strict=True)
            )
            chunks.append((segments, table.take(rows)))
        return chunks

    def _split(self, table: pa.Table) -> list[pa.Table]:
        """Break a table into pieces at the flush target.

        Uses in-memory size rather than a row count: one row of extracted PDF
        text can outweigh a thousand rows of statistics, so a fixed row count
        produces wildly uneven files.
        """
        total = table.nbytes
        if total <= self._target_bytes or table.num_rows <= 1:
            return [table]

        pieces = -(-total // self._target_bytes)  # ceiling division
        rows_each = max(1, -(-table.num_rows // pieces))
        return [table.slice(start, rows_each) for start in range(0, table.num_rows, rows_each)]

    def _flush(
        self,
        layer: Layer,
        dataset: str,
        table: pa.Table,
        partition: tuple[str, ...],
        stamp: str,
        index: int,
    ) -> str:
        directory = self._resolver.resolve_for_write(layer, slugify(dataset), *partition)
        path = Path(directory) / f"part-{stamp}-{index:05d}.parquet"
        pq.write_table(
            table,
            path,
            compression=self._compression,
            row_group_size=self._row_group_size,
            # Statistics are what let a reader skip a row group without
            # decompressing it; without them the partition pruning above is
            # the only pruning there is.
            write_statistics=True,
        )
        return str(path)
