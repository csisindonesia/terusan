"""Silver observations, stored in hash buckets.

The observations used to be partitioned by indicator: one directory, and one
small file, per series. That was sound at a few hundred series. The BPS
catalogue made it 31,700 directories averaging a few kilobytes each, and every
read of the whole table — the catalogue, a count, a search across series —
opened all of them: seven seconds to count rows that fit in 666 MB. program.md
§46–47 asks for coarse partition keys and large files; this is that layout.

Each series lands in one of `BUCKETS` buckets by a stable hash of its
identifier, and a bucket is one file, sorted by series and period, so a reader
after one series opens one file and skips to its row groups by their
statistics. A whole-table read opens 256 files.

Writes keep the grain they had. Normalizing a series rewrites its bucket —
the bucket's other series carried over, this one's rows replaced — under a
lock, into a temporary file renamed over the old one. A reader sees the old
bucket or the new one, never a series missing between a delete and a write,
which the previous `rmtree` then write allowed; two runs normalizing series
in the same bucket queue on its lock rather than one undoing the other.

The API computes the same bucket (FNV-1a over the identifier's UTF-8, modulo
`BUCKETS`) to read one series, and reads the layout marker written here to
know that it should. A lake half-way through `migrate_legacy` reads correctly
as long as it is read without `hive_partitioning` — the files carry
`indicator_id` and `temporal_resolution` as columns, and DuckDB refuses a glob
whose paths hold two different sets of partition keys. The API and the
pipelines' own readers of this table read it that way. Until the migration is
run, `replace` keeps writing the old layout, so no lake becomes a mixture by
accident.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import structlog

from ..storage import Layer, StorageResolver, slugify
from .writer import DEFAULT_ROW_GROUP_SIZE, ParquetWriter, WriteResult

log = structlog.get_logger(__name__)

#: How many buckets. Enough that rewriting one to replace a series is cheap
#: (a 256th of the table, about 60,000 rows today); few enough that reading
#: them all is a few hundred files rather than thousands. Changing it means
#: moving every series, so it is fixed, and the API holds the same number.
BUCKETS = 256

#: The file in the observations root that says the table is bucketed, and how.
LAYOUT_FILE = "_layout.json"
LAYOUT = {"layout": "buckets", "key": "indicator_id", "hash": "fnv1a32", "buckets": BUCKETS}

#: The one data file in a bucket.
BUCKET_FILE = "part.parquet"

#: The order within a bucket: a series' rows together, in time, so the row
#: groups' statistics on `indicator_id` let a reader skip every other series.
SORT_KEYS = [
    ("indicator_id", "ascending"),
    ("temporal_resolution", "ascending"),
    ("period_start", "ascending"),
    ("geo_id", "ascending"),
    ("commodity_id", "ascending"),
]

_LEGACY_PREFIX = "indicator_id="


def fnv1a32(text: str) -> int:
    """FNV-1a, 32-bit. The API's `hash/fnv` New32a gives the same number."""
    value = 0x811C9DC5
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def bucket_of(indicator_id: str) -> int:
    return fnv1a32(indicator_id) % BUCKETS


def bucket_name(bucket: int) -> str:
    return f"bucket={bucket:03d}"


