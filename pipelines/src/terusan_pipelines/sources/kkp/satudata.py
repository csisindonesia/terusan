"""Satu Data KKP — capture and aquaculture production, and the fleet.

Fisheries is the sector where Indonesia's figures matter most internationally
and are hardest to source: capture production by species and fishing ground,
aquaculture output, the registered fleet, and the export volumes that follow.
The ministry publishes them through its own Satu Data portal.

`satudata.kkp.go.id`, which the source sheet names, does not resolve — not from
this network and not from a public resolver, which is the difference between a
site that blocks us and one that is no longer there. The ministry's statistics
have moved between hosts more than once, so this is registered with the slug
and the note, and pointed at the successor host the day someone finds it.

Marine traffic around those fisheries is the `marinetraffic-ais` source, which
is gated for its own reasons; between them, the maritime picture is the thinnest
part of this lake and worth saying so.
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


class FisheriesData(GatedSource):
    """The fisheries data portal."""

    meta = SourceMeta(
        slug="kkp-satudata",
        name="Satu Data Kelautan dan Perikanan",
        organization="Kementerian Kelautan dan Perikanan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://satudata.kkp.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=("Capture and aquaculture production, the registered fleet, and fisheries exports."),
    )

    access = (
        "satudata.kkp.go.id does not resolve from any public resolver; find the "
        "ministry's current statistics host and point this source at it"
    )
