"""Loading the dimension registries from committed reference data.

Dimension members live in CSV under `reference/`, not in code and not in the
database. They are small, they change rarely, and when they do change the diff
is the record of what changed — which matters for geography, where a province
appearing is a legal event with a date attached (program.md §11).

The files are the authority. `terusan silver dimensions` publishes them into
Silver; nothing writes back the other way.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import structlog

from .dimensions import (
    Commodity,
    CommodityRegistry,
    Geography,
    GeographyRegistry,
    GeoType,
)

log = structlog.get_logger(__name__)

#: Repository root, found by walking up from this file. Reference data is
#: committed, so it sits beside the code rather than in the lake.
REFERENCE_ROOT = Path(__file__).resolve().parents[4] / "reference"

GEOGRAPHY_DIR = REFERENCE_ROOT / "geography"
COMMODITY_DIR = REFERENCE_ROOT / "commodities"

#: Aliases are pipe-separated: a comma would collide with the CSV itself, and
#: several names legitimately contain one.
ALIAS_SEPARATOR = "|"


class ReferenceDataError(ValueError):
    """Reference data that cannot be loaded.

    Raised rather than skipped. A dimension that silently loads half its members
    resolves half its names, and the other half look like unknown places.
    """


def _rows(path: Path) -> Iterator[dict[str, str]]:
    """Read a reference CSV, ignoring `#` comment lines.

    Comments carry provenance — where a code set came from, what still needs
    verifying — and that belongs next to the data rather than in a separate
    document nobody opens.
    """
    if not path.exists():
        raise ReferenceDataError(f"reference file not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(line for line in handle if not line.startswith("#"))


def _aliases(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(ALIAS_SEPARATOR) if part.strip())


def _date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ReferenceDataError(f"bad date {value!r}: {exc}") from exc


def load_countries(path: Path | None = None) -> list[Geography]:
    """Countries, from the ISO 3166-1 alpha-3 reference file."""
    source = path or GEOGRAPHY_DIR / "countries.csv"
    countries = []
    for row in _rows(source):
        iso3 = (row.get("iso3") or "").strip()
        if not iso3:
            continue
        countries.append(
            Geography(
                geo_id=iso3,
                name=(row.get("name") or "").strip(),
                geo_type=GeoType.COUNTRY,
                country_code=iso3,
                iso_code=(row.get("iso2") or "").strip() or None,
                # The ISO2 code is an alias so a source publishing `ID` rather
                # than `IDN` still resolves.
                aliases=tuple(a for a in [(row.get("iso2") or "").strip()] if a),
            )
        )
    return countries


def load_aggregates(path: Path | None = None) -> list[Geography]:
    """World Bank aggregate groupings, as regions.

    Recorded rather than dropped. They are real published figures, but they are
    sums of the countries beside them, so a total taken over the dataset must
    exclude them — which `geo_type = 'region'` lets a consumer do, and deleting
    the rows would not, since nobody can filter what is absent.
    """
    source = path or GEOGRAPHY_DIR / "worldbank-aggregates.csv"
    if not source.exists():
        return []
    return [
        Geography(
            geo_id=(row.get("code") or "").strip(),
            name=(row.get("name") or "").strip(),
            geo_type=GeoType.REGION,
            country_code=None,
        )
        for row in _rows(source)
        if (row.get("code") or "").strip()
    ]


def load_indonesia(path: Path | None = None) -> list[Geography]:
    """Indonesian administrative areas: provinces and the level below them.

    Both, because most Indonesian figures are published per regency or city —
    a district's movement share, a regency's budget realisation, a city's food
    prices — and a registry holding only the 38 provinces leaves every one of
    them resolving to nothing.

    `path` still names the provinces file alone, for a caller that wants only
    them; the regencies come from their own file beside it.
    """
    return [*load_provinces(path), *(load_regencies() if path is None else [])]


def load_provinces(path: Path | None = None) -> list[Geography]:
    """The 38 provinces, keyed by BPS code."""
    source = path or GEOGRAPHY_DIR / "indonesia-provinces.csv"
    areas = []
    for row in _rows(source):
        geo_id = (row.get("geo_id") or "").strip()
        if not geo_id:
            continue
        bps = (row.get("bps_code") or "").strip() or None
        areas.append(
            Geography(
                geo_id=geo_id,
                name=(row.get("name") or "").strip(),
                geo_type=GeoType((row.get("geo_type") or "province").strip()),
                parent_geo_id=(row.get("parent_geo_id") or "").strip() or None,
                country_code="ID",
                province_code=bps,
                bps_code=bps,
                valid_from=_date(row.get("valid_from")),
                valid_to=_date(row.get("valid_to")),
                aliases=_aliases(row.get("aliases")),
            )
        )
    return areas


def load_regencies(path: Path | None = None) -> list[Geography]:
    """Regencies and cities (kabupaten/kota), keyed by BPS code.

    Absent is not an error: a checkout without the file still resolves
    provinces, and a figure published per regency stays unresolved — which is
    the gap showing rather than a run failing (program.md §42).
    """
    source = path or GEOGRAPHY_DIR / "indonesia-regencies.csv"
    if not source.exists():
        return []

    areas = []
    for row in _rows(source):
        geo_id = (row.get("geo_id") or "").strip()
        if not geo_id:
            continue
        bps = (row.get("bps_code") or "").strip() or None
        areas.append(
            Geography(
                geo_id=geo_id,
                name=(row.get("name") or "").strip(),
                geo_type=GeoType((row.get("geo_type") or "regency").strip()),
                parent_geo_id=(row.get("parent_geo_id") or "").strip() or None,
                country_code="ID",
                # `11.05` belongs to province `11`: the prefix is the parent's
                # code, which is what makes a BPS code join upwards without a
                # lookup.
                province_code=bps.split(".")[0] if bps else None,
                regency_code=bps,
                bps_code=bps,
                valid_from=_date(row.get("valid_from")),
                valid_to=_date(row.get("valid_to")),
                aliases=_aliases(row.get("aliases")),
            )
        )
    return areas


def load_commodities(path: Path | None = None) -> list[Commodity]:
    """Commodities, from the registry file. Absent is not an error."""
    source = path or COMMODITY_DIR / "commodities.csv"
    if not source.exists():
        return []
    return [
        Commodity(
            commodity_id=(row.get("commodity_id") or "").strip(),
            canonical_name=(row.get("canonical_name") or "").strip(),
            category=(row.get("category") or "").strip() or None,
            subcategory=(row.get("subcategory") or "").strip() or None,
            hs_code=(row.get("hs_code") or "").strip() or None,
            hs_version=(row.get("hs_version") or "").strip() or None,
            unit_default=(row.get("unit_default") or "").strip() or None,
            aliases=_aliases(row.get("aliases")),
        )
        for row in _rows(source)
        if (row.get("commodity_id") or "").strip()
    ]


def geography_registry(
    *, countries: bool = True, aggregates: bool = True, indonesia: bool = True
) -> GeographyRegistry:
    """Build the geography registry from every reference file.

    All three sets load by default. A source publishing ISO3 country codes and
    one publishing BPS province codes resolve against the same registry, which
    is the point of having one.
    """
    registry = GeographyRegistry()
    loaded = 0
    seen: set[str] = set()
    for enabled, loader in (
        (countries, load_countries),
        (aggregates, load_aggregates),
        (indonesia, load_indonesia),
    ):
        if not enabled:
            continue
        for geography in loader():
            # A place may be described by more than one file — Indonesia is in
            # the country list and again in the provinces file, which is where
            # the names agencies actually print live. Registering it twice would
            # trip the duplicate guard and cost it every one of its names.
            if geography.geo_id in seen:
                registry.add(geography)
                continue
            seen.add(geography.geo_id)
            registry.add(geography)
            loaded += 1
    log.debug("reference.geography_loaded", members=loaded)
    return registry


def commodity_registry() -> CommodityRegistry:
    registry = CommodityRegistry()
    for commodity in load_commodities():
        registry.add(commodity)
    return registry
