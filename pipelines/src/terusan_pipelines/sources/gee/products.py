"""Earth Engine products as sources, one per entry in `catalog.PRODUCTS`.

Each product is its own source — `gee-chirps-rainfall`, `gee-modis-burned-area`
— so each has its own run history, schedule and failures, and a backfill of
forty years of rainfall does not hold up a year of night-time lights.

What every one of them does is the same, and lives here once: for each period,
build the product's image, reduce it over the 38 province outlines, and land the
answer as one CSV. Like `gee-air-quality`, the bytes in RAW are the result of a
computation rather than a published file, so each artifact's metadata records
the collection, scale and reducer it was computed with.

Periods are computed a few at a time. Earth Engine answers each request in ten
to thirty seconds and allows several at once, and a monthly product has up to
five hundred months: one at a time is a day's backfill. A request refused for
load is retried with backoff; one refused for any other reason costs only its
own period, and a re-run picks it up.

    --param start=1981-01 --param end=2026-08   a backfill of a monthly product
    --param start=2010                          an annual product from 2010
    --param workers=4                           fewer requests at once
"""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import structlog

from ...normalize.reference import GEOGRAPHY_DIR
from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..credentials import credentials
from .air_quality import last_full_month, months
from .catalog import PRODUCTS, Product, product_url

log = structlog.get_logger(__name__)

PROVINCES = GEOGRAPHY_DIR / "indonesia-provinces.geojson"

#: What a monthly product takes when not told: the newest months keep gaining
#: images for a while after they end.
DEFAULT_MONTHS = 3

#: Requests in flight per product.
WORKERS = 6

#: Earth Engine's answers to load, which a wait fixes. Anything else — a band
#: that does not exist, a computation too large — would fail again the same way.
_TRANSIENT = ("too many concurrent", "too many requests", "429", "rate limit",
              "internal error", "service unavailable", "503", "deadline")  # fmt: skip
ATTEMPTS = 5

COLUMNS = [
    "indicator", "series_name", "code", "period", "geo_id", "province",
    "value", "unit", "collection", "publisher",
]  # fmt: skip


def periods(product: Product, ctx: ScrapeContext, today: date) -> list[str]:
    """The periods a run computes for a product."""
    start = ctx.params.get("start")
    end = ctx.params.get("end")

    if product.cadence == "annual":
        chosen = list(product.periods)
        if start:
            chosen = [p for p in chosen if p >= str(start)[:4]]
        elif ctx.since:
            chosen = [p for p in chosen if p >= str(ctx.since.year)]
        if end:
            chosen = [p for p in chosen if p <= str(end)[:4]]
        return chosen

    last = product.last or last_full_month(today)
    if end:
        last = min(last, str(end))
    if start:
        first = max(str(start), product.first or str(start))
    elif ctx.since:
        first = max(f"{ctx.since.year:04d}-{ctx.since.month:02d}", product.first or "")
    else:
        recent = months("1900-01", last)[-DEFAULT_MONTHS:]
        first = max(recent[0], product.first or recent[0])
    return months(first, last) if first <= last else []


def formatted(value: float | None) -> str:
    """A figure as text, never in scientific notation."""
    if value is None:
        return ""
    return f"{value:.1f}" if abs(value) >= 1000 else f"{value:.4f}"


def rows(product: Product, period: str, answers: dict[str, dict[str, Any]]) -> list[dict]:
    """One row per province and band, from the merged `reduceRegions` answers."""
    out = []
    for geo_id, props in sorted(answers.items()):
        for band in product.bands_for(period):
            out.append({
                "indicator": product.indicator(band),
                "series_name": band.name,
                "code": band.key,
                "period": period,
                "geo_id": geo_id,
                "province": props["name"],
                # A province with no valid pixel comes back without the key.
                "value": formatted(props.get(band.key)),
                "unit": band.unit,
                "collection": product.collection,
                "publisher": product.publisher,
            })  # fmt: skip
    return out


