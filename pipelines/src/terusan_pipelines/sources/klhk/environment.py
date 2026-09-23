"""Environment and forestry, and what the 2024 split did to both.

The source sheet points at `menlhk.go.id` for environmental data and
`geoportal.menlhk.go.id` for the forest maps. Neither is where they live any
more. KLHK was split into two ministries, and `menlhk.go.id` now serves a
landing page announcing it: the environment half is `kemenlh.go.id`, which is a
working site, and the forestry half's hosts — including the geoportal — do not
resolve from outside at all.

So this collects what exists. `klhk-environment` lands the environment
ministry's publication section, which is where the state-of-the-environment
reports and the emission inventories are filed. `klhk-geoportal` holds the
slug for the forest cover and concession layers and says what happened to them,
because a forest question that silently returns nothing is worse than one that
says the map moved.

Forest cover change is meanwhile readable from `gfw-catalogue`'s datasets,
which are derived from satellite rather than from the ministry — an independent
measure, not a substitute for the official one.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import GatedSource, PageSource

SITE = "https://kemenlh.go.id"


class Environment(PageSource):
    """The environment ministry's publications."""

    meta = SourceMeta(
        slug="klhk-environment",
        name="Kementerian Lingkungan Hidup — publikasi dan data",
        organization="Kementerian Lingkungan Hidup",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        schedule="0 6 8 * *",
        notes=(
            "kemenlh.go.id is where the environment half of the former KLHK "
            "publishes since the 2024 split; menlhk.go.id now serves only a "
            "notice about it."
        ),
    )

    dataset = "klhk-publications"

    urls = (f"{SITE}/", f"{SITE}/publikasi")


class Geoportal(GatedSource):
    """The forestry geoportal, which moved with the split."""

    meta = SourceMeta(
        slug="klhk-geoportal",
        name="Geoportal KLHK — tutupan lahan dan kawasan hutan",
        organization="Kementerian Kehutanan",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url="https://geoportal.menlhk.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.ANNUAL,
        active=False,
        schedule=None,
        notes=(
            "Land cover, forest area status, concessions and deforestation "
            "layers. Satellite-derived forest change is meanwhile catalogued "
            "through gfw-catalogue."
        ),
    )

    access = (
        "geoportal.menlhk.go.id stopped resolving after the ministry was split in "
        "2024; find the forestry ministry's successor geoportal and point this "
        "source at it"
    )
