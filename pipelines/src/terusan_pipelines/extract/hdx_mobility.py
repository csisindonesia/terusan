"""Meta's movement distribution into Bronze records, for Indonesia.

The generic CSV reader would work on this file and should not be used for it.
Each release is a hundred megabytes of every country Meta reports on, around
1.3 million rows, and Indonesia is roughly a twentieth of that. Read
generically, one release puts a million rows of Brazil and Nigeria into an
Indonesian warehouse's Bronze — which costs more than it sounds, because every
later normalization run scans them.

So this reader keeps the Indonesian rows and drops the rest, and it streams:
the file is read a line at a time rather than decoded whole, because a hundred
megabytes of text becomes several hundred megabytes of Python strings and the
extraction runner holds a batch besides.

One derived column is written. `ds` is the day the figures describe and reads
as a date, which is right — but Meta reports a fortnight per release and the
same `(district, category)` appears on each day in it, so `period` restates
the day as itself and nothing more. It is kept separate from `ds` so the
restatement can be changed without re-reading the file.

Nothing else is interpreted: `distance_category_ping_fraction` stays the string
Meta wrote, `0.02845009162974341`, and the distance category stays `[10, 100)`
— brackets and all, because the bracket is what says whether 100 km is in the
band.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from typing import Any

import structlog

from .base import ExtractionError, Extractor, Landed

log = structlog.get_logger(__name__)

SOURCE_SLUG = "hdx-meta-movement-distribution"

#: Meta's ISO3 for Indonesia, in the `country` column.
COUNTRY = "IDN"

CSV_SUFFIXES = {".csv"}

#: The columns Meta publishes. Checked rather than assumed: a release that
#: renames a column would otherwise yield rows of empty strings, which read
#: downstream as a district with no figures rather than as a broken parse.
REQUIRED_COLUMNS = (
    "gadm_id",
    "country",
    "home_to_ping_distance_category",
    "distance_category_ping_fraction",
    "ds",
)

#: How far people travelled, as Meta writes the bands. The label is what a
#: reader sees; the raw category is kept beside it.
CATEGORIES = {
    "0": "0 km",
    "(0, 10)": "0–10 km",
    "[10, 100)": "10–100 km",
    "100+": "100 km+",
}

#: Identifiers are per distance band, not per district: the district is a
#: dimension of the observation (`geo_column`), and folding it into the
#: identifier would make forty thousand indicators out of four.
NAMESPACE = "meta_movement"


def indicator_id(category: str) -> str:
    """The Silver identifier for one distance band.

    Derived from the band rather than numbered, so a release that adds a band
    does not renumber the others — and a band Meta stops publishing keeps the
    identifier its history was written under.
    """
    known = {"0": "0km", "(0, 10)": "0_10km", "[10, 100)": "10_100km", "100+": "100km_plus"}
    suffix = known.get(category.strip())
    if suffix is None:
        # An unrecognised band still becomes a series: the figures are real,
        # and silently dropping them would leave a gap nobody can see.
        lowered = category.lower()
        suffix = "".join(c if c.isalnum() else "_" for c in lowered)
        suffix = "_".join(part for part in suffix.split("_") if part) or "unknown"
    return f"{NAMESPACE}_{suffix}"


#: Polygons GADM names in a way no registry can resolve. Keyed by the GADM
#: code, which is what says which place is meant.
#:
#: GADM writes both Kabupaten Banjar in South Kalimantan and Kota Banjar in
#: West Java as `Banjar`. Left alone, the two land on one place and one
#: observation id — two different shares of movement claiming to be the same
#: figure, which Silver refuses to write rather than pick between.
_GADM_NAMES = {
    "IDN.9.3_1": "Kota Banjar",
}


def _district(row: dict[str, str]) -> str:
    """The place a figure is about, by the name GADM gives it.

    The bare name — `Bogor`, `Kota Bandung` — because the geography registry
    now holds Indonesian regencies and cities, and a bare name is what it
    resolves. The GADM code stays in its own column: it identifies the polygon
    Meta aggregated, which is a fact about the source rather than a part of
    the place's name.

    An earlier version folded the code into the label, when the registry held
    provinces only and `Gorontalo` would have resolved to the province and
    quietly turned one regency's figures into a province's. The registry now
    refuses a name two areas both answer to, so the name alone is safe: what
    it cannot resolve stays unresolved (program.md §42) rather than misfiled.
    """
    code = (row.get("gadm_id") or "").strip()
    name = (row.get("gadm_name") or "").strip()
    return _GADM_NAMES.get(code) or name or code


class MovementDistributionExtractor(Extractor):
    """One Bronze record per Indonesian district, day and distance band."""

    target = "records"

    def __init__(self, country: str = COUNTRY) -> None:
        self._country = country

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in CSV_SUFFIXES

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        extra = landed.extra or {}
        resource = str(extra.get("resource_id") or landed.path.stem)
        number = 0

        # `newline=""` and a text handle: `csv` does its own line splitting,
        # and a quoted district name containing a newline would otherwise be
        # cut in half.
        with landed.path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            reader = csv.DictReader(handle)
            missing = [
                column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])
            ]
            if missing:
                raise ExtractionError(
                    str(landed.path),
                    f"release is missing the columns {missing}; Meta has reshaped the file",
                )

            for row in reader:
                if (row.get("country") or "").strip().upper() != self._country:
                    continue
                day = (row.get("ds") or "").strip()
                category = (row.get("home_to_ping_distance_category") or "").strip()
                if not day or not category:
                    continue

                number += 1
                yield {
                    "dataset": landed.dataset or "movement-distribution",
                    "row_number": number,
                    "columns": {
                        "indicator": indicator_id(category),
                        "series_name": f"Share of movements {CATEGORIES.get(category, category)} "
                        "from home",
                        "category": category,
                        "category_label": CATEGORIES.get(category, category),
                        "date": day,
                        # The period the figure describes. A day here, and
                        # separate from `ds` so restating it later does not mean
                        # re-reading a hundred megabytes.
                        "period": day,
                        "value": (row.get("distance_category_ping_fraction") or "").strip(),
                        # A share of one district's movements, not a count.
                        "unit": "share of movements",
                        "gadm_id": (row.get("gadm_id") or "").strip(),
                        "gadm_name": (row.get("gadm_name") or "").strip(),
                        # The place a normalization run maps: the name as
                        # GADM writes it, which the registry resolves to a
                        # regency or a city. Four of these polygons are lakes
                        # and a reservoir, and one — `Gorontalo` — names both
                        # a province and a regency; those resolve to nothing,
                        # which is the honest outcome.
                        "district": _district(row),
                        "polygon_level": (row.get("polygon_level") or "").strip(),
                        "country": self._country,
                        "publisher": "Data for Good at Meta",
                        "resource_id": resource,
                        "resource_name": str(extra.get("resource_name") or ""),
                    },
                }

        if number == 0:
            # The release parsed and holds no Indonesian row. Meta has dropped
            # the country, or changed how it writes the code — either way the
            # silence is the thing worth surfacing.
            raise ExtractionError(
                str(landed.path),
                f"release holds no rows for country {self._country!r}",
            )
        log.info("hdx.mobility_extracted", resource=resource, rows=number)


__all__ = ["CATEGORIES", "MovementDistributionExtractor", "indicator_id"]
