"""What each Earth Engine product is reduced to, per province and period.

A product is one Earth Engine dataset read one way: which bands, over which
periods, turned into which figures. Each becomes a source (`gee-<slug>`) and a
dataset (`<slug>`), and each of its bands becomes a series.

This module is data and the functions that build one period's image — nothing
here imports `ee`. The builders take the `ee` module as an argument, so the
dataset registry can read titles and descriptions off this file without
Google's client installed.

Three kinds of figure come out, and a band says which by its `stat`:

    mean   the province's mean of each pixel's value for the period —
           temperature, a vegetation index, a column density
    sum    a total over the province — rainfall is not this (it is a mean
           depth), but burned area, a population count and an area of forest
           lost are
    max    the highest pixel, for elevation

Areas are in km² and are computed as `pixelArea` over the pixels that meet a
condition, at the product's `scale`.

The scale is not a free choice. Read coarser than its native grid, an image is
read from Earth Engine's pyramid, and each band says how its pyramid was built:
a MODE pyramid (Hansen's `lossyear`, JRC's `waterClass`, WorldCover, MODIS land
cover, GHSL's built surface) keeps only the commonest class and loses the
minority ones — small lakes, scattered clearings, village roofs — and a MEAN
pyramid averages a fire mask's 8 with the land's 5 into something that is
neither. So a categorical band is read at or near its native scale, and `split` keeps
that affordable; a band whose MEAN pyramid is itself the answer (a 0/1 mangrove
mask averages to the mangrove fraction) can be read coarser.

"Near" is measured, not assumed. Hansen's loss and JRC's water, 30 m maps, are
read at 100 m: checked against a full 30 m count for 2019 and 2021, the totals
agree within 1% nationally and for Central Kalimantan, at a sixth of the cost.

Counts per pixel — population, buildings — are turned into densities first and
summed as density × area, so reading them at a coarser scale than native does
not undercount them.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CATALOG = "https://developers.google.com/earth-engine/datasets/catalog/"
COMMUNITY = "https://gee-community-catalog.org/"

#: Sabang to Merauke, for collections that are tiled and need filtering to the
#: archipelago before they are mosaicked.
INDONESIA_BBOX = (94.8, -11.2, 141.2, 6.2)

#: Earth Engine's authalic sphere, for the area of a pixel in degrees.
EARTH_RADIUS_M = 6371007.2


@dataclass(frozen=True, slots=True)
class Band:
    key: str
    name: str
    unit: str
    stat: str = "mean"
    #: Periods this band exists for, where it is narrower than the product's.
    first: str | None = None
    last: str | None = None

    def covers(self, period: str) -> bool:
        return (self.first is None or self.first <= period) and (
            self.last is None or period <= self.last
        )


@dataclass(frozen=True, slots=True)
class Product:
    slug: str
    title: str
    description: str
    tags: tuple[str, ...]
    organization: str
    publisher: str
    license: str
    #: The Earth Engine id the figures are read from, for provenance.
    collection: str
    bands: tuple[Band, ...]
    #: Builds one period's image, with one band per `Band.key`.
    build: Callable[[Any, str], Any]
    #: `monthly` periods are `YYYY-MM`; `annual` are `YYYY`.
    cadence: str
    #: Monthly: the first month (and the last, where the product has ended).
    first: str | None = None
    last: str | None = None
    #: Annual: every year the product holds.
    periods: tuple[str, ...] = ()
    scale: int = 5000
    tile_scale: int = 4
    #: Every period in one request: for a product whose periods are bands of
    #: a single image, which is then read once instead of once a period.
    together: bool = False
    #: Reduce each province in its own request. For products read at a fine
    #: scale, where one request over all 38 exceeds Earth Engine's five minutes.
    split: bool = False
    url: str = ""
    extra_tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_slug(self) -> str:
        return f"gee-{self.slug}"

    def indicator(self, band: Band) -> str:
        return f"gee_{self.slug.replace('-', '_')}_{band.key}"

    def bands_for(self, period: str) -> list[Band]:
        return [band for band in self.bands if band.covers(period)]


# -- helpers the builders share -----------------------------------------------


def _month(ee: Any, period: str) -> tuple[Any, Any]:
    start = ee.Date(f"{period}-01")
    return start, start.advance(1, "month")


def _year(ee: Any, period: str) -> tuple[Any, Any]:
    start = ee.Date(f"{period[:4]}-01-01")
    return start, start.advance(1, "year")


def _seconds(period: str) -> int:
    year, month = int(period[:4]), int(period[5:7])
    return calendar.monthrange(year, month)[1] * 86400


def _monthly(ee: Any, collection: str, period: str) -> Any:
    return ee.ImageCollection(collection).filterDate(*_month(ee, period))


def _yearly(ee: Any, collection: str, period: str) -> Any:
    return ee.ImageCollection(collection).filterDate(*_year(ee, period))


def _bbox(ee: Any) -> Any:
    return ee.Geometry.Rectangle(list(INDONESIA_BBOX))


def _km2(ee: Any) -> Any:
    return ee.Image.pixelArea().divide(1e6)


def _area(ee: Any, mask: Any) -> Any:
    """km² of the pixels where `mask` is true, zero elsewhere."""
    return mask.unmask(0).multiply(_km2(ee))


def _classes(ee: Any, image: Any, classes: dict[str, int | tuple[int, ...]]) -> Any:
    """One km² band per class of a categorical image."""
    bands = []
    for key, values in classes.items():
        values = values if isinstance(values, tuple) else (values,)
        mask = image.eq(values[0])
        for value in values[1:]:
            mask = mask.Or(image.eq(value))
        bands.append(_area(ee, mask).rename(key))
    return ee.Image.cat(bands)


def _arcsec_area(ee: Any, arcsec: float) -> Any:
    """The area in m² of a pixel `arcsec` on a side, at each pixel's latitude."""
    side = EARTH_RADIUS_M * math.radians(arcsec / 3600)
    latitude = ee.Image.pixelLonLat().select("latitude").multiply(math.pi / 180).cos()
    return latitude.multiply(side * side)


