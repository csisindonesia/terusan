"""Where an article says something happened.

The classifier picks between declared options and cannot read a place name out
of prose, so this does: the text is matched against the geography reference —
the same provinces and regencies every other dataset in the warehouse is keyed
by — and the most specific match wins.

Matching is on word boundaries over a normalised string, because `Kota Sorong`
appearing inside `Kabupaten Sorong Selatan` is the classic way to file an
incident in the wrong regency. Longer names are tried first for the same
reason.

An outlet's own province is used only as a tie-break, never as an answer. A
provincial paper reports on its neighbours, and a national paper reports on
everywhere; filing by masthead would put Jakarta's crime desk in Jakarta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache

from ..storage.root import project_root

GEOGRAPHY = project_root() / "reference" / "geography"

#: Provinces whose regency assignments in the reference are known to be wrong.
#:
#: `reference/geography/indonesia-regencies.csv` files all forty-two regencies
#: of the six Papua provinces under `ID-91` and `ID-92`. The four created in
#: 2022 — Papua Selatan, Papua Tengah, Papua Pegunungan, and the regencies that
#: moved to Papua Barat Daya — have no regencies assigned to them at all, and
#: the file's own header warns those codes were recorded from secondary
#: knowledge rather than from a BPS publication.
#:
#: So a regency in this set implies nothing about its province. Where the
#: report names one, that is used; where it does not, the province is left
#: unresolved rather than asserted. An unresolved province costs a row in the
#: provincial counts. Asserting the wrong one puts a killing in a province a
#: thousand kilometres away and is not recoverable by anybody reading the
#: figures, which is the worse of the two.
#:
#: Fixing the reference — assigning each regency to the province that actually
#: holds it, against BPS Kode dan Data Wilayah — retires this.
UNTRUSTED_PARENTS = frozenset({"ID-91", "ID-92"})

#: Words that precede a place name and are not part of it.
_PREFIXES = re.compile(r"^(kabupaten|kab|kota|kotamadya|provinsi|prov|kec|kecamatan)\s+")


@dataclass(frozen=True, slots=True)
class Place:
    """One resolved location."""

    geo_id: str
    name: str
    level: str
    parent_geo_id: str | None = None
    bps_code: str | None = None


def normalise(value: str) -> str:
    """Casefold, strip accents and collapse to single spaces."""
    text = unicodedata.normalize("NFKD", value.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _read(name: str) -> list[dict[str, str]]:
    import csv

    path = GEOGRAPHY / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        body = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(body))


@cache
def gazetteer() -> tuple[tuple[str, Place], ...]:
    """Every name that can be matched, longest first.

    Longest first so `sorong selatan` is tried before `sorong`: a scan that
    takes the first match would resolve the regency to its neighbour.
    """
    entries: list[tuple[str, Place]] = []
    for row in _read("indonesia-provinces.csv"):
        if row["geo_type"] != "province":
            continue
        place = Place(
            geo_id=row["geo_id"],
            name=row["name"],
            level="province",
            parent_geo_id=row["parent_geo_id"] or None,
            bps_code=row["bps_code"] or None,
        )
        for alias in (row["name"], *filter(None, row["aliases"].split("|"))):
            key = _PREFIXES.sub("", normalise(alias))
            # Kemendagri's `11.00` spellings are in the aliases and are not
            # names; a digit-only alias would match any article with a number.
            if len(key) > 3 and not key.replace(" ", "").isdigit():
                entries.append((key, place))
    for row in _read("indonesia-regencies.csv"):
        place = Place(
            geo_id=row["geo_id"],
            name=row["name"],
            level="regency",
            parent_geo_id=row.get("parent_geo_id") or None,
            bps_code=row.get("bps_code") or None,
        )
        for alias in (row["name"], *filter(None, (row.get("aliases") or "").split("|"))):
            key = _PREFIXES.sub("", normalise(alias))
            if len(key) > 3 and not key.replace(" ", "").isdigit():
                entries.append((key, place))
    entries.sort(key=lambda pair: (-len(pair[0]), pair[0]))
    return tuple(entries)


def resolve(text: str, *, hint_geo_id: str | None = None) -> tuple[Place | None, Place | None]:
    """Find the province and the regency an article is about.

    Returns both because the figures are counted per province while the event
    row records the regency, and an article naming only one of them is normal.
    A regency found without its province implies it.
    """
    haystack = f" {normalise(text)} "
    provinces: dict[str, Place] = {}
    regencies: dict[str, tuple[Place, int]] = {}

    # A matched name is consumed, so a shorter name sitting inside a longer one
    # cannot match the same words. `Papua Tengah` contains `Papua`, which is
    # also a province, and both matching made every report from the new Papua
    # provinces look like it named two — which is exactly the case where the
    # rule below stands down and takes the register's answer instead. The
    # gazetteer is longest-first, so the specific name always gets there first.
    remaining = haystack
    for key, place in gazetteer():
        needle = f" {key} "
        if needle not in remaining:
            continue
        count = remaining.count(needle)
        # A separator, not nothing: removing the words outright would join the
        # text either side and manufacture a name that was never written.
        remaining = remaining.replace(needle, " | ")
        if place.level == "province":
            provinces.setdefault(place.geo_id, place)
        else:
            existing = regencies.get(place.geo_id)
            regencies[place.geo_id] = (place, max(count, existing[1] if existing else 0))

    regency = None
    if regencies:
        # The regency named most often, and among equals the one whose province
        # was also named — a passing mention of another district should not
        # outrank the one the article is about.
        def rank(item: tuple[Place, int]) -> tuple[int, int, int]:
            place, count = item
            in_named = 1 if place.parent_geo_id in provinces else 0
            matches_hint = 1 if hint_geo_id and place.parent_geo_id == hint_geo_id else 0
            return (count, in_named, matches_hint)

        regency = max(regencies.values(), key=rank)[0]

    province: Place | None = None

    # What the article says beats what the register says it should be.
    #
    # A regency normally implies its province, and that is the usual path. But
    # the register can be wrong, and here it is: every regency of the six Papua
    # provinces is filed under Papua Barat or Papua Barat Daya, because the
    # four created in 2022 have no regencies assigned to them at all. Following
    # the parent puts Nabire, Mimika, Jayawijaya and Asmat in Papua Barat —
    # which is how a small province came to hold two-fifths of the country's
    # collective violence.
    #
    # So when a report names exactly one province and it is not the one the
    # register would have inferred, the report wins. One province only: an
    # article naming two is as likely to be mentioning a neighbour as
    # correcting us, and there the register is the better guess.
    named = list(provinces.values())
    if regency and regency.parent_geo_id:
        inferred = provinces.get(regency.parent_geo_id) or _province(regency.parent_geo_id)
        if len(named) == 1 and named[0].geo_id != regency.parent_geo_id:
            province = named[0]
        elif regency.parent_geo_id in UNTRUSTED_PARENTS and not named:
            # Known-wrong mapping and nothing in the report to correct it with.
            province = None
        else:
            province = inferred
    if province is None and named:
        province = named[0]
    return province, regency


@cache
def _province(geo_id: str) -> Place | None:
    for _, place in gazetteer():
        if place.geo_id == geo_id and place.level == "province":
            return place
    return None
