"""Compacting small Parquet files.

Incremental ingestion produces small files by construction: a daily run writes
one part per partition, so a year of daily runs leaves 365 parts in a partition
that should hold one or two. The writer cannot prevent this — it only sees one
run — so compaction is a separate periodic job (program.md §47).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import structlog

from ..storage import Layer, StorageResolver
from .writer import DEFAULT_ROW_GROUP_SIZE, DEFAULT_TARGET_BYTES

log = structlog.get_logger(__name__)

#: Files at or above this are left alone: rewriting them costs IO and gains
#: nothing. A quarter of the flush target is small enough to be worth merging.
SMALL_FILE_THRESHOLD = DEFAULT_TARGET_BYTES // 4


@dataclass(slots=True)
class CompactionResult:
    partition: str
    files_before: int
    files_after: int
    rows: int
    bytes_before: int
    bytes_after: int

    @property
    def files_removed(self) -> int:
        return self.files_before - self.files_after


def find_partitions(root: Path) -> list[Path]:
    """Every directory directly holding Parquet files."""
    return sorted({p.parent for p in root.rglob("*.parquet")})


def compact_partition(
    directory: Path,
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    threshold: int = SMALL_FILE_THRESHOLD,
    min_files: int = 2,
    dry_run: bool = False,
) -> CompactionResult | None:
    """Merge the small Parquet files in one partition directory.

    Returns None when there is nothing worth doing. The merge writes the new
    part first and deletes the old ones only once it is on disk, so an
    interrupted compaction leaves duplicate rows rather than missing ones —
    recoverable, where the other order is not.
    """
    parquet_files = sorted(directory.glob("*.parquet"))
    small = [p for p in parquet_files if p.stat().st_size < threshold]
    if len(small) < min_files:
        return None

    bytes_before = sum(p.stat().st_size for p in small)

    # The writer keeps partition columns inside the file as well as in the
    # path, so the file is readable on its own. Re-deriving them from the
    # directory name would yield a dictionary-typed column that will not merge
    # with the int64 one already there, so inference is turned off.
    table = pq.read_table(small, partitioning=None)

    result = CompactionResult(
        partition=str(directory),
        files_before=len(small),
        files_after=1,
        rows=table.num_rows,
        bytes_before=bytes_before,
        bytes_after=0,
    )
    if dry_run:
        return result

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    merged = directory / f"part-compacted-{stamp}-00000.parquet"
    pq.write_table(
        table,
        merged,
        compression="zstd",
        row_group_size=DEFAULT_ROW_GROUP_SIZE,
        write_statistics=True,
    )

    if merged.stat().st_size == 0:
        merged.unlink(missing_ok=True)
        raise OSError(f"compaction of {directory} produced an empty file")

    for path in small:
        path.unlink()

    result.bytes_after = merged.stat().st_size
    log.info(
        "compaction.done",
        partition=str(directory),
        merged=result.files_before,
        rows=result.rows,
        bytes_before=result.bytes_before,
        bytes_after=result.bytes_after,
    )
    return result


def compact_layer(
    resolver: StorageResolver,
    layer: Layer,
    dataset: str | None = None,
    *,
    dry_run: bool = False,
) -> list[CompactionResult]:
    """Compact every partition of a layer, or of one dataset within it."""
    root = Path(resolver.resolve(layer, dataset) if dataset else resolver.resolve(layer))
    if not root.is_dir():
        return []

    results = []
    for partition in find_partitions(root):
        result = compact_partition(partition, dry_run=dry_run)
        if result is not None:
            results.append(result)
    return results