def _count(ee: Any, per_pixel: Any, native_area: Any) -> Any:
    """A per-pixel count, as a count within each pixel at any scale.

    Divided by the native pixel's area it is a density; times the area of the
    pixel it is read at, it is that pixel's count — so a sum over a province
    comes out the same whether it is read at 100 m or 1 km.
    """
    return per_pixel.divide(native_area).multiply(ee.Image.pixelArea())


def _kelvin(image: Any) -> Any:
    return image.subtract(273.15)


# -- monthly -------------------------------------------------------------------


def _chirps(ee: Any, period: str) -> Any:
    days = _monthly(ee, "UCSB-CHG/CHIRPS/DAILY", period).select("precipitation")
    return ee.Image.cat([
        days.sum().rename("precipitation"),
        days.map(lambda day: day.gte(1)).sum().rename("wet_days"),
    ])  # fmt: skip


def _era5(ee: Any, period: str) -> Any:
    days = _monthly(ee, "ECMWF/ERA5_LAND/DAILY_AGGR", period)
    return ee.Image.cat([
        _kelvin(days.select("temperature_2m").mean()).rename("temperature"),
        _kelvin(days.select("temperature_2m_max").mean()).rename("temperature_max"),
        _kelvin(days.select("temperature_2m_min").mean()).rename("temperature_min"),
        days.select("total_precipitation_sum").sum().multiply(1000).rename("precipitation"),
        # ERA5 writes evaporation as a negative flux: water leaving the surface.
        days.select("total_evaporation_sum").sum().multiply(-1000).rename("evaporation"),
        days.select("volumetric_soil_water_layer_1").mean().rename("soil_water"),
    ])  # fmt: skip


def _modis_vi(ee: Any, period: str) -> Any:
    image = _monthly(ee, "MODIS/061/MOD13A3", period).mean()
    return ee.Image.cat([
        image.select("NDVI").multiply(0.0001).rename("ndvi"),
        image.select("EVI").multiply(0.0001).rename("evi"),
    ])  # fmt: skip


def _modis_lst(ee: Any, period: str) -> Any:
    image = _monthly(ee, "MODIS/061/MOD11A2", period).mean()
    return ee.Image.cat([
        _kelvin(image.select("LST_Day_1km").multiply(0.02)).rename("lst_day"),
        _kelvin(image.select("LST_Night_1km").multiply(0.02)).rename("lst_night"),
    ])  # fmt: skip


def _burned(ee: Any, period: str) -> Any:
    burned = _monthly(ee, "MODIS/061/MCD64A1", period).select("BurnDate").max().gt(0)
    return _area(ee, burned).rename("burned_area")


def _viirs_monthly(ee: Any, period: str) -> Any:
    image = _monthly(ee, "NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG", period).select("avg_rad").mean()
    return image.rename("radiance")


def _viirs_fire(ee: Any, period: str) -> Any:
    # FireMask 7, 8 and 9 are fire at low, nominal and high confidence. Summed
    # over the month's daily images, each 1 km pixel counts once per day it burned.
    days = _monthly(ee, "NASA/VIIRS/002/VNP14A1", period).select("FireMask")
    # Not unmasked: an unmasked composite of these reads as zero everywhere.
    return days.map(lambda day: day.gte(7)).sum().rename("fire_pixel_days")


def _smap(ee: Any, period: str) -> Any:
    days = _monthly(ee, "NASA/SMAP/SPL3SMP_E/006", period).select("soil_moisture_am")
    return days.mean().rename("soil_moisture")


def _imerg(ee: Any, period: str) -> Any:
    # A mean rate in mm/hr over the month.
    image = _monthly(ee, "NASA/GPM_L3/IMERG_MONTHLY_V07", period).select("precipitation").mean()
    return image.multiply(_seconds(period) / 3600).rename("precipitation")


def _terraclimate(ee: Any, period: str) -> Any:
    image = _monthly(ee, "IDAHO_EPSCOR/TERRACLIMATE", period).mean()
    return ee.Image.cat([
        image.select("pr").rename("precipitation"),
        image.select("tmmx").multiply(0.1).rename("temperature_max"),
        image.select("tmmn").multiply(0.1).rename("temperature_min"),
        image.select("pdsi").multiply(0.01).rename("pdsi"),
        image.select("def").multiply(0.1).rename("water_deficit"),
        image.select("soil").multiply(0.1).rename("soil_moisture"),
    ])  # fmt: skip


def _persiann(ee: Any, period: str) -> Any:
    days = _monthly(ee, "NOAA/PERSIANN-CDR", period).select("precipitation")
    return days.sum().rename("precipitation")


def _gldas(ee: Any, period: str) -> Any:
    steps = _monthly(ee, "NASA/GLDAS/V021/NOAH/G025/T3H", period)
    seconds = _seconds(period)
    return ee.Image.cat([
        _kelvin(steps.select("Tair_f_inst").mean()).rename("temperature"),
        # kg/m²/s averaged over the month, which is mm/s.
        steps.select("Rainf_f_tavg").mean().multiply(seconds).rename("precipitation"),
        steps.select("Evap_tavg").mean().multiply(seconds).rename("evaporation"),
        steps.select("SoilMoi0_10cm_inst").mean().rename("soil_moisture"),
    ])  # fmt: skip


def _grace(ee: Any, period: str) -> Any:
    # A GRACE solution spans about a month but rarely a calendar one — it can
    # run 16 January to 15 February — so a month takes the solutions that cover
    # its 15th, not the ones that start in it: filtering by start loses every
    # month whose solution began in the month before.
    middle = ee.Date(f"{period}-15")
    images = (
        ee.ImageCollection("NASA/GRACE/MASS_GRIDS_V04/MASCON_CRI")
        .filter(ee.Filter.lte("system:time_start", middle.millis()))
        .filter(ee.Filter.gt("system:time_end", middle.millis()))
    )
    return images.select("lwe_thickness").mean().rename("water_storage")


# -- annual --------------------------------------------------------------------


def _viirs_annual(ee: Any, period: str) -> Any:
    collection = "NOAA/VIIRS/DNB/ANNUAL_V21" if period <= "2021" else "NOAA/VIIRS/DNB/ANNUAL_V22"
    return _yearly(ee, collection, period).select("average").mean().rename("radiance")