def to_csv(records: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue().encode()


def earth_engine() -> Any:
    try:
        import ee
    except ImportError as error:
        raise RuntimeError(
            "Earth Engine sources need the `gee` extra: uv sync --extra gee"
        ) from error

    creds = credentials()
    if not creds.ee_project:
        raise RuntimeError(
            "EE_PROJECT is not set: Earth Engine meters every request against "
            "a Cloud project registered for it"
        )
    if creds.ee_service_account_key:
        key = json.loads(Path(creds.ee_service_account_key).read_text())
        ee.Initialize(
            ee.ServiceAccountCredentials(key["client_email"], creds.ee_service_account_key),
            project=creds.ee_project,
        )
    else:
        ee.Initialize(project=creds.ee_project)
    return ee


def provinces(ee: Any) -> list[Any]:
    collection = json.loads(PROVINCES.read_text())
    return [
        ee.Feature(ee.Geometry(f["geometry"], geodesic=False), f["properties"])
        for f in collection["features"]
    ]


def chunks(ee: Any, product: Product, features: list[Any]) -> list[Any]:
    """What one request reduces over: all 38 provinces, or one at a time."""
    if product.split:
        return [ee.FeatureCollection([feature]) for feature in features]
    return [ee.FeatureCollection(features)]


_REDUCERS = {"mean": "mean", "sum": "sum", "max": "max"}


def _band_name(key: str, period: str, together: bool) -> str:
    return f"{key}__{period.replace('-', '_')}" if together else key


def reduce(
    ee: Any, product: Product, group: list[str], regions: Any
) -> dict[str, dict[str, dict[str, Any]]]:
    """Each province's figures for a group of periods: period → geo_id → figures.

    A group is one period, or — for a product whose periods are bands of one
    image, as Hansen's years are — all of them, so the image is read once
    rather than once a year.

    One `reduceRegions` per statistic: a mean band and a sum band cannot share
    a reducer. Only the figures come back — the outlines are dropped from the
    answer, or every request would return the geometry it was sent.
    """
    together = len(group) > 1
    images, names = [], {}
    for period in group:
        keys = [band.key for band in product.bands_for(period)]
        renamed = [_band_name(key, period, together) for key in keys]
        images.append(product.build(ee, period).select(keys).rename(renamed))
        for band in product.bands_for(period):
            names[_band_name(band.key, period, together)] = (period, band)
    image = ee.Image.cat(images)

    merged: dict[str, dict[str, dict[str, Any]]] = {period: {} for period in group}
    for stat in _REDUCERS:
        keys = [name for name, (_, band) in names.items() if band.stat == stat]
        if not keys:
            continue
        reducer = getattr(ee.Reducer, stat)()
        # A single-output reducer names its outputs after the bands — except
        # over a single band, where it names it after itself.
        if len(keys) == 1:
            reducer = reducer.setOutputs(keys)
        stats = image.select(keys).reduceRegions(
            collection=regions, reducer=reducer, scale=product.scale,
            tileScale=product.tile_scale,
        ).select(["geo_id", "name", *keys], None, False)  # fmt: skip
        for feature in stats.getInfo()["features"]:
            props = feature["properties"]
            for key in keys:
                period, band = names[key]
                entry = merged[period].setdefault(props["geo_id"], {"name": props["name"]})
                if props.get(key) is not None:
                    entry[band.key] = props[key]
    return merged


def transient(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _TRANSIENT)


def reduce_with_retry(ee: Any, product: Product, group: list[str], regions: Any) -> dict:
    for attempt in range(ATTEMPTS):
        try:
            return reduce(ee, product, group, regions)
        except ee.EEException as error:
            if attempt == ATTEMPTS - 1 or not transient(error):
                raise
            wait = 5 * 2**attempt
            log.info("gee.retry", source=product.source_slug, period=group[0], wait=wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


class EarthEngineProduct(Source, abstract=True):
    """One catalogue product, reduced per province and period."""

    product: Product

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        product = self.product
        wanted = periods(product, ctx, date.today())
        if ctx.limit is not None:
            wanted = wanted[: ctx.limit]
        if not wanted:
            log.info("gee.nothing_to_do", source=product.source_slug)
            return

        ee = earth_engine()
        parts = chunks(ee, product, provinces(ee))
        groups = [wanted] if product.together else [[period] for period in wanted]
        workers = int(ctx.params.get("workers") or (WORKERS * 2 if product.split else WORKERS))
        log.info("gee.collect", source=product.source_slug, periods=len(wanted),
                 first=wanted[0], last=wanted[-1], requests=len(groups) * len(parts))  # fmt: skip

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = [
                (group, [pool.submit(reduce_with_retry, ee, product, group, part)
                         for part in parts])
                for group in groups
            ]  # fmt: skip
            # In order, so RAW fills chronologically and a stopped run leaves a
            # clean edge rather than scattered months.
            for group, futures in pending:
                answers: dict[str, dict[str, dict[str, Any]]] = {p: {} for p in group}
                try:
                    for future in futures:
                        for period, figures in future.result().items():
                            answers[period].update(figures)
                except ee.EEException as error:
                    # A period lands whole or not at all: a CSV missing three
                    # provinces would read as three provinces with no figure.
                    for future in futures:
                        future.cancel()
                    log.warning("gee.period_failed", source=product.source_slug,
                                period=group[0], error=str(error)[:300])  # fmt: skip
                    continue

                for period in group:
                    yield self._artifact(period, answers[period])

    def _artifact(self, period: str, answers: dict[str, dict[str, Any]]) -> Artifact:
        product = self.product
        records = rows(product, period, answers)
        filled = sum(1 for r in records if r["value"])
        log.info("gee.period", source=product.source_slug, period=period,
                 values=filled, rows=len(records))  # fmt: skip
        return Artifact(
            content=to_csv(records),
            filename=f"{period}.csv",
            dataset=product.slug,
            source_url=product_url(product),
            media_type="text/csv",
            partition=_partition(period),
            metadata={
                "period": period,
                "collection": product.collection,
                "scale_m": product.scale,
                "bands": {band.key: band.stat for band in product.bands_for(period)},
                "regions": "reference/geography/indonesia-provinces.geojson",
            },
        )


def _partition(period: str) -> tuple[str, ...]:
    if len(period) == 7:
        return (f"year={period[:4]}", f"month={period[5:7]}")
    return (f"year={period[:4]}",)


def _meta(product: Product) -> SourceMeta:
    monthly = product.cadence == "monthly"
    static = product.cadence == "annual" and len(product.periods) == 1
    return SourceMeta(
        slug=product.source_slug,
        name=f"{product.title.split(' by province')[0]} (via Google Earth Engine)",
        organization=product.organization,
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.API,
        base_url=product_url(product),
        license=product.license,
        update_frequency=(
            UpdateFrequency.MONTHLY
            if monthly
            else UpdateFrequency.IRREGULAR
            if static
            else UpdateFrequency.ANNUAL
        ),
        max_requests_per_second=0.5,
        # Monthly on the 7th, a day after air quality; annual on 7 February,
        # once most annual products have published the year before. A static
        # product is computed once, when asked.
        schedule=None if static else ("0 7 7 * *" if monthly else "0 7 7 2 *"),
        notes=(
            f"Computed, not downloaded: {product.collection} reduced per province "
            f"at {product.scale} m. Needs EE_PROJECT."
        ),
    )


def _source(product: Product) -> type[EarthEngineProduct]:
    name = "".join(part.capitalize() for part in product.slug.split("-"))
    return type(
        f"EarthEngine{name}",
        (EarthEngineProduct,),
        {"product": product, "meta": _meta(product), "__module__": __name__,
         "__doc__": product.title},
    )  # fmt: skip


#: Module-level, so discovery finds them as it finds any other source.
SOURCES: dict[str, type[EarthEngineProduct]] = {
    product.source_slug: _source(product) for product in PRODUCTS
}
globals().update({cls.__name__: cls for cls in SOURCES.values()})

__all__ = ["SOURCES", "EarthEngineProduct", "periods", "reduce", "rows", "to_csv"]
