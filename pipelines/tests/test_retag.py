"""Re-reading published series' topics without collecting them again."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from terusan_pipelines.normalize.runner import SilverRunner
from terusan_pipelines.normalize.schema import SILVER_INDICATORS
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import ParquetWriter, Warehouse, table_from_rows


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


def _publish(resolver: StorageResolver, rows: list[dict]) -> None:
    """Series as an earlier rule tagged them."""
    base = {column: None for column in SILVER_INDICATORS.names}
    base["processed_at"] = datetime(2026, 9, 1, tzinfo=UTC)
    ParquetWriter(resolver).write(
        Layer.SILVER,
        "indicators",
        table_from_rows([base | row for row in rows], SILVER_INDICATORS),
        partition_by=["source_id"],
        run_id="bps-indicators",
    )


def _tags(resolver: StorageResolver) -> dict[str, list[str]]:
    with Warehouse(resolver) as warehouse:
        rows = warehouse.query(
            "SELECT indicator_id, tags FROM "
            "read_parquet(?, union_by_name=true, hive_partitioning=true)",
            [resolver.glob(Layer.SILVER, "indicators")],
        ).fetchall()
    return {indicator: list(tags) for indicator, tags in rows}


HELD = ["bps-indicators", "statistics", "badan-pusat-statistik", "annual", "indicator"]


def test_retag_reads_the_titles_again(resolver: StorageResolver) -> None:
    _publish(
        resolver,
        [
            {
                "indicator_id": "grk00001",
                "source_id": "bps-indicators",
                "name": "Penyediaan dan Penggunaan Fisik untuk Emisi GRK Indonesia — Total",
                "unit": "Ribu Ton CO2",
                "tags": [*HELD[:4], "ribu-ton-co2", "indicator"],
            },
            {
                "indicator_id": "ntp00001",
                "source_id": "bps-indicators",
                "name": "NTPR (Nilai Tukar Petani Tanaman Perkebunan) — NTP",
                "tags": [*HELD[:4], "exchange-rate", "monetary", "indicator"],
            },
        ],
    )
    runner = SilverRunner(resolver)

    assert runner.retag_indicators(dry_run=True) == {"bps-indicators": 2}
    assert "emissions" not in _tags(resolver)["grk00001"]  # a dry run writes nothing

    assert runner.retag_indicators() == {"bps-indicators": 2}
    tags = _tags(resolver)
    assert {"emissions", "climate", "ribu-ton-co2"} <= set(tags["grk00001"])
    assert "exchange-rate" not in tags["ntp00001"]
    assert "farmers-terms-of-trade" in tags["ntp00001"]

    # Twice is once.
    assert runner.retag_indicators() == {"bps-indicators": 0}