_IGBP = {
    "evergreen_needleleaf": 1, "evergreen_broadleaf": 2, "deciduous_needleleaf": 3,
    "deciduous_broadleaf": 4, "mixed_forest": 5, "closed_shrubland": 6,
    "open_shrubland": 7, "woody_savanna": 8, "savanna": 9, "grassland": 10,
    "wetland": 11, "cropland": 12, "urban": 13, "cropland_mosaic": 14,
    "snow_ice": 15, "barren": 16, "water": 17,
}  # fmt: skip


def _modis_lc(ee: Any, period: str) -> Any:
    image = _yearly(ee, "MODIS/061/MCD12Q1", period).first().select("LC_Type1")
    return _classes(ee, image, _IGBP)


_DYNAMIC_WORLD = (
    "water", "trees", "grass", "flooded_vegetation", "crops",
    "shrub_and_scrub", "built", "bare",
)  # fmt: skip


def _dynamic_world(ee: Any, period: str) -> Any:
    # The year's mean probability of each class, times the pixel's area: the
    # area the classifier expects each class to cover.
    images = _yearly(ee, "GOOGLE/DYNAMICWORLD/V1", period).filterBounds(_bbox(ee))
    mean = images.select(list(_DYNAMIC_WORLD)).mean()
    return mean.multiply(_km2(ee)).rename(list(_DYNAMIC_WORLD))


def _ghsl(ee: Any, period: str) -> Any:
    # Both on a 100 m Mollweide grid, which is equal-area: 10,000 m² a pixel.
    built = _yearly(ee, "JRC/GHSL/P2023A/GHS_BUILT_S", period).first().select("built_surface")
    people = _yearly(ee, "JRC/GHSL/P2023A/GHS_POP", period).first().select("population_count")
    return ee.Image.cat([
        _count(ee, built, 1e4).divide(1e6).rename("built_surface"),
        _count(ee, people, 1e4).rename("population"),
    ])  # fmt: skip


def _gpw(ee: Any, period: str) -> Any:
    people = _yearly(ee, "CIESIN/GPWv411/GPW_Population_Count", period).first()
    return _count(ee, people.select("population_count"), _arcsec_area(ee, 30)).rename("population")


def _worldpop(ee: Any, period: str) -> Any:
    people = (
        _yearly(ee, "WorldPop/GP/100m/pop", period)
        .filter(ee.Filter.eq("country", "IDN"))
        .select("population")
        .mosaic()
    )
    return _count(ee, people, _arcsec_area(ee, 3)).rename("population")


def _jrc_water(ee: Any, period: str) -> Any:
    water = _yearly(ee, "JRC/GSW1_4/YearlyHistory", period).first().select("waterClass")
    return _classes(ee, water, {"permanent_water": 3, "seasonal_water": 2})


def _floods(ee: Any, period: str) -> Any:
    events = _yearly(ee, "GLOBAL_FLOOD_DB/MODIS_EVENTS/V1", period).select("flooded")
    # A year with no mapped event is a year of zero flooded area, not an error:
    # a constant zero joins the events so `max` always has something to take.
    none = ee.ImageCollection([ee.Image.constant(0).toByte().rename("flooded")])
    flooded = events.merge(none).max()
    permanent = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("transition").eq(1).unmask(0)
    return _area(ee, flooded.gt(0).And(permanent.Not())).rename("flooded_area")


def _mangroves(ee: Any, period: str) -> Any:
    extent = (
        ee.ImageCollection("projects/sat-io/open-datasets/GMW/extent/GMW_V3")
        .filterDate(*_year(ee, period))
        .filterBounds(_bbox(ee))
        .mosaic()
    )
    # A 0/1 mask with a MEAN pyramid: read at 100 m, each pixel is the share of
    # it under mangrove, which times its area is the mangrove in it.
    return extent.select(0).unmask(0).multiply(_km2(ee)).rename("mangrove_area")


_HANSEN = "UMD/hansen/global_forest_change_2025_v1_13"


def _forest(ee: Any, period: str) -> Any:
    image = ee.Image(_HANSEN)
    if period == "2000":
        return _area(ee, image.select("treecover2000").gte(30)).rename("tree_cover")
    loss = image.select("lossyear").eq(int(period) - 2000)
    return _area(ee, loss).rename("tree_cover_loss")


def _buildings(ee: Any, period: str) -> Any:
    count = (
        _yearly(ee, "GOOGLE/Research/open-buildings-temporal/v1", period)
        .filterBounds(_bbox(ee))
        .select("building_fractional_count")
        .mosaic()
    )
    # A fractional count per 0.5 m pixel, in UTM: 0.25 m² each.
    return _count(ee, count, 0.25).rename("buildings")


_WORLDCOVER = {
    "tree_cover": 10, "shrubland": 20, "grassland": 30, "cropland": 40,
    "built_up": 50, "bare": 60, "water": 80, "herbaceous_wetland": 90,
    "mangroves": 95,
}  # fmt: skip


def _worldcover(ee: Any, period: str) -> Any:
    version = "ESA/WorldCover/v100" if period == "2020" else "ESA/WorldCover/v200"
    image = ee.ImageCollection(version).first().select("Map")
    return _classes(ee, image, _WORLDCOVER)


# -- static ---------------------------------------------------------------------


def _terrain(ee: Any, dem: Any) -> Any:
    # Every DEM on the same 100 m grid before its slope is taken, so the three
    # are comparable and none is differentiated at 30 m over two million km².
    dem = dem.reproject(crs="EPSG:4326", scale=100)
    return ee.Image.cat([
        dem.rename("elevation"),
        dem.rename("elevation_max"),
        ee.Terrain.slope(dem).rename("slope"),
    ])  # fmt: skip


def _copernicus_dem(ee: Any, period: str) -> Any:
    tiles = ee.ImageCollection("COPERNICUS/DEM/GLO30_2024_1").filterBounds(_bbox(ee))
    return _terrain(ee, tiles.select("DEM").mosaic())


def _srtm(ee: Any, period: str) -> Any:
    return _terrain(ee, ee.Image("USGS/SRTMGL1_003").select("elevation"))


def _nasadem(ee: Any, period: str) -> Any:
    return _terrain(ee, ee.Image("NASA/NASADEM_HGT/001").select("elevation"))


