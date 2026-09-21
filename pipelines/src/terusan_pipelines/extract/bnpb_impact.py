"""BNPB's impact tables, and the series inside them.

Each of these tables is one measure — deaths, people displaced, houses damaged
— with a row per province and a column per hazard: `BANJIR`, `GEMPABUMI`,
`TANAH LONGSOR`. The measure is named nowhere in the file. It is in the
resource's title, and the year is in neither: it is the CKAN dataset the table
was published under, which is why the landing partition carries it.

Read as cells, that is a Bronze row per province holding nine numbers that mean
nine different things. So a recognised table is read long instead: one record
per province and hazard, naming the series it belongs to. Composing that name
belongs here, where BNPB's own title is still at hand — by the time Silver sees
the rows there is nothing left to read it from (`normalize-each --by`).

Two things are deliberately not done.

**The `INDONESIA` row is dropped.** It is the sum of the rows above it, and
keeping it would double every national total taken over the series — the same
reason the World Bank extractor drops "World" and "Euro area".

**Hazards are not totalled.** BNPB publishes no all-hazard column and this does
not invent one: a sum across the nine is a derivation, and derivations belong in
Silver where they can be seen (program.md §6).

Regency tables are left in their wide form for now. They are the same shape at a
finer grain, and mixing both into one series would double-count the moment
anyone summed it — so they wait for a series of their own.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

#: Where the long rows go. One Bronze dataset across every year and every CKAN
#: package: the tables differ by what they count and when, not by shape, and a
#: series that changed dataset each year would be twenty series.
IMPACT_DATASET = "bnpb-impact"

#: The columns that say which province a row is about. BNPB's code is the BPS
#: code the geography registry is keyed by, so it resolves exactly rather than
#: by name — which matters in a country with a Kabupaten and a Kota Banjar.
PROVINCE_CODE = "Kode Wilayah Provinsi"
PROVINCE_NAME = "Provinsi"

#: Columns that are not hazards. `No.` is a line number and the coordinates
#: describe the province rather than the disaster.
NON_HAZARD = frozenset(
    {
        "No.",
        "No",
        PROVINCE_CODE,
        PROVINCE_NAME,
        "Latitude",
        "Longitude",
        "resource",
        "resource_id",
        "package_title",
    }
)

#: The national row, which is the sum of the others.
NATIONAL = "INDONESIA"

#: BNPB's numbering for the six Papua provinces, corrected to BPS's.
#:
#: Papua was split into six provinces in 2022, and BNPB numbers them in an
#: order of its own: its `94` is Papua Tengah, where the BPS code 94 is Papua
#: itself. Every one of the six disagrees, so a code join does not fail — it
#: quietly files each province's dead under its neighbour, which is the worst
#: way for a mapping to be wrong.
#:
#: Keyed by the name with its spaces removed, because one table spells the
#: province `P A P U A`, and applied only where the name is one of these six:
#: BNPB adopting the standard codes later would leave the names matching the
#: BPS numbering, and this must not then move them back.
PAPUA_BPS: dict[str, str] = {
    "PAPUA": "94",
    "PAPUABARAT": "91",
    "PAPUASELATAN": "95",
    "PAPUATENGAH": "96",
    "PAPUAPEGUNUNGAN": "97",
    "PAPUABARATDAYA": "92",
}


@dataclass(frozen=True, slots=True)
class Measure:
    """What one table counts."""

    key: str
    title: str
    unit: str


#: What BNPB's titles mean, longest phrase first. The rate tables read as the
#: count tables plus `per 100.000 Orang`, so they have to be recognised before
#: the counts they are derived from — otherwise every rate lands in the count
#: series and a hundred thousand people become a hundred thousand deaths.
MEASURES: tuple[tuple[str, Measure], ...] = (
    (
        "korban meninggal dan hilang per 100.000",
        Measure("deaths_missing_rate", "Deaths and missing per 100,000 people", "per 100,000"),
    ),
    (
        "korban mengungsi per 100.000",
        Measure("displaced_rate", "People displaced per 100,000 people", "per 100,000"),
    ),
    ("kejadian bencana", Measure("events", "Disaster events", "events")),
    ("korban meninggal", Measure("deaths", "Deaths from disasters", "people")),
    ("korban hilang", Measure("missing", "People missing after disasters", "people")),
    ("korban luka", Measure("injured", "People injured or taken ill in disasters", "people")),
    ("korban terdampak", Measure("affected", "People affected by disasters", "people")),
    ("korban mengungsi", Measure("displaced", "People displaced by disasters", "people")),
    ("rumah rusak", Measure("houses_damaged", "Houses damaged by disasters", "houses")),
    (
        "satuan pendidikan rusak",
        Measure("schools_damaged", "Education facilities damaged by disasters", "facilities"),
    ),
    (
        "rumah ibadat",
        Measure("worship_damaged", "Places of worship damaged by disasters", "buildings"),
    ),
    (
        "fasilitas pelayanan kesehatan",
        Measure("health_damaged", "Health facilities damaged by disasters", "facilities"),
    ),
    ("kantor", Measure("offices_damaged", "Government offices damaged by disasters", "buildings")),
    ("jembatan", Measure("bridges_damaged", "Bridges damaged by disasters", "bridges")),
    ("pabrik", Measure("factories_damaged", "Factories damaged by disasters", "buildings")),
)

#: BNPB's hazard headings, and what they are called in English. This is the
#: vocabulary, not a convenience: a heading that is not here is not read as a
#: hazard, because the same organization publishes province tables whose
#: columns are years and tables carrying a population column, and guessing that
#: a column is a hazard because it sits where hazards sit turned `2014` into a
#: series. An unrecognised column on a hazard table is logged instead, so a
#: hazard BNPB adds shows up as a line to add here rather than as silence.
HAZARDS: dict[str, tuple[str, str]] = {
    "BANJIR": ("FLOOD", "floods"),
    "CUACA EKSTREM": ("EXTREME_WEATHER", "extreme weather"),
    "ERUPSI GUNUNG API": ("VOLCANIC_ERUPTION", "volcanic eruptions"),
    "GELOMBANG PASANG DAN ABRASI": ("TIDAL_WAVE", "tidal waves and abrasion"),
    "GEMPABUMI": ("EARTHQUAKE", "earthquakes"),
    "KEBAKARAN HUTAN DAN LAHAN": ("WILDFIRE", "forest and land fires"),
    "KEKERINGAN": ("DROUGHT", "droughts"),
    "TANAH LONGSOR": ("LANDSLIDE", "landslides"),
    "TSUNAMI": ("TSUNAMI", "tsunamis"),
}

#: How many known hazards a table must carry to be read as one. Five rather
#: than one: a single coincidental heading should not turn a table of years
#: into a table of hazards, and every real one here carries all nine.
MIN_KNOWN_HAZARDS = 5

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def bps_code(code: str, name: str) -> str:
    """The BPS province code for a row, which is not always the one printed."""
    return PAPUA_BPS.get("".join(name.split()).upper(), code)


def measure_for(resource: str) -> Measure | None:
    """What a table counts, read off the title BNPB gave it."""
    title = resource.lower()
    for phrase, measure in MEASURES:
        if phrase in title:
            return measure
    return None


def is_province_table(columns: dict[str, Any]) -> bool:
    """Whether a row came from a table of provinces by hazard.

    Both halves are required. The code column alone appears in tables that are
    not this shape — one lists a province's disaster count per year from 2010,
    whose columns are years — and hazard columns alone appear in the national
    recapitulation, which has no geography at all.
    """
    return PROVINCE_CODE in columns and len(hazards_in(columns)) >= MIN_KNOWN_HAZARDS


def hazards_in(columns: dict[str, Any]) -> list[str]:
    """The hazard columns of a row, in the order the table published them."""
    return [key for key in columns if key.strip().upper() in HAZARDS]


def unrecognised(columns: dict[str, Any]) -> list[str]:
    """Columns that sit where a hazard would without being one.

    Reported rather than dropped quietly: a heading this does not know is
    either a hazard BNPB has started reporting — which wants a line in
    `HAZARDS` — or something else entirely, as `Jumlah Penduduk` is on the rate
    tables, where the population is the denominator and not a disaster.
    """
    return [
        key
        for key in columns
        if key not in NON_HAZARD
        and not key.startswith("label:")
        and key.strip().upper() not in HAZARDS
    ]


def period_for(partition: tuple[str, ...], *fallbacks: str) -> str | None:
    """The year these figures describe.

    The landing partition first — it is the CKAN dataset's own year, and it is
    the only place the year is stated for a table whose title omits it.
    """
    for segment in partition:
        match = _YEAR.search(segment)
        if match:
            return match.group(1)
    for text in fallbacks:
        match = _YEAR.search(text or "")
        if match:
            return match.group(1)
    return None


def records(
    columns: dict[str, str],
    *,
    resource: str,
    period: str,
    row_number: int,
) -> Iterator[dict[str, Any]]:
    """One Bronze record per hazard in one province's row."""
    measure = measure_for(resource)
    if measure is None:
        return

    hazards = hazards_in(columns)
    code = (columns.get(PROVINCE_CODE) or "").strip()
    name = (columns.get(PROVINCE_NAME) or "").strip()
    if not code or name.upper() == NATIONAL:
        # The national row carries no code and is the sum of the rest.
        return

    for offset, column in enumerate(hazards):
        key, hazard = HAZARDS[column.strip().upper()]
        value = (columns.get(column) or "").strip()
        if not value:
            continue

        yield {
            "dataset": IMPACT_DATASET,
            # Unique across the table: a province's nine hazards would
            # otherwise share one number and overwrite each other.
            "row_number": row_number * 100 + offset,
            "columns": {
                "indicator": f"BNPB_{measure.key.upper()}_{key}",
                "name": f"{measure.title}: {hazard}",
                "measure": measure.key,
                "hazard": key,
                "hazard_name": column.strip(),
                "period": period,
                "value": value,
                "unit": measure.unit,
                # The BPS code, which the geography registry is keyed by, and
                # which for the Papua provinces is not the code BNPB printed.
                "province_code": bps_code(code, name),
                "province": " ".join(name.split()),
                "province_code_published": code,
                "level": "province",
                "resource": resource,
                "publisher": "Badan Nasional Penanggulangan Bencana",
            },
        }
