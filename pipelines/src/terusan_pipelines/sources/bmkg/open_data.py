"""BMKG's open feeds: the earthquakes, as the agency publishes them.

Two halves of BMKG are public and they are not equally reachable.

The earthquake feeds under `data.bmkg.go.id/DataMKG/TEWS/` answer anybody with
JSON and are the same files the agency's own site reads. They are landed here.

The weather forecast API at `api.bmkg.go.id/publik/prakiraan-cuaca` also answers
anybody, but only per village: `adm4=31.71.03.1001` returns Kemayoran and
`adm1=31` returns a landing page, so collecting the country means holding
83,000 village codes first. Those codes are Kemendagri's, and the source that
lands them is `kemendagri-wilayah`. The forecast belongs in its own source
driven off that list, on the day something downstream reads weather — which is
a different collection, not a bigger version of this one.

`dataonline.bmkg.go.id`, the station archive of daily rainfall and temperature,
is a third thing again: it needs an account and approves requests by hand. It
is registered as `bmkg-dataonline` and collects nothing until someone holds
that account.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import ApiSource, Endpoint, GatedSource

TEWS = "https://data.bmkg.go.id/DataMKG/TEWS"


class Earthquakes(ApiSource):
    """The three earthquake feeds, landed as served."""

    meta = SourceMeta(
        slug="bmkg-earthquakes",
        name="BMKG — Gempabumi terkini dan dirasakan",
        organization="Badan Meteorologi, Klimatologi, dan Geofisika",
        category=Category.GOVERNMENT,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://data.bmkg.go.id",
        license="BMKG open data — attribution required",
        update_frequency=UpdateFrequency.REALTIME,
        max_requests_per_second=1.0,
        # Hourly. The feeds hold the last fifteen events, so a daily run over a
        # busy week would land a window that had already scrolled past the
        # events it missed.
        schedule="7 * * * *",
        notes=(
            "Three JSON feeds: the latest event, the last fifteen at M5.0 and "
            "above, and the last fifteen that were felt. Landing is "
            "content-addressed, so an hour with no new quake writes nothing."
        ),
    )

    endpoints = (
        Endpoint(
            dataset="earthquake-latest",
            url=f"{TEWS}/autogempa.json",
            filename="autogempa.json",
            metadata={
                "feed": "autogempa",
                "coverage": "the most recent event of any magnitude, with its shakemap",
            },
        ),
        Endpoint(
            dataset="earthquakes-recent",
            url=f"{TEWS}/gempaterkini.json",
            filename="gempaterkini.json",
            metadata={"feed": "gempaterkini", "coverage": "last fifteen events at M5.0+"},
        ),
        Endpoint(
            dataset="earthquakes-felt",
            url=f"{TEWS}/gempadirasakan.json",
            filename="gempadirasakan.json",
            metadata={
                "feed": "gempadirasakan",
                "coverage": "last fifteen events reported felt, with MMI by place",
            },
        ),
    )


class DataOnline(GatedSource):
    """BMKG's station archive, which is not open."""

    meta = SourceMeta(
        slug="bmkg-dataonline",
        name="BMKG — Data Online (arsip stasiun)",
        organization="Badan Meteorologi, Klimatologi, dan Geofisika",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://dataonline.bmkg.go.id/",
        license="BMKG terms of use; redistribution restricted",
        update_frequency=UpdateFrequency.DAILY,
        active=False,
        schedule=None,
        notes=(
            "Daily station observations — rainfall, temperature, humidity, wind. "
            "Registered so the catalogue records that the lake knows about this "
            "archive and does not hold it."
        ),
    )

    access = (
        "dataonline.bmkg.go.id issues data per registered account and approves "
        "each request by hand; open an account, then land the approved exports "
        "through this source"
    )