def _soilgrids(ee: Any, period: str) -> Any:
    root = "projects/soilgrids-isric"
    # SoilGrids stores integers: soil organic carbon in dg/kg, pH × 10, clay in g/kg.
    soc = ee.Image(f"{root}/soc_mean")
    ph = ee.Image(f"{root}/phh2o_mean")
    clay = ee.Image(f"{root}/clay_mean")
    return ee.Image.cat([
        soc.select("soc_0-5cm_mean").divide(10).rename("soc_topsoil"),
        soc.select("soc_15-30cm_mean").divide(10).rename("soc_subsoil"),
        ph.select("phh2o_0-5cm_mean").divide(10).rename("ph_topsoil"),
        ph.select("phh2o_15-30cm_mean").divide(10).rename("ph_subsoil"),
        clay.select("clay_0-5cm_mean").divide(10).rename("clay_topsoil"),
        clay.select("clay_15-30cm_mean").divide(10).rename("clay_subsoil"),
    ])  # fmt: skip


def _gfsad(ee: Any, period: str) -> Any:
    image = ee.Image("USGS/GFSAD1000_V1").select("landcover")
    return _classes(ee, image, {
        "irrigated_major": 1, "irrigated_minor": 2, "rainfed": 3,
        "rainfed_minor": 4, "rainfed_very_minor": 5,
    })  # fmt: skip


# -- the catalogue ----------------------------------------------------------------

_ESA = "European Space Agency / Copernicus"
_NASA = "NASA"
_KM2 = "km²"


def _years(first: int, last: int, step: int = 1) -> tuple[str, ...]:
    return tuple(str(year) for year in range(first, last + 1, step))


_TERRAIN_BANDS = (
    Band("elevation", "Mean elevation", "m"),
    Band("elevation_max", "Highest point", "m", stat="max"),
    Band("slope", "Mean slope", "degrees"),
)

