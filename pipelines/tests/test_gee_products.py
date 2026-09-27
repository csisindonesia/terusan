"""The Earth Engine catalogue: what each product computes, and what lands.

Earth Engine is not called. What is checked is the catalogue's own
consistency — every product a source, a dataset and unique series — which
periods a run asks for, and how a reduction's answer becomes rows.
"""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import date

from terusan_pipelines import datasets
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.gee.catalog import PRODUCTS, Band
from terusan_pipelines.sources.gee.products import SOURCES, formatted, periods, rows, to_csv
from terusan_pipelines.sources.registry import registry

TODAY = date(2026, 9, 27)
BY_SLUG = {p.slug: p for p in PRODUCTS}


def test_every_product_is_a_registered_source_and_a_dataset() -> None:
    registered = {source.meta.slug for source in registry.all()}
    for product in PRODUCTS:
        assert product.source_slug in registered
        assert product.source_slug in SOURCES
        meta = datasets.get(product.slug)
        assert meta is not None and meta.source == product.source_slug
        assert meta.title == product.title


def test_slugs_indicators_and_band_keys_are_unique() -> None:
    assert len(BY_SLUG) == len(PRODUCTS)
    indicators = [p.indicator(b) for p in PRODUCTS for b in p.bands]
    assert len(indicators) == len(set(indicators))
    for product in PRODUCTS:
        keys = [band.key for band in product.bands]
        assert len(keys) == len(set(keys)), product.slug
        assert all(band.stat in {"mean", "sum", "max"} for band in product.bands)


def test_cadence_is_declared_consistently() -> None:
    for product in PRODUCTS:
        if product.cadence == "monthly":
            assert product.first and not product.periods, product.slug
        else:
            assert product.cadence == "annual" and product.periods, product.slug
            assert list(product.periods) == sorted(product.periods), product.slug


def test_a_monthly_product_defaults_to_the_last_three_full_months() -> None:
    assert periods(BY_SLUG["chirps-rainfall"], ScrapeContext(), TODAY) == [
        "2026-06", "2026-07", "2026-08",
    ]  # fmt: skip


def test_a_backfill_starts_no_earlier_than_the_product() -> None:
    ctx = ScrapeContext(params={"start": "1970-01", "end": "2015-06"})
    assert periods(BY_SLUG["smap-soil-moisture"], ctx, TODAY) == ["2015-04", "2015-05", "2015-06"]


def test_an_ended_product_stops_at_its_last_month() -> None:
    ended = replace(BY_SLUG["chirps-rainfall"], last="1981-02")
    assert periods(ended, ScrapeContext(params={"start": "1981-01"}), TODAY) == [
        "1981-01", "1981-02",
    ]  # fmt: skip


def test_an_annual_product_takes_every_year_unless_narrowed() -> None:
    product = BY_SLUG["gpw-population"]
    assert periods(product, ScrapeContext(), TODAY) == ["2000", "2005", "2010", "2015", "2020"]
    assert periods(product, ScrapeContext(params={"start": "2010"}), TODAY) == [
        "2010", "2015", "2020",
    ]  # fmt: skip


def test_a_band_limited_to_some_periods_appears_only_in_them() -> None:
    forest = BY_SLUG["global-forest-change"]
    assert [b.key for b in forest.bands_for("2000")] == ["tree_cover"]
    assert [b.key for b in forest.bands_for("2019")] == ["tree_cover_loss"]


def test_figures_are_never_written_in_scientific_notation() -> None:
    assert formatted(None) == ""
    assert formatted(0.00001) == "0.0000"
    assert formatted(-3.14159) == "-3.1416"
    assert formatted(276850069.25) == "276850069.2"


def test_rows_carry_every_band_and_leave_missing_figures_empty() -> None:
    product = BY_SLUG["modis-vegetation"]
    answers = {
        "ID-31": {"name": "DKI Jakarta", "ndvi": 0.31, "evi": 0.16},
        "ID-94": {"name": "Papua", "ndvi": 0.84},
    }
    out = {(r["geo_id"], r["code"]): r for r in rows(product, "2023-08", answers)}

    assert len(out) == 4
    assert out[("ID-31", "ndvi")]["value"] == "0.3100"
    assert out[("ID-31", "ndvi")]["indicator"] == "gee_modis_vegetation_ndvi"
    assert out[("ID-94", "evi")]["value"] == ""
    assert out[("ID-94", "evi")]["unit"] == "index"

    parsed = list(csv.DictReader(io.StringIO(to_csv(list(out.values())).decode())))
    assert {r["period"] for r in parsed} == {"2023-08"}


def test_band_coverage() -> None:
    band = Band("x", "X", "u", first="2021-07")
    assert not band.covers("2021-06")
    assert band.covers("2021-07")
