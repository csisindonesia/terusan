"""Resolving source names to stable dimension identifiers.

Sources name the same thing a dozen ways: `Jawa Barat`, `JAWA BARAT`, `Jabar`,
`West Java`, `Prov. Jawa Barat`. Silver stores one identifier so figures from
different sources can be compared at all (program.md §11, §12).

Resolution is deliberately conservative. An unresolved name is recorded as
unresolved and kept; it is not guessed at. A figure filed under the wrong
province is worse than one filed under none, because the second is visible and
the first is not.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

#: Words that carry no distinguishing information in Indonesian place names.
#: Stripped before matching so `Prov. Jawa Barat` and `Jawa Barat` agree.
_GEO_NOISE = {
    "prov",
    "provinsi",
    "kab",
    "kabupaten",
    "kota",
    "kec",
    "kecamatan",
    "daerah",
    "khusus",
    "ibukota",
}

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


class GeoType(StrEnum):
    """Levels of the Indonesian administrative hierarchy (program.md §11)."""

    COUNTRY = "country"
    PROVINCE = "province"
    REGENCY = "regency"
    DISTRICT = "district"
    VILLAGE = "village"
    REGION = "region"


class EntityType(StrEnum):
    """Program.md §15."""

    PERSON = "person"
    ORGANIZATION = "organization"
    COMPANY = "company"
    GOVERNMENT_AGENCY = "government_agency"
    LOCATION = "location"
    COMMODITY = "commodity"
    INDUSTRY = "industry"
    REGULATION = "regulation"
    EVENT = "event"
    TOPIC = "topic"


def normalize_name(raw: str, *, drop: set[str] | None = None) -> str:
    """Reduce a name to a matchable form.

    Accents folded, punctuation dropped, case flattened, noise words removed.
    Only ever used for lookup — the original name is what gets stored.
    """
    text = unicodedata.normalize("NFKD", raw or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = _PUNCTUATION.sub(" ", text)
    words = [w for w in _WHITESPACE.split(text) if w and w not in (drop or set())]
    return " ".join(words)


@dataclass(frozen=True, slots=True)
class Geography:
    """One administrative area (program.md §11)."""

    geo_id: str
    name: str
    geo_type: GeoType
    parent_geo_id: str | None = None
    country_code: str = "ID"
    province_code: str | None = None
    regency_code: str | None = None
    bps_code: str | None = None
    iso_code: str | None = None

    #: Administrative areas are created, split and merged. A code reused for a
    #: new area would otherwise silently absorb the old one's history
    #: (program.md §11).
    valid_from: date | None = None
    valid_to: date | None = None

    aliases: tuple[str, ...] = ()

    def covers(self, day: date) -> bool:
        if self.valid_from and day < self.valid_from:
            return False
        return not (self.valid_to and day > self.valid_to)


@dataclass(frozen=True, slots=True)
class Commodity:
    """One traded good (program.md §12)."""

    commodity_id: str
    canonical_name: str
    category: str | None = None
    subcategory: str | None = None
    hs_code: str | None = None
    hs_version: str | None = None
    unit_default: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of looking a name up."""

    identifier: str | None
    matched_on: str | None = None
    #: How the match was made, so a downstream reader can decide what to trust.
    method: str = "none"

    @property
    def resolved(self) -> bool:
        return self.identifier is not None


class Registry[T]:
    """Name-to-identifier lookup over a set of dimension members."""

    def __init__(self, noise: set[str] | None = None) -> None:
        self._members: dict[str, T] = {}
        self._index: dict[str, str] = {}
        self._noise = noise or set()

    def add(self, identifier: str, member: T, names: list[str]) -> None:
        """Register a member under every name it is known by."""
        self._members[identifier] = member
        for name in names:
            for key in self._keys(name):
                existing = self._index.get(key)
                if existing is not None and existing != identifier:
                    # Two members answering to one name cannot both be right,
                    # and picking one silently would misfile every figure using
                    # it. Neither keeps the name.
                    self._index[key] = ""
                    continue
                self._index[key] = identifier

    def resolve(self, name: str) -> Resolution:
        """Look a name up, reporting how it matched."""
        if not name or not name.strip():
            return Resolution(None)

        exact = normalize_name(name)
        if identifier := self._index.get(exact):
            return Resolution(identifier, matched_on=name, method="exact")

        reduced = normalize_name(name, drop=self._noise)
        if reduced != exact and (identifier := self._index.get(reduced)):
            return Resolution(identifier, matched_on=name, method="normalized")

        return Resolution(None, matched_on=name)

    def get(self, identifier: str) -> T | None:
        return self._members.get(identifier)

    def _keys(self, name: str) -> set[str]:
        keys = {normalize_name(name)}
        if self._noise:
            keys.add(normalize_name(name, drop=self._noise))
        return {k for k in keys if k}

    def __len__(self) -> int:
        return len(self._members)

    def __contains__(self, identifier: str) -> bool:
        return identifier in self._members


@dataclass
class GeographyRegistry:
    """Geographies, resolvable by name and by code."""

    registry: Registry[Geography] = field(
        default_factory=lambda: Registry[Geography](noise=_GEO_NOISE)
    )

    def add(self, geography: Geography) -> None:
        names = [geography.name, *geography.aliases]
        codes = [c for c in (geography.bps_code, geography.iso_code) if c]
        self.registry.add(geography.geo_id, geography, [*names, *codes])

    def resolve(self, name: str, *, on: date | None = None) -> Resolution:
        """Resolve a place name, optionally as of a date.

        `on` matters where an area has been split or merged: resolving a 2010
        figure against today's boundaries would file it under an area that did
        not exist (program.md §11).
        """
        result = self.registry.resolve(name)
        if not result.resolved or on is None:
            return result

        geography = self.registry.get(result.identifier or "")
        if geography is not None and not geography.covers(on):
            return Resolution(None, matched_on=name, method="out_of_period")
        return result

    def get(self, geo_id: str) -> Geography | None:
        return self.registry.get(geo_id)

    def __len__(self) -> int:
        return len(self.registry)


@dataclass
class CommodityRegistry:
    """Commodities, resolvable by name, alias and HS code."""

    registry: Registry[Commodity] = field(default_factory=Registry[Commodity])

    def add(self, commodity: Commodity) -> None:
        names = [commodity.canonical_name, *commodity.aliases]
        if commodity.hs_code:
            names.append(commodity.hs_code)
        self.registry.add(commodity.commodity_id, commodity, names)

    def resolve(self, name: str) -> Resolution:
        return self.registry.resolve(name)

    def get(self, commodity_id: str) -> Commodity | None:
        return self.registry.get(commodity_id)

    def __len__(self) -> int:
        return len(self.registry)
