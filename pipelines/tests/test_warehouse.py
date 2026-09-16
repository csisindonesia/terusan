"""Parquet writing, compaction and DuckDB reads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import (
    BRONZE_DOCUMENTS,
    BadPartitionKey,
    ParquetWriter,
    Warehouse,
    check_partition_keys,
    compact_layer,
    compact_partition,
    conform,
    empty,
    find_partitions,
)


@pytest.fixture
def resolver(tmp_path: Path) -> StorageResolver:
    return StorageResolver(
        StorageConfig(
            STORAGE_PROFILE="local",
            STORAGE_BACKEND="local",
            STORAGE_ROOT=str(tmp_path / "data"),
            SCRATCH_DIR=str(tmp_path / "cache"),
        )
    )


def observations(rows: int = 6, year: int = 2026) -> pa.Table:
    return pa.table(
        {
            "observation_id": [f"obs-{i}" for i in range(rows)],
            "indicator": ["inflation"] * rows,
            "year": [year + (i % 2) for i in range(rows)],
            "value": [1.0 * i for i in range(rows)],
        }
    )


# ---- partition key validation (program.md §46) ----------------------------


def test_partition_keys_must_exist_in_the_schema():
    with pytest.raises(BadPartitionKey, match="not a column"):
        check_partition_keys(observations(), ["quarter"])


def test_a_unique_column_is_rejected_as_a_partition_key():
    """One directory per row prunes nothing."""
    table = pa.table({"observation_id": [f"obs-{i}" for i in range(200)]})
    with pytest.raises(BadPartitionKey, match="behaves like an identifier"):
        check_partition_keys(table, ["observation_id"])


def test_a_low_cardinality_id_column_is_accepted():
    """`source_id` names like an identifier and partitions well.

    This is why the rule reads the data rather than the column name.
    """
    table = pa.table({"source_id": ["bps"] * 150 + ["bi"] * 50})
    check_partition_keys(table, ["source_id"])


def test_very_high_cardinality_is_rejected_whatever_the_ratio():
    table = pa.table({"code": [str(i) for i in range(20_000)] * 2})
    with pytest.raises(BadPartitionKey, match="ceiling"):
        check_partition_keys(table, ["code"])


def test_small_tables_are_not_judged_on_ratio():
    """Three rows from one source say nothing about the column at scale."""
    check_partition_keys(observations(6), ["observation_id"])


def test_ordinary_partition_keys_are_accepted():
    check_partition_keys(observations(), ["year", "indicator"])


# ---- writing --------------------------------------------------------------


def test_write_produces_a_readable_parquet_file(resolver):
    result = ParquetWriter(resolver).write(Layer.SILVER, "observations", observations())
    assert result.files == 1
    assert result.rows == 6
    assert pq.read_table(result.paths[0]).num_rows == 6


def test_write_partitions_hive_style(resolver):
    result = ParquetWriter(resolver).write(
        Layer.SILVER, "observations", observations(), partition_by=["year"]
    )
    assert result.files == 2
    assert result.rows == 6
    assert {p.split("/")[-2] for p in result.paths} == {"year=2026", "year=2027"}


def test_write_rejects_an_identifier_partition(resolver):
    table = pa.table({"observation_id": [f"obs-{i}" for i in range(200)]})
    with pytest.raises(BadPartitionKey):
        ParquetWriter(resolver).write(
            Layer.SILVER, "observations", table, partition_by=["observation_id"]
        )


def test_empty_table_still_writes_a_file(resolver):
    """A missing partition reads as a gap; an empty one reads as no news."""
    result = ParquetWriter(resolver).write(Layer.BRONZE, "documents", empty(BRONZE_DOCUMENTS))
    assert result.files == 1
    assert result.rows == 0
    assert pq.read_table(result.paths[0]).schema.names == BRONZE_DOCUMENTS.names


def test_large_tables_split_at_the_size_target(resolver):
    """One giant file is as bad for pruning as ten thousand tiny ones."""
    writer = ParquetWriter(resolver, target_bytes=2048)
    table = pa.table({"indicator": ["x" * 256] * 200, "year": [2026] * 200})
    result = writer.write(Layer.SILVER, "big", table)

    assert result.files > 1
    assert result.rows == 200
    assert sum(pq.read_table(p).num_rows for p in result.paths) == 200


def test_small_tables_are_not_split(resolver):
    writer = ParquetWriter(resolver, target_bytes=256 * 1024 * 1024)
    assert writer.write(Layer.SILVER, "small", observations()).files == 1


def test_concurrent_runs_do_not_overwrite_each_other(resolver):
    """Two runs writing one partition must not both produce part-00000."""
    writer = ParquetWriter(resolver)
    first = writer.write(Layer.SILVER, "obs", observations(2), run_id="run-a")
    second = writer.write(Layer.SILVER, "obs", observations(2), run_id="run-b")

    assert first.paths != second.paths
    assert all(Path(p).exists() for p in first.paths + second.paths)


def test_partition_values_are_slugified(resolver):
    """Partition values come from data and can carry anything."""
    table = pa.table({"category": ["Ekspor Nikel — Q1"], "value": [1.0]})
    result = ParquetWriter(resolver).write(Layer.GOLD, "trade", table, partition_by=["category"])
    assert "category=ekspor-nikel-q1" in result.paths[0]


def test_null_partition_values_get_their_own_directory(resolver):
    table = pa.table({"year": [2026, None], "value": [1.0, 2.0]})
    result = ParquetWriter(resolver).write(Layer.SILVER, "sparse", table, partition_by=["year"])
    assert any("year=__null__" in p for p in result.paths)


# ---- schema conformance ---------------------------------------------------


def test_conform_fills_missing_nullable_columns():
    table = pa.table(
        {
            "document_id": ["doc_1"],
            "source_id": ["bps"],
            "content_hash": ["abc"],
            "raw_path": ["raw/x"],
            "processed_at": pa.array([datetime.now(UTC)], type=pa.timestamp("us", tz="UTC")),
        }
    )
    conformed = conform(table, BRONZE_DOCUMENTS)
    assert conformed.schema.names == BRONZE_DOCUMENTS.names
    assert conformed.column("title").null_count == 1


def test_conform_refuses_to_null_a_required_column():
    """Silently nulling provenance is how a row loses its way back."""
    table = pa.table({"document_id": ["doc_1"]})
    with pytest.raises(ValueError, match="required by the schema"):
        conform(table, BRONZE_DOCUMENTS)


# ---- compaction (program.md §47) ------------------------------------------


def _write_many_small(resolver, count: int) -> Path:
    writer = ParquetWriter(resolver)
    for i in range(count):
        writer.write(Layer.SILVER, "daily", observations(2, year=2026), run_id=f"run-{i}")
    return Path(resolver.resolve(Layer.SILVER, "daily"))


def test_compaction_merges_small_files(resolver):
    directory = _write_many_small(resolver, 5)
    before = len(list(directory.glob("*.parquet")))
    assert before == 5

    result = compact_partition(directory)
    assert result is not None
    assert result.files_before == 5
    assert result.files_after == 1
    assert result.rows == 10
    assert len(list(directory.glob("*.parquet"))) == 1


def test_compaction_preserves_every_row(resolver):
    directory = _write_many_small(resolver, 4)
    rows_before = pq.read_table(sorted(directory.glob("*.parquet"))).num_rows
    compact_partition(directory)
    assert pq.read_table(sorted(directory.glob("*.parquet"))).num_rows == rows_before


def test_compaction_skips_a_partition_not_worth_touching(resolver):
    directory = _write_many_small(resolver, 1)
    assert compact_partition(directory) is None


def test_compaction_leaves_large_files_alone(resolver):
    directory = _write_many_small(resolver, 3)
    assert compact_partition(directory, threshold=1) is None


def test_dry_run_reports_without_rewriting(resolver):
    directory = _write_many_small(resolver, 3)
    result = compact_partition(directory, dry_run=True)
    assert result is not None
    assert result.files_before == 3
    assert len(list(directory.glob("*.parquet"))) == 3


def test_compact_layer_walks_every_partition(resolver):
    writer = ParquetWriter(resolver)
    for i in range(3):
        writer.write(Layer.SILVER, "obs", observations(4), partition_by=["year"], run_id=f"r{i}")
    results = compact_layer(resolver, Layer.SILVER, "obs")
    assert len(results) == 2  # one per year partition
    assert all(r.files_after == 1 for r in results)


def test_compact_layer_on_an_absent_layer_is_harmless(resolver):
    assert compact_layer(resolver, Layer.GOLD, "nothing-here") == []


def test_find_partitions_locates_leaf_directories(resolver):
    ParquetWriter(resolver).write(Layer.SILVER, "obs", observations(), partition_by=["year"])
    partitions = find_partitions(Path(resolver.resolve(Layer.SILVER, "obs")))
    assert len(partitions) == 2
    assert all(p.name.startswith("year=") for p in partitions)


# ---- reading through DuckDB -----------------------------------------------


def test_warehouse_reads_what_the_writer_wrote(resolver):
    ParquetWriter(resolver).write(
        Layer.SILVER, "observations", observations(), partition_by=["year"]
    )
    with Warehouse(resolver) as wh:
        rows = wh.query(f"SELECT count(*) FROM {wh.source(Layer.SILVER, 'observations')}")
        assert rows.fetchone()[0] == 6


def test_hive_partitions_are_queryable_as_columns(resolver):
    """Partitioning only pays off if the key is usable in a WHERE clause."""
    ParquetWriter(resolver).write(
        Layer.SILVER, "observations", observations(), partition_by=["year"]
    )
    with Warehouse(resolver) as wh:
        wh.view("obs", Layer.SILVER, "observations")
        assert wh.query("SELECT count(*) FROM obs WHERE year = 2026").fetchone()[0] == 3


def test_warehouse_spills_to_local_disk_not_the_lake(resolver):
    """A NAS-backed temp directory turns a large join into a crawl."""
    with Warehouse(resolver) as wh:
        temp = wh.query("SELECT current_setting('temp_directory')").fetchone()[0]
        assert str(resolver.config.scratch_dir) in temp
        assert resolver.config.root not in temp
