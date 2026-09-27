"""The bucketed observations table (warehouse/observations.py)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from terusan_pipelines.normalize.schema import SILVER_OBSERVATIONS
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import table_from_rows
from terusan_pipelines.warehouse.observations import (
    BUCKETS,
    LAYOUT_FILE,
    ObservationStore,
    bucket_name,
    bucket_of,
    fnv1a32,
)

# The same vectors are asserted by the API (internal/query/warehouse_test.go):
# if the two ever disagree, the API reads the wrong bucket and a series goes
# missing, so neither side may change its hash alone.
SHARED_VECTORS = {"0kp69xys": 3, "39q7qs8c": 52, "a": 44, "": 197, "bps-inflasi": 69}


@pytest.fixture
def resolver(tmp_path: Path) -> StorageResolver:
    resolver = StorageResolver(
        StorageConfig(
            STORAGE_PROFILE="local",
            STORAGE_BACKEND="local",
            STORAGE_ROOT=str(tmp_path / "lake"),
            SCRATCH_DIR=str(tmp_path / "cache"),
        )
    )
    # These tests rewrite and delete the observations table: never anywhere
    # but a temporary directory.
    assert Path(resolver.resolve(Layer.SILVER, "observations")).is_relative_to(tmp_path)
    resolver.ensure_layout()
    return resolver


def _series(indicator: str, periods: list[str], value: int = 1) -> pa.Table:
    return table_from_rows(
        [
            {
                "observation_id": f"{indicator}-{p}",
                "indicator_id": indicator,
                "period": p,
                "period_start": date(int(p), 1, 1),
                "period_end": date(int(p), 12, 31),
                "temporal_resolution": "annual",
                "value": value,
                "status": "ok",
                "value_unambiguous": True,
                "source_id": "test",
                "processed_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
            for p in periods
        ],
        SILVER_OBSERVATIONS,
    )


def _rows(resolver: StorageResolver) -> list[tuple]:
    pattern = resolver.glob(Layer.SILVER, "observations")
    return duckdb.sql(
        f"SELECT indicator_id, period, CAST(value AS INTEGER) FROM read_parquet('{pattern}', "
        "union_by_name=true, hive_partitioning=false) ORDER BY 1, 2"
    ).fetchall()


def test_the_hash_is_the_one_the_api_uses():
    assert fnv1a32("a") == 0xE40C292C  # the published FNV-1a test vector
    assert {v: bucket_of(v) for v in SHARED_VECTORS} == SHARED_VECTORS
    assert all(0 <= bucket_of(str(n)) < BUCKETS for n in range(1000))


def test_replacing_a_series_keeps_its_bucket_neighbours(resolver):
    store = ObservationStore(resolver)
    # Two series that share a bucket, so replacing one must carry the other.
    first = "a"
    neighbour = next(f"n{n}" for n in range(10_000) if bucket_of(f"n{n}") == bucket_of(first))
    store.replace(first, _series(first, ["2020", "2021"]))
    store.replace(neighbour, _series(neighbour, ["2020"]))
    store.replace(first, _series(first, ["2022"], value=7))

    assert _rows(resolver) == [(first, "2022", 7), (neighbour, "2020", 1)]
    root = Path(resolver.resolve(Layer.SILVER, "observations"))
    assert [p.name for p in root.glob("bucket=*")] == [bucket_name(bucket_of(first))]
    assert json.loads((root / LAYOUT_FILE).read_text())["buckets"] == BUCKETS
    # Nothing half-written is left for a reader to find.
    assert not list(root.rglob("*.tmp"))


def test_a_bucket_is_sorted_by_series_and_period(resolver):
    store = ObservationStore(resolver)
    ids = [f"s{n}" for n in range(2000) if bucket_of(f"s{n}") == 0][:3]
    for indicator in reversed(ids):
        store.replace(indicator, _series(indicator, ["2021", "2020"]))
    table = pq.read_table(
        Path(resolver.resolve(Layer.SILVER, "observations")) / bucket_name(0) / "part.parquet"
    )
    keys = list(
        zip(
            table.column("indicator_id").to_pylist(),
            table.column("period").to_pylist(),
            strict=True,
        )
    )
    assert keys == sorted(keys)


def test_the_old_layout_moves_into_buckets_and_reads_the_same(resolver):
    root = Path(resolver.resolve(Layer.SILVER, "observations"))
    # A lake written one directory per series, as before.
    for indicator in ["x1", "x2", "x3"]:
        directory = root / f"indicator_id={indicator}" / "temporal_resolution=annual"
        directory.mkdir(parents=True)
        pq.write_table(_series(indicator, ["2020", "2021"]), directory / "part-0.parquet")
    before = _rows(resolver)
    # A scheduled run before migrating keeps to the old layout: it must not
    # turn the lake into a mixture on its own.
    ObservationStore(resolver).replace("x2", _series("x2", ["2024"], value=9))
    assert not list(root.glob("bucket=*"))
    mixed = _rows(resolver)
    assert ("x2", "2024", 9) in mixed and ("x2", "2020", 1) not in mixed

    moved = ObservationStore(resolver).migrate_legacy()

    assert moved["series"] == 3
    assert not list(root.glob("indicator_id=*"))
    assert _rows(resolver) == mixed
    assert len(before) == 6
    # Running it again finds nothing to move.
    assert ObservationStore(resolver).migrate_legacy()["series"] == 0
