"""Air quality from Earth Engine: which months a run asks for, and what lands.

Earth Engine itself is not called: what matters here is the window a run
computes, how a `reduceRegions` answer becomes rows, and that the province
outlines the computation averages over resolve to the geography registry.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date

from terusan_pipelines.normalize.reference import GEOGRAPHY_DIR
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.gee.air_quality import (
    POLLUTANTS,
    PROVINCES,
    months,
    rows,
    to_csv,
    window,
)

TODAY = date(2026, 9, 27)


def test_months_cross_a_year() -> None:
    assert months("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_default_window_is_the_last_three_full_months() -> None:
    assert window(ScrapeContext(), TODAY) == ["2026-06", "2026-07", "2026-08"]


def test_default_window_crosses_a_year() -> None:
    assert window(ScrapeContext(), date(2026, 2, 3)) == ["2025-11", "2025-12", "2026-01"]


def test_since_runs_to_the_last_full_month() -> None:
    assert window(ScrapeContext(since=date(2026, 7, 15)), TODAY) == ["2026-07", "2026-08"]


def test_start_and_end_params_are_a_backfill() -> None:
    ctx = ScrapeContext(params={"start": "2018-07", "end": "2018-09"})
    assert window(ctx, TODAY) == ["2018-07", "2018-08", "2018-09"]


def test_rows_read_the_band_prefixed_outputs() -> None:
    wanted = [p for p in POLLUTANTS if p.key in {"no2", "pm25"}]
    features = [
        {"properties": {"geo_id": "ID-31", "name": "DKI Jakarta",
                        "no2_mean": 123.456789, "no2_count": 40,
                        "pm25_mean": 31.2, "pm25_count": 3}},
        # No clear pixel all month: Earth Engine leaves the mean out.
        {"properties": {"geo_id": "ID-94", "name": "Papua", "no2_count": 0,
                        "pm25_mean": 9.8, "pm25_count": 900}},
    ]  # fmt: skip

    out = {(r["geo_id"], r["pollutant"]): r for r in rows("2026-08", features, wanted)}

    jakarta = out[("ID-31", "no2")]
    assert jakarta["value"] == "123.4568"
    assert jakarta["unit"] == "µmol/m²"
    assert jakarta["pixels"] == 40
    assert jakarta["indicator"] == "gee_air_no2"
    assert jakarta["period"] == "2026-08"
    assert out[("ID-94", "no2")]["value"] == ""
    assert out[("ID-94", "no2")]["pixels"] == 0
    assert out[("ID-94", "pm25")]["value"] == "9.8000"


def test_a_single_band_reads_the_bare_outputs() -> None:
    wanted = [p for p in POLLUTANTS if p.key == "pm25"]
    features = [{"properties": {"geo_id": "ID-31", "name": "DKI Jakarta",
                                "mean": 40.0, "count": 3}}]  # fmt: skip

    (row,) = rows("2017-01", features, wanted)
    assert row["value"] == "40.0000"
    assert row["pixels"] == 3


def test_csv_has_no_scientific_notation() -> None:
    wanted = [p for p in POLLUTANTS if p.key in {"so2", "co"}]
    features = [{"properties": {"geo_id": "ID-31", "name": "DKI Jakarta",
                                "so2_mean": 0.00001, "co_mean": -0.5}}]  # fmt: skip

    records = list(csv.DictReader(io.StringIO(to_csv(rows("2026-08", features, wanted)).decode())))
    assert [r["value"] for r in records] == ["-0.5000", "0.0000"]


def test_pollutant_months_are_ordered_and_indicators_unique() -> None:
    assert len({p.indicator for p in POLLUTANTS}) == len(POLLUTANTS)
    for p in POLLUTANTS:
        assert months(p.first, p.first) == [p.first]


def test_every_outline_is_a_registered_province() -> None:
    registered = {
        row["geo_id"]
        for row in csv.DictReader(
            line
            for line in (GEOGRAPHY_DIR / "indonesia-provinces.csv").open()
            if not line.startswith("#")
        )
        if row["geo_type"] == "province"
    }
    outlines = json.loads(PROVINCES.read_text())["features"]

    assert len(outlines) == 38
    assert {f["properties"]["geo_id"] for f in outlines} == registered
