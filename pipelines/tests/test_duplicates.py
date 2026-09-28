"""Sweeping the same figures collected from more than one source."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from terusan_pipelines.normalize import SilverRunner
from terusan_pipelines.normalize.duplicates import Thresholds, carried_forward, find
from terusan_pipelines.normalize.schema import SILVER_OBSERVATIONS
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import table_from_rows
from terusan_pipelines.warehouse.observations import ObservationStore


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
    # These tests delete from Silver: never anywhere but a temporary directory.
    assert Path(resolver.resolve(Layer.SILVER, "observations")).is_relative_to(tmp_path)
    resolver.ensure_layout()
    return resolver


def figure(year: int) -> float:
    """A series that varies, so agreement between two copies means something."""
    return round(1000 + (year - 1990) ** 2 * 3.7, 2)


def rows(indicator: str, source: str, years, *, scale: float = 1.0, value=None) -> list[dict]:
    return [
        {
            "observation_id": f"{indicator}-{y}",
            "indicator_id": indicator,
            "period": str(y),
            "period_start": date(y, 1, 1),
            "period_end": date(y, 12, 31),
            "temporal_resolution": "annual",
            "value": Decimal(str(value if value is not None else round(figure(y) * scale, 6))),
            "status": "ok",
            "value_unambiguous": True,
            "geo_id": "IDN",
            "source_id": source,
            "processed_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        for y in years
    ]


def publish(resolver: StorageResolver, indicator: str, source: str, years, **kwargs) -> None:
    ObservationStore(resolver).replace(
        indicator, table_from_rows(rows(indicator, source, years, **kwargs), SILVER_OBSERVATIONS)
    )
    SilverRunner(resolver).write_indicators(
        [{"indicator_id": indicator, "name": f"{indicator} from {source}"}],
        source_id=source,
        merge=True,
    )


def deleted(found: list[dict]) -> dict[str, str]:
    return {row["indicator_id"]: row["superseded_by"] for row in found}


def test_a_shorter_copy_at_another_scale_gives_way(resolver: StorageResolver) -> None:
    publish(resolver, "long", "bps-indicators", range(2000, 2020))
    # The same figures, in thousands, over the last fifteen years only.
    publish(resolver, "copy", "adb-kidb", range(2005, 2020), scale=1e-3)

    found = find(resolver)
    assert deleted(found) == {"copy": "long"}
    assert found[0]["scale"] == 3
    assert found[0]["coverage"] == 1.0


def test_complements_both_stay(resolver: StorageResolver) -> None:
    publish(resolver, "early", "bps-indicators", range(1995, 2020))
    # Agrees over 2010-2019, then runs twenty years past the other.
    publish(resolver, "late", "fred-indonesia", range(2010, 2040))

    assert find(resolver) == []


def test_constant_series_do_not_match_each_other(resolver: StorageResolver) -> None:
    publish(resolver, "vat", "tradingeconomics-indonesia", range(2000, 2020), value=10)
    publish(resolver, "share", "wits-tradestats", range(2005, 2020), value=10)

    assert find(resolver) == []


def test_a_series_within_one_source_is_not_compared_with_itself(
    resolver: StorageResolver,
) -> None:
    publish(resolver, "table-a", "bps-indicators", range(2000, 2020))
    publish(resolver, "table-b", "bps-indicators", range(2005, 2020))

    assert find(resolver) == []


def test_disagreeing_figures_are_not_duplicates(resolver: StorageResolver) -> None:
    publish(resolver, "a", "bps-indicators", range(2000, 2020))
    publish(resolver, "b", "adb-kidb", range(2005, 2020), scale=1.07)

    assert find(resolver) == []


def test_ties_go_to_the_source_easiest_to_collect_again(resolver: StorageResolver) -> None:
    # Same length, same span: BPS needs a key, ADB does not.
    publish(resolver, "keyed", "bps-indicators", range(2000, 2020))
    publish(resolver, "open", "adb-kidb", range(2000, 2020))

    assert deleted(find(resolver)) == {"keyed": "open"}


def test_deletion_is_not_transitive(resolver: StorageResolver) -> None:
    store = ObservationStore(resolver)

    def perturbed(indicator, source, off):
        out = rows(indicator, source, range(2000, 2020))
        for row in out:
            if row["period_start"].year in off:
                row["value"] = row["value"] * Decimal("1.5") + off[row["period_start"].year]
        return table_from_rows(out, SILVER_OBSERVATIONS)

    publish(resolver, "kept", "bps-indicators", range(1990, 2020))
    # Agrees with `kept` on 16 of 20 years.
    store.replace("near", perturbed("near", "adb-kidb", {y: 1 for y in range(2016, 2020)}))
    # Agrees with `near` on 16 of 20 years, but with `kept` on only 12.
    far = {y: 1 for y in range(2016, 2020)} | {y: 2 for y in range(2000, 2004)}
    store.replace("far", perturbed("far", "fred-indonesia", far))

    found = deleted(find(resolver))
    assert found == {"near": "kept"}


def test_sweep_deletes_and_records(resolver: StorageResolver) -> None:
    publish(resolver, "long", "bps-indicators", range(2000, 2020))
    publish(resolver, "copy", "adb-kidb", range(2005, 2020), scale=1e-3)

    runner = SilverRunner(resolver)
    assert runner.sweep_duplicates(dry_run=True)
    assert ObservationStore(resolver).series("copy") is not None

    standing = runner.sweep_duplicates()
    assert [row["indicator_id"] for row in standing] == ["copy"]
    assert ObservationStore(resolver).series("copy") is None
    assert ObservationStore(resolver).series("long") is not None
    assert runner._published_indicators("adb-kidb") == []
    assert [r["indicator_id"] for r in runner._published_indicators("bps-indicators")] == ["long"]

    # A second sweep cannot see `copy` any more, and must keep holding it out.
    again = SilverRunner(resolver).sweep_duplicates()
    assert [row["indicator_id"] for row in again] == ["copy"]


def test_normalization_does_not_write_a_swept_series_back(resolver: StorageResolver) -> None:
    publish(resolver, "long", "bps-indicators", range(2000, 2020))
    publish(resolver, "copy", "adb-kidb", range(2005, 2020), scale=1e-3)
    SilverRunner(resolver).sweep_duplicates()

    runner = SilverRunner(resolver)
    same = rows("copy", "adb-kidb", range(2005, 2020), scale=1e-3)
    assert runner._held_out("copy", same) == "long"

    # The copy starts publishing years the kept series lacks: let it back in.
    longer = rows("copy", "adb-kidb", range(2005, 2030), scale=1e-3)
    assert runner._held_out("copy", longer) is None

    # And its name is not published while its figures are held out.
    runner.write_indicators([{"indicator_id": "copy", "name": "copy"}], source_id="adb-kidb")
    assert runner._published_indicators("adb-kidb") == []


def test_a_chain_of_deletions_points_at_the_series_still_standing() -> None:
    previous = [
        {
            "indicator_id": "old",
            "superseded_by": "middle",
            "superseded_by_source_id": "s2",
            "superseded_by_name": "middle",
        }
    ]
    found = [
        {
            "indicator_id": "middle",
            "superseded_by": "top",
            "superseded_by_source_id": "s3",
            "superseded_by_name": "top",
        }
    ]
    standing = {
        r["indicator_id"]: r["superseded_by"] for r in carried_forward(previous, found, {"top"})
    }
    assert standing == {"old": "top", "middle": "top"}


def test_thresholds_are_the_documented_ones() -> None:
    assert Thresholds() == Thresholds(
        min_matches=5, min_distinct=5, min_match_share=0.8, min_coverage=0.9
    )