PRODUCTS: tuple[Product, ...] = (
    # -- climate, monthly ---------------------------------------------------------
    Product(
        slug="chirps-rainfall",
        title="Rainfall by province (CHIRPS)",
        description=(
            "Monthly rainfall over each province since 1981 from CHIRPS, which blends "
            "infrared satellite estimates with station records at ~5 km: the total "
            "depth, as the province's mean, and the number of days with 1 mm or more."
        ),
        tags=("climate", "rainfall", "precipitation", "weather", "province"),
        organization="UCSB Climate Hazards Center",
        publisher="Climate Hazards Center, UC Santa Barbara",
        license="CC0 / public domain",
        collection="UCSB-CHG/CHIRPS/DAILY",
        bands=(
            Band("precipitation", "Monthly rainfall (CHIRPS)", "mm"),
            Band("wet_days", "Days with rainfall of 1 mm or more (CHIRPS)", "days"),
        ),
        build=_chirps,
        cadence="monthly",
        first="1981-01",
        scale=5000,
    ),
    Product(
        slug="era5-land",
        title="Temperature, rainfall and soil water by province (ERA5-Land)",
        description=(
            "ECMWF's ERA5-Land reanalysis, from its daily aggregates, as monthly "
            "province means since 1981: air temperature and its daily highs and lows, "
            "total precipitation and evaporation, and the water in the top 7 cm of soil. "
            "A model of the land surface driven by observations, at ~9 km."
        ),
        tags=("climate", "temperature", "rainfall", "reanalysis", "province"),
        organization="ECMWF / Copernicus Climate Change Service",
        publisher="ECMWF",
        license="Copernicus licence — free, attribution required",
        collection="ECMWF/ERA5_LAND/DAILY_AGGR",
        bands=(
            Band("temperature", "Mean air temperature (ERA5-Land)", "°C"),
            Band("temperature_max", "Mean daily maximum temperature (ERA5-Land)", "°C"),
            Band("temperature_min", "Mean daily minimum temperature (ERA5-Land)", "°C"),
            Band("precipitation", "Monthly precipitation (ERA5-Land)", "mm"),
            Band("evaporation", "Monthly evaporation (ERA5-Land)", "mm"),
            Band("soil_water", "Topsoil volumetric water, 0–7 cm (ERA5-Land)", "m³/m³"),
        ),
        build=_era5,
        cadence="monthly",
        first="1981-01",
        scale=5000,
    ),
    Product(
        slug="gpm-imerg-rainfall",
        title="Rainfall by province (GPM IMERG)",
        description=(
            "Monthly rainfall since 1998 from NASA and JAXA's IMERG, merging the "
            "GPM constellation's microwave and infrared estimates with gauges at "
            "0.1°. A satellite estimate independent of CHIRPS, worth reading beside it."
        ),
        tags=("climate", "rainfall", "precipitation", "remote-sensing", "province"),
        organization="NASA / JAXA",
        publisher="NASA Goddard Earth Sciences Data and Information Services Center",
        license="NASA open data — no restrictions",
        collection="NASA/GPM_L3/IMERG_MONTHLY_V07",
        bands=(Band("precipitation", "Monthly rainfall (GPM IMERG)", "mm"),),
        build=_imerg,
        cadence="monthly",
        first="1998-01",
        scale=5000,
    ),
    Product(
        slug="persiann-cdr-rainfall",
        title="Rainfall by province (PERSIANN-CDR)",
        description=(
            "Monthly rainfall since 1983 from PERSIANN-CDR, a climate data record "
            "estimated from infrared imagery by a neural network and bias-adjusted "
            "to GPCP, at 0.25°. Coarse, but the longest consistent satellite record."
        ),
        tags=("climate", "rainfall", "precipitation", "remote-sensing", "province"),
        organization="NOAA / UC Irvine CHRS",
        publisher="NOAA National Centers for Environmental Information",
        license="NOAA open data — no restrictions",
        collection="NOAA/PERSIANN-CDR",
        bands=(Band("precipitation", "Monthly rainfall (PERSIANN-CDR)", "mm"),),
        build=_persiann,
        cadence="monthly",
        first="1983-01",
        scale=5000,
    ),
    Product(
        slug="terraclimate",
        title="Climate and water balance by province (TerraClimate)",
        description=(
            "TerraClimate's monthly climate and water balance since 1981 at ~4 km: "
            "rainfall, daily highs and lows, the Palmer drought severity index, "
            "climatic water deficit and soil moisture. Published with a lag of a year "
            "or more."
        ),
        tags=("climate", "drought", "water-balance", "temperature", "province"),
        organization="University of Idaho",
        publisher="Climatology Lab, University of Idaho",
        license="CC0 / public domain",
        collection="IDAHO_EPSCOR/TERRACLIMATE",
        bands=(
            Band("precipitation", "Monthly rainfall (TerraClimate)", "mm"),
            Band("temperature_max", "Mean daily maximum temperature (TerraClimate)", "°C"),
            Band("temperature_min", "Mean daily minimum temperature (TerraClimate)", "°C"),
            Band("pdsi", "Palmer drought severity index (TerraClimate)", "index"),
            Band("water_deficit", "Climatic water deficit (TerraClimate)", "mm"),
            Band("soil_moisture", "Soil moisture (TerraClimate)", "mm"),
        ),
        build=_terraclimate,
        cadence="monthly",
        first="1981-01",
        scale=4000,
    ),
    Product(
        slug="gldas",
        title="Land surface model by province (GLDAS)",
        description=(
            "NASA's GLDAS-2.1 Noah land surface model, three-hourly at 0.25°, as "
            "monthly province figures since 2000: air temperature, rainfall, "
            "evaporation and the water in the top 10 cm of soil."
        ),
        tags=("climate", "hydrology", "soil-moisture", "reanalysis", "province"),
        organization="NASA",
        publisher="NASA Goddard Space Flight Center",
        license="NASA open data — no restrictions",
        collection="NASA/GLDAS/V021/NOAH/G025/T3H",
        bands=(
            Band("temperature", "Mean air temperature (GLDAS)", "°C"),
            Band("precipitation", "Monthly rainfall (GLDAS)", "mm"),
            Band("evaporation", "Monthly evapotranspiration (GLDAS)", "mm"),
            Band("soil_moisture", "Soil moisture, 0–10 cm (GLDAS)", "kg/m²"),
        ),
        build=_gldas,
        cadence="monthly",
        first="2000-01",
        scale=5000,
    ),
    Product(
        slug="grace-water-storage",
        title="Terrestrial water storage by province (GRACE)",
        description=(
            "The GRACE and GRACE-FO satellites' monthly measure of all the water on "
            "and under the land — soil, groundwater, lakes — as an anomaly against "
            "the 2004–2009 mean, in cm of equivalent water depth. JPL's mascon "
            "solution, whose real resolution is ~300 km: a province figure is a "
            "regional one. There is a gap between the two missions in 2017–2018."
        ),
        tags=("hydrology", "groundwater", "water", "remote-sensing", "province"),
        organization="NASA / GFZ / JPL",
        publisher="NASA Jet Propulsion Laboratory",
        license="NASA open data — no restrictions",
        collection="NASA/GRACE/MASS_GRIDS_V04/MASCON_CRI",
        bands=(Band("water_storage", "Terrestrial water storage anomaly (GRACE)", "cm"),),
        build=_grace,
        cadence="monthly",
        first="2002-04",
        scale=5000,
    ),
    Product(
        slug="smap-soil-moisture",
        title="Soil moisture by province (SMAP)",
        description=(
            "Surface soil moisture, top 5 cm, from NASA's SMAP radiometer on its "
            "morning passes, as a monthly province mean since April 2015. Enhanced "
            "9 km product; dense forest and open water are masked by SMAP itself."
        ),
        tags=("soil-moisture", "drought", "agriculture", "remote-sensing", "province"),
        organization="NASA",
        publisher="NASA National Snow and Ice Data Center",
        license="NASA open data — no restrictions",
        collection="NASA/SMAP/SPL3SMP_E/006",
        bands=(Band("soil_moisture", "Surface soil moisture (SMAP)", "m³/m³"),),
        build=_smap,
        cadence="monthly",
        first="2015-04",
        scale=5000,
    ),
    # -- land surface, monthly -------------------------------------------------------
    Product(
        slug="modis-vegetation",
        title="Vegetation greenness by province (MODIS)",
        description=(
            "MODIS Terra's monthly vegetation indices at 1 km since 2000: NDVI and "
            "EVI averaged over each province. EVI saturates less over dense tropical "
            "canopy, which is most of Indonesia's."
        ),
        tags=("vegetation", "ndvi", "agriculture", "remote-sensing", "province"),
        organization="NASA",
        publisher="NASA LP DAAC",
        license="NASA open data — no restrictions",
        collection="MODIS/061/MOD13A3",
        bands=(
            Band("ndvi", "Normalized difference vegetation index (MODIS)", "index"),
            Band("evi", "Enhanced vegetation index (MODIS)", "index"),
        ),
        build=_modis_vi,
        cadence="monthly",
        first="2000-02",
        scale=900,
    ),
    Product(
        slug="modis-land-surface-temperature",
        title="Land surface temperature by province (MODIS)",
        description=(
            "How hot the ground itself is, day and night, from MODIS Terra's 8-day "
            "composites at 1 km, as monthly province means since 2000. Skin "
            "temperature under clear sky, not air temperature — it runs well above "
            "it in the day over bare and built land."
        ),
        tags=("temperature", "heat", "climate", "remote-sensing", "province"),
        organization="NASA",
        publisher="NASA LP DAAC",
        license="NASA open data — no restrictions",
        collection="MODIS/061/MOD11A2",
        bands=(
            Band("lst_day", "Daytime land surface temperature (MODIS)", "°C"),
            Band("lst_night", "Night-time land surface temperature (MODIS)", "°C"),
        ),
        build=_modis_lst,
        cadence="monthly",
        first="2000-03",
        scale=900,
    ),
    Product(
        slug="modis-burned-area",
        title="Burned area by province (MODIS)",
        description=(
            "Area burned each month since November 2000, from MODIS's MCD64A1 at "
            "500 m: every pixel whose burn date falls in the month, summed over the "
            "province. Small and peat-smouldering fires under 500 m escape it."
        ),
        tags=("fire", "forest-fire", "haze", "environment", "province"),
        organization="NASA",
        publisher="NASA LP DAAC",
        license="NASA open data — no restrictions",
        collection="MODIS/061/MCD64A1",
        bands=(Band("burned_area", "Burned area (MODIS)", _KM2, stat="sum"),),
        build=_burned,
        cadence="monthly",
        first="2000-11",
        scale=460,
    ),
    Product(
        slug="viirs-active-fire",
        title="Active fires by province (VIIRS)",
        description=(
            "Fire detections from VIIRS on Suomi NPP since 2012, from the daily "
            "1 km VNP14A1 fire mask: each pixel flagged as fire at low, nominal or "
            "high confidence counts once per day it burns, summed over the month."
        ),
        tags=("fire", "forest-fire", "haze", "hotspots", "province"),
        organization="NASA",
        publisher="NASA LP DAAC",
        license="NASA open data — no restrictions",
        collection="NASA/VIIRS/002/VNP14A1",
        bands=(
            Band("fire_pixel_days", "Active fire detections (VIIRS)", "pixel-days", stat="sum"),
        ),  # fmt: skip
        build=_viirs_fire,
        cadence="monthly",
        first="2012-02",
        scale=900,
    ),
    Product(
        slug="viirs-nightlights-monthly",
        title="Night-time lights by province, monthly (VIIRS)",
        description=(
            "Average night-time radiance from VIIRS's day/night band since 2014, "
            "stray-light corrected, at ~500 m: a province mean that tracks "
            "electrification and economic activity. Cloud-free coverage is thin in "
            "the wet season, and fires and gas flares light up too."
        ),
        tags=("nightlights", "economy", "electrification", "remote-sensing", "province"),
        organization="NOAA / Earth Observation Group",
        publisher="Earth Observation Group, Colorado School of Mines",
        license="CC BY 4.0",
        collection="NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG",
        bands=(Band("radiance", "Mean night-time radiance (VIIRS, monthly)", "nW/cm²/sr"),),
        build=_viirs_monthly,
        cadence="monthly",
        first="2014-01",
        scale=460,
    ),
    # -- annual -------------------------------------------------------------------------
    Product(
        slug="viirs-nightlights-annual",
        title="Night-time lights by province, annual (VIIRS)",
        description=(
            "The Earth Observation Group's annual VIIRS night-time lights since "
            "2013, with fires, flares and background noise screened out: mean "
            "radiance per province. Versions 2.1 (to 2021) and 2.2 (from 2022)."
        ),
        tags=("nightlights", "economy", "electrification", "remote-sensing", "province"),
        organization="NOAA / Earth Observation Group",
        publisher="Earth Observation Group, Colorado School of Mines",
        license="CC BY 4.0",
        collection="NOAA/VIIRS/DNB/ANNUAL_V21",
        bands=(Band("radiance", "Mean night-time radiance (VIIRS, annual)", "nW/cm²/sr"),),
        build=_viirs_annual,
        cadence="annual",
        periods=_years(2013, 2025),
        scale=460,
    ),
    Product(
        slug="modis-land-cover",
        title="Land cover by province (MODIS)",
        description=(
            "MODIS's annual land cover at 500 m since 2001, in the IGBP scheme: "
            "the area each class covers in each province. A 500 m classification "
            "blurs smallholder mosaics, so compare years rather than trust a class "
            "to the hectare."
        ),
        tags=("land-cover", "land-use", "forests", "remote-sensing", "province"),
        organization="NASA",
        publisher="NASA LP DAAC",
        license="NASA open data — no restrictions",
        collection="MODIS/061/MCD12Q1",
        bands=tuple(
            Band(key, f"Land cover: {key.replace('_', ' ')} (MODIS IGBP)", _KM2, stat="sum")
            for key in _IGBP
        ),
        build=_modis_lc,
        cadence="annual",
        periods=_years(2001, 2024),
        scale=460,
    ),
    Product(
        slug="dynamic-world",
        title="Land cover by province (Dynamic World)",
        description=(
            "Google and WRI's Dynamic World, classified from every Sentinel-2 scene "
            "at 10 m: the year's mean probability of each class, times area, read at "
            "1 km. It is the area the classifier expects each class to cover, not a "
            "count of pixels labelled so. Snow and ice left out."
        ),
        tags=("land-cover", "land-use", "built-up", "remote-sensing", "province"),
        organization="Google / World Resources Institute",
        publisher="Google and World Resources Institute",
        license="CC BY 4.0",
        collection="GOOGLE/DYNAMICWORLD/V1",
        bands=tuple(
            Band(key, f"Land cover: {key.replace('_', ' ')} (Dynamic World)", _KM2, stat="sum")
            for key in _DYNAMIC_WORLD
        ),
        build=_dynamic_world,
        cadence="annual",
        periods=_years(2016, 2025),
        scale=1000,
        tile_scale=16,
    ),
    Product(
        slug="esa-worldcover",
        title="Land cover by province (ESA WorldCover)",
        description=(
            "ESA's WorldCover, 10 m land cover from Sentinel-1 and -2, for 2020 "
            "(v100) and 2021 (v200): each class's area per province, read at 100 m "
            "from the 10 m map. The two versions use different "
            "algorithms, so the change between them is partly the method."
        ),
        tags=("land-cover", "land-use", "mangroves", "remote-sensing", "province"),
        organization=_ESA,
        publisher="European Space Agency",
        license="CC BY 4.0",
        collection="ESA/WorldCover/v200",
        bands=tuple(
            Band(key, f"Land cover: {key.replace('_', ' ')} (ESA WorldCover)", _KM2, stat="sum")
            for key in _WORLDCOVER
        ),
        build=_worldcover,
        cadence="annual",
        periods=("2020", "2021"),
        scale=100,
        split=True,
        tile_scale=16,
    ),
    Product(
        slug="global-forest-change",
        title="Tree cover and tree cover loss by province (Hansen)",
        description=(
            "The University of Maryland's Global Forest Change, v1.13: tree cover "
            "in 2000 (canopy of 30% or more) and the area of it lost each year since "
            "2001, read at 100 m from the 30 m map. Loss is any "
            "stand-replacing disturbance — clearing, fire, plantation harvest — not "
            "deforestation alone."
        ),
        tags=("forests", "deforestation", "tree-cover", "remote-sensing", "province"),
        organization="University of Maryland",
        publisher="Hansen/UMD/Google/USGS/NASA",
        license="CC BY 4.0",
        collection=_HANSEN,
        bands=(
            Band(
                "tree_cover",
                "Tree cover, 30% canopy or more, in 2000 (Hansen)",
                _KM2,
                stat="sum",
                first="2000",
                last="2000",
            ),
            Band("tree_cover_loss", "Tree cover loss (Hansen)", _KM2, stat="sum", first="2001"),
        ),  # fmt: skip
        build=_forest,
        cadence="annual",
        periods=_years(2000, 2025),
        scale=100,
        split=True,
        together=True,
        tile_scale=16,
    ),
    Product(
        slug="global-mangrove-watch",
        title="Mangrove extent by province (Global Mangrove Watch)",
        description=(
            "Global Mangrove Watch v3's mangrove extent, mapped from L-band radar "
            "and Landsat, for 1996 and 2007–2020: the area of mangrove in each "
            "province. Published through the GEE community catalogue."
        ),
        tags=("mangroves", "coastal", "forests", "blue-carbon", "province"),
        organization="Global Mangrove Watch",
        publisher="Aberystwyth University / soloEO / JAXA",
        license="CC BY 4.0",
        collection="projects/sat-io/open-datasets/GMW/extent/GMW_V3",
        bands=(
            Band("mangrove_area", "Mangrove area (Global Mangrove Watch)", _KM2, stat="sum"),
        ),  # fmt: skip
        build=_mangroves,
        cadence="annual",
        periods=("1996", *_years(2007, 2010), *_years(2015, 2020)),
        scale=100,
        split=True,
        tile_scale=8,
        url=f"{COMMUNITY}projects/mangrove/",
    ),
    Product(
        slug="jrc-surface-water",
        title="Surface water by province (JRC)",
        description=(
            "The European Commission JRC's Global Surface Water yearly history, "
            "classified from every Landsat scene since 1984: the area of water "
            "present all year and of water present only part of it, per province, "
            "read at 100 m from the 30 m map."
        ),
        tags=("water", "hydrology", "lakes", "remote-sensing", "province"),
        organization="European Commission Joint Research Centre",
        publisher="EC JRC / Google",
        license="Free and open, attribution required",
        collection="JRC/GSW1_4/YearlyHistory",
        bands=(
            Band("permanent_water", "Permanent surface water (JRC)", _KM2, stat="sum"),
            Band("seasonal_water", "Seasonal surface water (JRC)", _KM2, stat="sum"),
        ),
        build=_jrc_water,
        cadence="annual",
        periods=_years(1984, 2021),
        scale=100,
        split=True,
        tile_scale=16,
    ),
    Product(
        slug="global-flood-database",
        title="Flooded area by province (Global Flood Database)",
        description=(
            "Floods mapped from MODIS for the large events in the Dartmouth Flood "
            "Observatory's catalogue, 2000–2018: the area any mapped flood covered "
            "during the year, permanent water excluded. Only the events the "
            "database holds — most local floods are not in it."
        ),
        tags=("floods", "disasters", "water", "remote-sensing", "province"),
        organization="Cloud to Street / Dartmouth Flood Observatory",
        publisher="Cloud to Street",
        license="CC BY-NC 4.0",
        collection="GLOBAL_FLOOD_DB/MODIS_EVENTS/V1",
        bands=(
            Band(
                "flooded_area",
                "Area flooded during the year (Global Flood Database)",
                _KM2,
                stat="sum",
            ),
        ),  # fmt: skip
        build=_floods,
        cadence="annual",
        periods=_years(2000, 2018),
        scale=240,
        tile_scale=8,
    ),
    Product(
        slug="ghsl",
        title="Built-up surface and population by province (GHSL)",
        description=(
            "The European Commission JRC's Global Human Settlement Layer, release "
            "2023A, every five years from 1975 to 2020: the built-up surface and "
            "the resident population modelled onto it at 100 m, summed per province. "
            "Its 2025 and 2030 epochs are projections and are left out."
        ),
        tags=("population", "urbanisation", "built-up", "settlements", "province"),
        organization="European Commission Joint Research Centre",
        publisher="EC JRC",
        license="CC BY 4.0",
        collection="JRC/GHSL/P2023A/GHS_BUILT_S",
        bands=(
            Band("built_surface", "Built-up surface (GHSL)", _KM2, stat="sum"),
            Band("population", "Population (GHSL)", "people", stat="sum"),
        ),
        build=_ghsl,
        cadence="annual",
        periods=_years(1975, 2020, 5),
        scale=100,
        split=True,
        tile_scale=16,
    ),
    Product(
        slug="gpw-population",
        title="Population by province (GPW)",
        description=(
            "CIESIN's Gridded Population of the World v4.11: census counts spread "
            "evenly over each census unit at 1 km, summed per province, for 2000–2020 "
            "every five years. No model of where people live within a unit — "
            "which is the point of it beside WorldPop and GHSL."
        ),
        tags=("population", "demography", "census", "gridded", "province"),
        organization="CIESIN, Columbia University",
        publisher="NASA SEDAC",
        license="CC BY 4.0",
        collection="CIESIN/GPWv411/GPW_Population_Count",
        bands=(Band("population", "Population (GPW)", "people", stat="sum"),),
        build=_gpw,
        cadence="annual",
        periods=_years(2000, 2020, 5),
        scale=1000,
    ),
    Product(
        slug="worldpop-population",
        title="Population by province (WorldPop)",
        description=(
            "WorldPop's unconstrained population estimates at 100 m, annual 2000–2020, "
            "summed per province. Census totals redistributed by a model of "
            "settlement, land cover and roads."
        ),
        tags=("population", "demography", "gridded", "settlements", "province"),
        organization="WorldPop, University of Southampton",
        publisher="WorldPop",
        license="CC BY 4.0",
        collection="WorldPop/GP/100m/pop",
        bands=(Band("population", "Population (WorldPop)", "people", stat="sum"),),
        build=_worldpop,
        cadence="annual",
        periods=_years(2000, 2020),
        scale=1000,
        tile_scale=8,
    ),
    Product(
        slug="open-buildings",
        title="Buildings by province (Google Open Buildings)",
        description=(
            "Google Research's Open Buildings 2.5D temporal dataset, annual 2016–2023: "
            "the number of buildings in each province, from the per-pixel fractional "
            "counts its model estimates from Sentinel-2. A model's estimate, read "
            "from 100 m aggregates of a 0.5 m grid."
        ),
        tags=("buildings", "urbanisation", "settlements", "built-up", "province"),
        organization="Google Research",
        publisher="Google Research",
        license="CC BY 4.0",
        collection="GOOGLE/Research/open-buildings-temporal/v1",
        bands=(Band("buildings", "Buildings (Open Buildings)", "buildings", stat="sum"),),
        build=_buildings,
        cadence="annual",
        periods=_years(2016, 2023),
        scale=250,
        split=True,
        tile_scale=16,
    ),
    # -- static ----------------------------------------------------------------------
    Product(
        slug="copernicus-dem",
        title="Elevation and slope by province (Copernicus DEM)",
        description=(
            "The Copernicus GLO-30 digital surface model (2024 release, from "
            "TanDEM-X acquisitions of 2011–2015): mean and highest elevation and "
            "mean slope per province, read at 100 m. A surface model — the top of "
            "the canopy and of buildings, not bare ground."
        ),
        tags=("elevation", "terrain", "topography", "geography", "province"),
        organization="Copernicus / Airbus",
        publisher="European Space Agency",
        license="Copernicus DEM licence — free, attribution required",
        collection="COPERNICUS/DEM/GLO30_2024_1",
        bands=_TERRAIN_BANDS,
        build=_copernicus_dem,
        cadence="annual",
        periods=("2015",),
        scale=100,
        split=True,
        tile_scale=16,
    ),
    Product(
        slug="srtm-elevation",
        title="Elevation and slope by province (SRTM)",
        description=(
            "NASA's Shuttle Radar Topography Mission of February 2000, void-filled, "
            "at 30 m: mean and highest elevation and mean slope per province, read "
            "at 100 m."
        ),
        tags=("elevation", "terrain", "topography", "geography", "province"),
        organization="NASA / USGS",
        publisher="NASA / USGS",
        license="Public domain",
        collection="USGS/SRTMGL1_003",
        bands=_TERRAIN_BANDS,
        build=_srtm,
        cadence="annual",
        periods=("2000",),
        scale=100,
        split=True,
        tile_scale=16,
    ),
    Product(
        slug="nasadem",
        title="Elevation and slope by province (NASADEM)",
        description=(
            "NASADEM, SRTM's 2000 radar data reprocessed with improved void filling "
            "and ICESat control: mean and highest elevation and mean slope per "
            "province, read at 100 m."
        ),
        tags=("elevation", "terrain", "topography", "geography", "province"),
        organization="NASA",
        publisher="NASA JPL",
        license="Public domain",
        collection="NASA/NASADEM_HGT/001",
        bands=_TERRAIN_BANDS,
        build=_nasadem,
        cadence="annual",
        periods=("2000",),
        scale=100,
        split=True,
        tile_scale=16,
    ),
    Product(
        slug="soilgrids",
        title="Soil properties by province (SoilGrids)",
        description=(
            "ISRIC's SoilGrids 2.0, soil properties predicted at 250 m from soil "
            "profiles and covariates: organic carbon, pH in water and clay content, "
            "at 0–5 cm and 15–30 cm, as province means. Published by ISRIC through "
            "the GEE community catalogue."
        ),
        tags=("soil", "agriculture", "carbon", "land", "province"),
        organization="ISRIC World Soil Information",
        publisher="ISRIC World Soil Information",
        license="CC BY 4.0",
        collection="projects/soilgrids-isric",
        bands=(
            Band("soc_topsoil", "Soil organic carbon, 0–5 cm (SoilGrids)", "g/kg"),
            Band("soc_subsoil", "Soil organic carbon, 15–30 cm (SoilGrids)", "g/kg"),
            Band("ph_topsoil", "Soil pH in water, 0–5 cm (SoilGrids)", "pH"),
            Band("ph_subsoil", "Soil pH in water, 15–30 cm (SoilGrids)", "pH"),
            Band("clay_topsoil", "Clay content, 0–5 cm (SoilGrids)", "%"),
            Band("clay_subsoil", "Clay content, 15–30 cm (SoilGrids)", "%"),
        ),
        build=_soilgrids,
        cadence="annual",
        periods=("2020",),
        scale=250,
        split=True,
        tile_scale=8,
        url=f"{COMMUNITY}soil/soilgrids/",
    ),
    Product(
        slug="gfsad-cropland",
        title="Cropland by province (GFSAD)",
        description=(
            "The USGS Global Food Security-support Analysis Data cropland extent "
            "for nominal 2010 at 1 km: the area of each cropland class per province. "
            "A pixel counts whole whatever share of it is cropped, so the 'minor' "
            "and 'very minor fragments' classes overstate the cropped area."
        ),
        tags=("agriculture", "cropland", "irrigation", "food-security", "province"),
        organization="USGS / NASA",
        publisher="USGS",
        license="Public domain",
        collection="USGS/GFSAD1000_V1",
        bands=(
            Band("irrigated_major", "Cropland, irrigated, major (GFSAD)", _KM2, stat="sum"),
            Band("irrigated_minor", "Cropland, irrigated, minor (GFSAD)", _KM2, stat="sum"),
            Band("rainfed", "Cropland, rainfed (GFSAD)", _KM2, stat="sum"),
            Band("rainfed_minor", "Cropland, rainfed, minor fragments (GFSAD)", _KM2, stat="sum"),
            Band(
                "rainfed_very_minor",
                "Cropland, rainfed, very minor fragments (GFSAD)",
                _KM2,
                stat="sum",
            ),
        ),  # fmt: skip
        build=_gfsad,
        cadence="annual",
        periods=("2010",),
        scale=990,
    ),
)


def product_url(product: Product) -> str:
    return product.url or f"{CATALOG}{product.collection.replace('/', '_')}"


__all__ = ["PRODUCTS", "Band", "Product", "product_url"]
