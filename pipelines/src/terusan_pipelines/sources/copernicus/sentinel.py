"""Sentinel — the imagery everything spatial is eventually checked against.

Sentinel-1 radar sees through cloud, which in Indonesia is the whole argument:
optical imagery of Sumatra in the wet season is a picture of cloud. Sentinel-2
gives the ten-metre optical record. Between them they are how a mining
concession, a burn scar, a flood extent or a new plantation is verified against
something other than a ministry's own account of it.

The Copernicus Data Space Ecosystem serves both free, to registered accounts:
every download exchanges an account for an OAuth token, and the catalogue is
queried through STAC. Registration is free and takes minutes; nobody has done
it, so this source holds the slug and says so.

Scale is the second reason it is gated rather than half-wired. A single
Sentinel-2 tile is hundreds of megabytes and Indonesia is some three hundred
tiles per pass. What this source collects when it opens is a defined product
for a defined question, not an archive mirror — which is a decision to take
with the question in hand.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import GatedSource


class Sentinel(GatedSource):
    """Sentinel-1 and Sentinel-2 over Indonesia."""

    meta = SourceMeta(
        slug="copernicus-sentinel",
        name="Copernicus Data Space — Sentinel-1 and Sentinel-2",
        organization="European Space Agency / European Commission",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.API,
        base_url="https://dataspace.copernicus.eu/",
        country=None,
        license="Free and open — Copernicus data policy",
        update_frequency=UpdateFrequency.DAILY,
        active=False,
        schedule=None,
        notes=(
            "Radar and optical imagery, free to registered accounts through a "
            "STAC catalogue and an OAuth token. Held back by account and by "
            "scale, not by licence."
        ),
    )

    access = (
        "register free at dataspace.copernicus.eu, then query the STAC catalogue "
        "and exchange the account for an OAuth token per download; decide the "
        "product and the area first, because a mirror is not the goal"
    )
