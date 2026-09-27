"""Migrating a lake off slug identifiers and onto derived codes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from terusan_pipelines.identifiers import dataset_code, indicator_code
from terusan_pipelines.normalize.observations import observation_id
from terusan_pipelines.normalize.recode import _is_slug, recode
from terusan_pipelines.normalize.runner import SilverRunner
from terusan_pipelines.normalize.schema import SILVER_OBSERVATIONS
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import ParquetWriter, Warehouse, table_from_rows
from terusan_pipelines.warehouse.observations import bucket_name, bucket_of


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


def _row(indicator: str, dataset: str, period: str) -> dict:
    """One observation as the old rule wrote it: identified by its slug."""
    return {
        "observation_id": observation_id(indicator, period, None, None),
        "indicator_id": indicator,
        "period": period,
        "period_start": date(int(period), 1, 1),
        "period_end": date(int(period), 12, 31),
        "temporal_resolution": "annual",
        "value": None,
        "unit": "IDR",
        "status": "ok",
        "value_unambiguous": True,
        "raw_value": "1",
        "geo_id": "ID",
        "geo_name_raw": "Indonesia",
        "commodity_id": None,
        "commodity_name_raw": None,
        "release_date": None,
        "revision": None,
        "source_id": "djpk-apbd",
        "source_url": None,
        "document_id": None,
        "dataset_id": dataset,
        "content_hash": None,
        "raw_path": None,
        "retrieved_at": None,
        "processed_at": datetime.now(UTC),
        "pipeline_version": "1",
    }


def _lake(resolver: StorageResolver, rows: list[dict]) -> None:
    ParquetWriter(resolver).write(
        Layer.SILVER,
        "observations",
        table_from_rows(rows, SILVER_OBSERVATIONS),
        partition_by=["indicator_id", "temporal_resolution"],
        run_id="fixture",
    )


def _observations(resolver: StorageResolver) -> list[tuple]:
    pattern = resolver.glob(Layer.SILVER, "observations")
    with Warehouse(resolver) as warehouse:
        return warehouse.query(
            "SELECT indicator_id, dataset_id, observation_id, period FROM "
            "read_parquet(?, union_by_name=true, hive_partitioning=true) "
            "ORDER BY period",
            [pattern],
        ).fetchall()


def test_a_slug_lake_moves_onto_codes(resolver: StorageResolver) -> None:
    _lake(resolver, [_row("apbd_revenue_realisasi", "apbd-national", "2024")])
    runner = SilverRunner(resolver)

    result = recode(runner)

    code = indicator_code("djpk-apbd", "apbd_revenue_realisasi")
    assert result.mapping == {"apbd_revenue_realisasi": code}

    indicator_id, dataset_id, obs_id, period = _observations(resolver)[0]
    assert indicator_id == code
    assert dataset_id == dataset_code("apbd-national")
    # The observation id is derived from the indicator, so it moves with it —
    # and lands where a re-normalization would put it, which is what keeps a
    # later run revising these figures instead of duplicating them.
    assert obs_id == observation_id(code, period, "ID", None)

    root = Path(resolver.resolve(Layer.SILVER, "observations"))
    assert [p.parent.name for p in root.glob("bucket=*/part.parquet")] == [
        bucket_name(bucket_of(code))
    ]
    assert not list(root.glob("indicator_id=*"))


def test_running_it_twice_changes_nothing(resolver: StorageResolver) -> None:
    """A migration that is not idempotent cannot be run on a schedule."""
    _lake(resolver, [_row("apbd_revenue_realisasi", "apbd-national", "2024")])
    runner = SilverRunner(resolver)

    recode(runner)
    first = _observations(resolver)
    second_result = recode(runner)

    assert second_result.mapping == {}
    assert _observations(resolver) == first


def test_a_second_pass_keeps_the_readable_key(resolver: StorageResolver) -> None:
    """The slug survives re-running, or the migration destroys what it renamed.

    On the second pass the identifier is already a code, and the key it was
    declared under exists only in the indicators table this pass rewrites.
    Reading it back before rewriting is the whole of the fix; without it the
    table ends up naming every series after its own code.
    """
    _lake(resolver, [_row("gold_price_close", "gold", "2024")])
    runner = SilverRunner(resolver)

    recode(runner)
    recode(runner)
    recode(runner)

    pattern = resolver.glob(Layer.SILVER, "indicators")
    with Warehouse(resolver) as warehouse:
        rows = warehouse.query(
            "SELECT indicator_id, slug, name FROM "
            "read_parquet(?, union_by_name=true, hive_partitioning=true)",
            [pattern],
        ).fetchall()

    assert rows == [
        (
            indicator_code("djpk-apbd", "gold_price_close"),
            "gold_price_close",
            "Gold price close",
        )
    ]


def test_the_indicators_table_is_rebuilt_with_slugs_and_tags(
    resolver: StorageResolver,
) -> None:
    """Most series have no indicators row at all; the migration gives them one."""
    _lake(resolver, [_row("apbd_revenue_realisasi", "apbd-national", "2024")])
    runner = SilverRunner(resolver)

    recode(runner)

    pattern = resolver.glob(Layer.SILVER, "indicators")
    with Warehouse(resolver) as warehouse:
        rows = warehouse.query(
            "SELECT indicator_id, slug, name, dataset_id, tags FROM "
            "read_parquet(?, union_by_name=true, hive_partitioning=true)",
            [pattern],
        ).fetchall()

    assert len(rows) == 1
    indicator_id, slug, name, dataset_id, tags = rows[0]
    assert indicator_id == indicator_code("djpk-apbd", "apbd_revenue_realisasi")
    assert slug == "apbd_revenue_realisasi"
    assert name == "Apbd revenue realisasi"
    assert dataset_id == dataset_code("apbd-national")
    assert len(tags) >= 5


def test_a_dataset_named_like_a_code_is_read_as_a_name() -> None:
    """A slug can look exactly like a derived code — `handbook` is eight
    lowercase letters — so the registry decides, not the string's shape.

    Tested at the rule rather than through a migration, because whether any
    declared dataset happens to be code-shaped changes as sources come and go,
    and the rule has to hold either way.
    """
    assert _is_slug("handbook", {"handbook"})
    # Nothing declares it: it is taken for the code it looks like.
    assert not _is_slug("handbook", set())
    # Not code-shaped, so a name whatever the registry says.
    assert _is_slug("apbd-national", set())


def test_the_catalogue_covers_every_dataset_the_figures_point_at(
    resolver: StorageResolver,
) -> None:
    _lake(resolver, [_row("apbd_revenue_realisasi", "apbd-national", "2024")])
    runner = SilverRunner(resolver)

    recode(runner)

    pattern = resolver.glob(Layer.SILVER, "datasets")
    with Warehouse(resolver) as warehouse:
        covered = warehouse.query(
            "SELECT count(*) FROM read_parquet(?, union_by_name=true) d WHERE d.dataset_id = ?",
            [pattern, dataset_code("apbd-national")],
        ).fetchone()
    assert covered[0] == 1
