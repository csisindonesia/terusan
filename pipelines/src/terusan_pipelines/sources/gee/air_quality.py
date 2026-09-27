"""Air pollution per Indonesian province and month, computed in Earth Engine.

Nobody publishes this table. KLHK's ISPU is a few dozen ground stations, most
of them in Java's cities, and a province without one has no figure at all. What
covers the whole archipelago every day is Sentinel-5P overhead and the CAMS
model analysis, and Earth Engine holds both — so this source asks Earth Engine
for a province's monthly mean and lands the answer.

That makes it unlike every other source here: the bytes in RAW are not a file
someone published but the result of a computation this module defines. What
keeps that honest is that the computation is fixed and written down — the
collection, the band, the month, the province outline, the scale — and all of
it lands in each artifact's metadata, so a figure can be recomputed from the
same archive and checked.

What is measured:

    no2     Sentinel-5P TROPOMI, tropospheric NO2 column      µmol/m²
    so2     Sentinel-5P TROPOMI, SO2 column                   µmol/m²
    co      Sentinel-5P TROPOMI, CO column                    mmol/m²
    o3      Sentinel-5P TROPOMI, O3 column                    DU
    hcho    Sentinel-5P TROPOMI, tropospheric HCHO column     µmol/m²
    aer_ai  Sentinel-5P TROPOMI, UV absorbing aerosol index   index
    ch4     Sentinel-5P TROPOMI, CH4 mixing ratio (bias-corr.) ppb
    pm25    CAMS near-real-time, surface PM2.5                µg/m³
    pm10    CAMS near-real-time, surface PM10                 µg/m³

Two things a reader has to be told, because the figures do not say them:

**TROPOMI measures a column, not the air at the surface.** It is the amount of
gas between the satellite and the ground, which ranks provinces and tracks a
dry-season burn well and is not comparable with an ISPU reading or a WHO
guideline. The L3 products are already QA-filtered and cloud gaps are not
filled, so `pixels` records what each month's mean rests on — a wet-season
month over Papua can rest on very little.

**CAMS is a model at ~44 km.** It is what gives surface PM2.5 everywhere, and
it is coarse: DKI Jakarta and DI Yogyakarta are a handful of cells. PM10 is in
the archive from July 2021 only.

Every mean is a spatial one — each pixel's monthly mean, averaged over the
province — not population-weighted. The provinces are the 38 outlines in
reference/geography/indonesia-provinces.geojson (scripts/province-shapes.py).

One artifact per month. A run with neither `--since` nor `start`/`end` takes
the last three full months, because the newest month keeps gaining images for a
few days after it ends; a recomputed month that came out differently lands as a
new artifact, and Silver keeps the later retrieval.

    --param start=2018-07 --param end=2026-08   a backfill
    --since 2026-01-01                          from a month to the last full one
    --param only=ch4                            one pollutant, for a backfill
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
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

log = structlog.get_logger(__name__)

DATASET = "air-quality"
PROVINCES = GEOGRAPHY_DIR / "indonesia-provinces.geojson"
CATALOG = "https://developers.google.com/earth-engine/datasets/catalog/"

#: Close to TROPOMI's L3 grid, which resamples ~5.5 × 3.5 km pixels. Finer buys
#: nothing and costs quota; CAMS is sampled onto the same grid.
SCALE_M = 5000

#: What a run takes when not told: the newest month is still filling in.
DEFAULT_MONTHS = 3

COLUMNS = [
    "indicator", "series_name", "pollutant", "period", "geo_id", "province",
    "value", "unit", "pixels", "collection", "band", "publisher",
]  # fmt: skip


@dataclass(frozen=True, slots=True)
class Pollutant:
    key: str
    name: str
    collection: str
    band: str
    unit: str
    #: The first month the collection covers in full.
    first: str
    #: From the collection's unit to `unit`.
    factor: float
    publisher: str
    cams: bool = False

    @property
    def indicator(self) -> str:
        return f"gee_air_{self.key}"


_S5P = "Copernicus Sentinel-5P (ESA)"
_CAMS = "Copernicus Atmosphere Monitoring Service (ECMWF)"
#: One Dobson unit is 4.4615e-4 mol/m².
_DU = 1 / 4.4615e-4

POLLUTANTS: tuple[Pollutant, ...] = (
    Pollutant("no2", "Tropospheric NO2 column (Sentinel-5P)", "COPERNICUS/S5P/OFFL/L3_NO2",
              "tropospheric_NO2_column_number_density", "µmol/m²", "2018-07", 1e6, _S5P),
    Pollutant("so2", "SO2 column (Sentinel-5P)", "COPERNICUS/S5P/OFFL/L3_SO2",
              "SO2_column_number_density", "µmol/m²", "2019-01", 1e6, _S5P),
    Pollutant("co", "CO column (Sentinel-5P)", "COPERNICUS/S5P/OFFL/L3_CO",
              "CO_column_number_density", "mmol/m²", "2018-07", 1e3, _S5P),
    Pollutant("o3", "Total ozone column (Sentinel-5P)", "COPERNICUS/S5P/OFFL/L3_O3",
              "O3_column_number_density", "DU", "2018-10", _DU, _S5P),
    Pollutant("hcho", "Tropospheric formaldehyde column (Sentinel-5P)",
              "COPERNICUS/S5P/OFFL/L3_HCHO", "tropospheric_HCHO_column_number_density",
              "µmol/m²", "2019-01", 1e6, _S5P),
    Pollutant("aer_ai", "UV absorbing aerosol index (Sentinel-5P)", "COPERNICUS/S5P/OFFL/L3_AER_AI",
              "absorbing_aerosol_index", "index", "2018-08", 1.0, _S5P),
    Pollutant("ch4", "CH4 column-averaged mixing ratio (Sentinel-5P)", "COPERNICUS/S5P/OFFL/L3_CH4",
              "CH4_column_volume_mixing_ratio_dry_air_bias_corrected", "ppb", "2019-01", 1.0, _S5P),
    Pollutant("pm25", "Surface PM2.5 (CAMS)", "ECMWF/CAMS/NRT",
              "particulate_matter_d_less_than_25_um_surface", "µg/m³", "2016-07", 1e9, _CAMS, True),
    Pollutant("pm10", "Surface PM10 (CAMS)", "ECMWF/CAMS/NRT",
              "particulate_matter_d_less_than_10_um_surface", "µg/m³", "2021-07", 1e9, _CAMS, True),
)  # fmt: skip


def months(start: str, end: str) -> list[str]:
    """Every `YYYY-MM` from `start` to `end`, inclusive."""
    y, m = (int(part) for part in start.split("-")[:2])
    end_y, end_m = (int(part) for part in end.split("-")[:2])
    out = []
    while (y, m) <= (end_y, end_m):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_full_month(today: date) -> str:
    previous = today.replace(day=1) - timedelta(days=1)
    return f"{previous.year:04d}-{previous.month:02d}"


def window(ctx: ScrapeContext, today: date) -> list[str]:
    """The months a run computes."""
    end = str(ctx.params.get("end") or last_full_month(today))
    if start := ctx.params.get("start"):
        return months(str(start), end)
    if ctx.since:
        return months(f"{ctx.since.year:04d}-{ctx.since.month:02d}", end)
    y, m = (int(part) for part in end.split("-")[:2])
    back = y * 12 + (m - 1) - (DEFAULT_MONTHS - 1)
    return months(f"{back // 12:04d}-{back % 12 + 1:02d}", end)


def rows(period: str, features: list[dict[str, Any]], wanted: list[Pollutant]) -> list[dict]:
    """One row per province and pollutant, from a `reduceRegions` answer."""
    # A single-band image names the outputs `mean` and `count`; only several
    # bands get the band's name in front. Before July 2018 only PM2.5 exists.
    single = len(wanted) == 1
    out = []
    for feature in features:
        props = feature["properties"]
        for p in wanted:
            prefix = "" if single else f"{p.key}_"
            # A province with no clear pixel all month comes back without the
            # key at all. It is written as an empty value rather than dropped:
            # "looked and saw nothing" is a fact the pixel count carries.
            mean = props.get(f"{prefix}mean")
            out.append({
                "indicator": p.indicator,
                "series_name": p.name,
                "pollutant": p.key,
                "period": period,
                "geo_id": props["geo_id"],
                "province": props["name"],
                "value": "" if mean is None else f"{mean:.4f}",
                "unit": p.unit,
                "pixels": int(props.get(f"{prefix}count") or 0),
                "collection": p.collection,
                "band": p.band,
                "publisher": p.publisher,
            })  # fmt: skip
    return sorted(out, key=lambda r: (r["geo_id"], r["pollutant"]))


def to_csv(records: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue().encode()


class AirQuality(Source):
    """Monthly pollutant means per province, from Sentinel-5P and CAMS."""

    meta = SourceMeta(
        slug="gee-air-quality",
        name="Air quality from Sentinel-5P and CAMS (via Google Earth Engine)",
        organization="Copernicus (ESA / ECMWF)",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.API,
        base_url=f"{CATALOG}COPERNICUS_S5P_OFFL_L3_NO2",
        license="Copernicus data policy — free and open, attribution required",
        update_frequency=UpdateFrequency.MONTHLY,
        # Each month is one Earth Engine request per pollutant set; the ceiling
        # is Earth Engine's quota, not politeness.
        max_requests_per_second=0.5,
        # The 6th: late enough that the month's last orbits are in.
        schedule="0 6 6 * *",
        notes=(
            "Computed, not downloaded: a monthly mean per province over "
            "Sentinel-5P L3 columns and CAMS surface PM. Needs EE_PROJECT."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        ee = self._earth_engine()
        regions = self._provinces(ee)
        wanted_months = window(ctx, date.today())
        # `--param only=ch4` computes a subset, for backfilling a pollutant
        # added after the rest were collected.
        only = set(str(ctx.params.get("only") or ",".join(p.key for p in POLLUTANTS)).split(","))
        log.info("gee.collect", months=len(wanted_months), first=wanted_months[0])

        for number, period in enumerate(wanted_months):
            if ctx.limit is not None and number >= ctx.limit:
                return
            wanted = [p for p in POLLUTANTS if p.first <= period and p.key in only]
            if not wanted:
                continue
            try:
                features = self._reduce(ee, period, wanted, regions)
            except ee.EEException as error:
                # One month refused — quota, a collection gap — should not cost
                # the months either side of it; a re-run picks it up.
                log.warning("gee.month_failed", period=period, error=str(error))
                continue

            records = rows(period, features, wanted)
            filled = sum(1 for r in records if r["value"])
            log.info("gee.month", period=period, values=filled, rows=len(records))
            yield Artifact(
                content=to_csv(records),
                filename=f"{period}.csv",
                dataset=DATASET,
                source_url=f"{CATALOG}{wanted[0].collection.replace('/', '_')}",
                media_type="text/csv",
                partition=(f"year={period[:4]}", f"month={period[5:7]}"),
                metadata={
                    "period": period,
                    "scale_m": SCALE_M,
                    "reducer": "per-pixel monthly mean, then province mean",
                    "regions": "reference/geography/indonesia-provinces.geojson",
                    "collections": {p.key: f"{p.collection}:{p.band}" for p in wanted},
                    "cams_filter": "model_initialization_hour == 0, model_forecast_hour < 24",
                },
            )

    @staticmethod
    def _earth_engine() -> Any:
        try:
            import ee
        except ImportError as error:
            raise RuntimeError(
                "gee-air-quality needs the `gee` extra: uv sync --extra gee"
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

    @staticmethod
    def _provinces(ee: Any) -> Any:
        collection = json.loads(PROVINCES.read_text())
        return ee.FeatureCollection([
            ee.Feature(ee.Geometry(f["geometry"], geodesic=False), f["properties"])
            for f in collection["features"]
        ])  # fmt: skip

    @staticmethod
    def _reduce(ee: Any, period: str, wanted: list[Pollutant], regions: Any) -> list[dict]:
        start = ee.Date(f"{period}-01")
        end = start.advance(1, "month")
        bands = []
        for p in wanted:
            images = ee.ImageCollection(p.collection).filterDate(start, end).select(p.band)
            if p.cams:
                # Two runs a day, each forecasting five days ahead. The 00 UTC
                # run's first day only, so each hour is counted once and always
                # from the freshest analysis.
                images = images.filter(ee.Filter.eq("model_initialization_hour", 0)).filter(
                    ee.Filter.lt("model_forecast_hour", 24)
                )
            bands.append(images.mean().multiply(p.factor).rename(p.key))

        reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)
        stats = ee.Image.cat(bands).reduceRegions(
            collection=regions, reducer=reducer, scale=SCALE_M, tileScale=4
        )
        return stats.getInfo()["features"]


__all__ = ["POLLUTANTS", "AirQuality", "months", "rows", "window"]
