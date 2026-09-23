"""DPR RI — bills, sittings and the record of who did what.

The legislature publishes its programme of bills, the stage each has reached,
the committee minutes behind them and the attendance of members. It is the only
account of what happened between a draft and a law, which is what makes it
worth holding beside the laws themselves in `bpk-peraturan-pusat`.

`www.dpr.go.id` answers this network with a bare `Access Denied` from an
appliance in front of it — not a rate limit and not a challenge, but a block,
which a scheduled run cannot negotiate its way around. The same material is
also filed in JDIH DPR, a member of the JDIHN network already indexed by
`jdihn-documents`, which is the route to try first when this is picked up
again.
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


class Legislation(GatedSource):
    """The legislative record."""

    meta = SourceMeta(
        slug="dpr-legislation",
        name="DPR RI — Program legislasi dan dokumen persidangan",
        organization="Dewan Perwakilan Rakyat Republik Indonesia",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://www.dpr.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "Bill programme, committee documents and sitting records. JDIH DPR "
            "carries much of the same material and is indexed by jdihn-documents."
        ),
    )

    access = (
        "dpr.go.id returns 'Access Denied' to this network at the edge; collect "
        "through JDIH DPR via the JDIHN index, or ask the secretariat for access"
    )