class ObservationStore:
    """Reads and replaces series in the bucketed observations table."""

    def __init__(
        self,
        resolver: StorageResolver,
        *,
        compression: str = "zstd",
        row_group_size: int = DEFAULT_ROW_GROUP_SIZE,
    ) -> None:
        self._resolver = resolver
        self._compression = compression
        self._row_group_size = row_group_size

    # ---- writing -------------------------------------------------------

    def replace(self, indicator_id: str, table: pa.Table) -> WriteResult:
        """Replace one series' rows with `table`, whose rows are all that series.

        Into its bucket once the table is bucketed. A lake still holding the
        old per-series directories is written the old way until
        `migrate_legacy` is run on purpose: a scheduled run must not turn a
        lake into a mixture behind the back of whoever reads it.
        """
        root = self._root()
        if self.legacy(root):
            return self._replace_legacy(root, indicator_id, table)
        bucket = bucket_of(indicator_id)
        result = WriteResult(dataset="observations", layer=Layer.SILVER)
        with self._locked(root, bucket):
            existing = self._read_bucket(root, bucket)
            kept = _without(existing, {indicator_id})
            path = self._write_bucket(root, bucket, _concat([kept, table]))
            # The series' directory from the old layout, if the lake has not
            # been migrated: removed once its rows are in the bucket, so they
            # are never counted twice.
            shutil.rmtree(root / f"{_LEGACY_PREFIX}{indicator_id}", ignore_errors=True)
        self._mark(root)
        result.paths.append(str(path))
        result.rows = table.num_rows
        result.bytes_written = path.stat().st_size
        log.info(
            "observations.replaced", indicator=indicator_id, bucket=bucket, rows=table.num_rows
        )
        return result

    def remove(self, indicator_ids: set[str]) -> int:
        """Delete these series' rows. Returns how many rows went.

        Bucket by bucket under the same lock `replace` takes, so a normalization
        running beside a sweep either lands before the delete or after it,
        never half-way through a bucket rewrite.
        """
        if not indicator_ids:
            return 0
        root = self._root()
        removed = 0
        if self.legacy(root):
            for indicator_id in indicator_ids:
                directory = root / f"{_LEGACY_PREFIX}{indicator_id}"
                held = _read_directory(directory)
                removed += held.num_rows if held is not None else 0
                shutil.rmtree(directory, ignore_errors=True)
            return removed

        by_bucket: dict[int, set[str]] = defaultdict(set)
        for indicator_id in indicator_ids:
            by_bucket[bucket_of(indicator_id)].add(indicator_id)
        for bucket, series in sorted(by_bucket.items()):
            with self._locked(root, bucket):
                existing = self._read_bucket(root, bucket)
                if existing is None:
                    continue
                kept = _without(existing, series)
                if kept is None or kept.num_rows == existing.num_rows:
                    continue
                removed += existing.num_rows - kept.num_rows
                if kept.num_rows:
                    self._write_bucket(root, bucket, kept)
                else:
                    (root / bucket_name(bucket) / BUCKET_FILE).unlink(missing_ok=True)
        log.info("observations.removed", series=len(indicator_ids), rows=removed)
        return removed

    def series(self, indicator_id: str) -> pa.Table | None:
        """One series' rows, or None where it holds none."""
        root = self._root()
        if self.legacy(root):
            return _read_directory(root / f"{_LEGACY_PREFIX}{indicator_id}")
        held = self._read_bucket(root, bucket_of(indicator_id))
        if held is None:
            return None
        mask = pc.equal(held.column("indicator_id"), indicator_id)
        rows = held.filter(pc.fill_null(mask, False))
        return rows if rows.num_rows else None

    def rewrite_all(self, table: pa.Table) -> WriteResult:
        """Replace the whole table with `table`, for a migration that rewrites
        every series (a recode). Bucket by bucket, each renamed into place."""
        root = self._root()
        result = WriteResult(dataset="observations", layer=Layer.SILVER)
        by_bucket = _split_by_bucket(table)
        for bucket in range(BUCKETS):
            with self._locked(root, bucket):
                part = by_bucket.get(bucket)
                target = root / bucket_name(bucket) / BUCKET_FILE
                if part is None:
                    target.unlink(missing_ok=True)
                    continue
                path = self._write_bucket(root, bucket, part)
                result.paths.append(str(path))
                result.rows += part.num_rows
                result.bytes_written += path.stat().st_size
        for legacy in root.glob(f"{_LEGACY_PREFIX}*"):
            shutil.rmtree(legacy, ignore_errors=True)
        self._mark(root)
        return result

    def migrate_legacy(self) -> dict[str, int]:
        """Move every series still in the old per-indicator layout into its
        bucket. Resumable: a series is moved, then its directory removed, so a
        run stopped part-way leaves every series in exactly one place."""
        root = self._root()
        legacy = sorted(p for p in root.glob(f"{_LEGACY_PREFIX}*") if p.is_dir())
        grouped: dict[int, list[Path]] = defaultdict(list)
        for directory in legacy:
            grouped[bucket_of(directory.name[len(_LEGACY_PREFIX) :])].append(directory)

        moved_series = moved_rows = 0
        for bucket in sorted(grouped):
            directories = grouped[bucket]
            tables = [_read_directory(d) for d in directories]
            tables = [t for t in tables if t is not None and t.num_rows]
            series = set()
            for t in tables:
                series.update(v for v in pc.unique(t.column("indicator_id")).to_pylist() if v)
            with self._locked(root, bucket):
                kept = _without(self._read_bucket(root, bucket), series)
                self._write_bucket(root, bucket, _concat([kept, *tables]))
                for directory in directories:
                    shutil.rmtree(directory, ignore_errors=True)
            moved_series += len(directories)
            moved_rows += sum(t.num_rows for t in tables)
            log.info("observations.migrated_bucket", bucket=bucket, series=len(directories))
        self._mark(root)
        return {"series": moved_series, "rows": moved_rows, "buckets": len(grouped)}

    def legacy(self, root: Path | None = None) -> bool:
        """Whether the table still holds series in the old per-series layout."""
        root = root or self._root()
        return next(root.glob(f"{_LEGACY_PREFIX}*"), None) is not None

    # ---- internals -----------------------------------------------------

    def _replace_legacy(self, root: Path, indicator_id: str, table: pa.Table) -> WriteResult:
        for directory in root.glob(f"{_LEGACY_PREFIX}{indicator_id}"):
            shutil.rmtree(directory, ignore_errors=True)
        return ParquetWriter(
            self._resolver, compression=self._compression, row_group_size=self._row_group_size
        ).write(
            Layer.SILVER,
            "observations",
            table,
            partition_by=["indicator_id", "temporal_resolution"],
            run_id=slugify(indicator_id),
        )

    def _root(self) -> Path:
        return Path(self._resolver.resolve_for_write(Layer.SILVER, "observations"))

    def _mark(self, root: Path) -> None:
        # The root's modification time is what the API watches to know the
        # table changed; a file renamed inside a bucket does not move it.
        os.utime(root)
        marker = root / LAYOUT_FILE
        if not marker.exists():
            temporary = root / f".{LAYOUT_FILE}.{uuid.uuid4().hex}"
            temporary.write_text(json.dumps(LAYOUT))
            os.replace(temporary, marker)

    @contextmanager
    def _locked(self, root: Path, bucket: int) -> Iterator[None]:
        directory = root / bucket_name(bucket)
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / ".lock", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read_bucket(self, root: Path, bucket: int) -> pa.Table | None:
        path = root / bucket_name(bucket) / BUCKET_FILE
        if not path.exists():
            return None
        return pq.read_table(path)

    def _write_bucket(self, root: Path, bucket: int, table: pa.Table) -> Path:
        directory = root / bucket_name(bucket)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / BUCKET_FILE
        # A dot-file, which the readers' `*.parquet` globs do not match, so a
        # half-written bucket is never read.
        temporary = directory / f".{BUCKET_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            pq.write_table(
                table.sort_by(SORT_KEYS),
                temporary,
                compression=self._compression,
                row_group_size=self._row_group_size,
                write_statistics=True,
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target


def _without(table: pa.Table | None, series: set[str]) -> pa.Table | None:
    if table is None or not series:
        return table
    mask = pc.invert(pc.is_in(table.column("indicator_id"), value_set=pa.array(sorted(series))))
    return table.filter(pc.fill_null(mask, True))


def _concat(tables: list[pa.Table | None]) -> pa.Table:
    present = [t for t in tables if t is not None]
    if len(present) == 1:
        return present[0]
    # Files written before a column was added lack it; promoted to nulls.
    return pa.concat_tables(present, promote_options="default")


def _split_by_bucket(table: pa.Table) -> dict[int, pa.Table]:
    ids = table.column("indicator_id").to_pylist()
    rows: dict[int, list[int]] = defaultdict(list)
    for index, indicator in enumerate(ids):
        rows[bucket_of(str(indicator))].append(index)
    return {bucket: table.take(indices) for bucket, indices in rows.items()}


def _read_directory(directory: Path) -> pa.Table | None:
    files = sorted(directory.rglob("*.parquet"))
    if not files:
        return None
    return _concat([pq.read_table(f) for f in files])
