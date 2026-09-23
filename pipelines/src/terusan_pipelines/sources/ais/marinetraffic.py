"""AIS vessel tracking — the one holding here that costs money.

Every ship broadcasts its identity, position and draught over AIS, and the
receivers that collect it are commercial. For a country whose exports leave by
sea, that record is what turns a customs figure into an observable event: a
bulk carrier loading nickel ore at Morowali is visible days before the shipment
appears in anyone's statistics.

MarineTraffic and its competitors sell that feed. There is no free tier worth
ingesting and no public archive; the terms forbid redistribution, which also
shapes what could ever be published from it.

Registered so the catalogue states the gap plainly. Anything in this lake that
claims to measure shipping without it — port statistics from Kemenhub, export
volumes from Kemendag — is measuring the paperwork, and that is a distinction
worth having written down somewhere.
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


class MarineTraffic(GatedSource):
    """Commercial AIS."""

    meta = SourceMeta(
        slug="marinetraffic-ais",
        name="MarineTraffic / AIS vessel tracking",
        organization="MarineTraffic (Kpler) and comparable AIS providers",
        category=Category.RESEARCH,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.API,
        base_url="https://www.marinetraffic.com/",
        country=None,
        license="Commercial subscription; redistribution prohibited",
        update_frequency=UpdateFrequency.REALTIME,
        active=False,
        schedule=None,
        notes=(
            "Vessel positions, port calls and voyages. The observable side of "
            "seaborne trade, against which the customs figures can be checked."
        ),
    )

    access = (
        "AIS is sold, not published: buy a subscription from MarineTraffic or "
        "another provider, and note that their terms restrict what may be "
        "redistributed from anything landed here"
    )
