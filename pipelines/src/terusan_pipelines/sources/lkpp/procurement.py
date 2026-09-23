"""Public procurement: what every agency plans to buy, and what it bought.

SiRUP is the plan — every procurement package each ministry, agency and region
intends to run this year, with its value, its method and its budget line. It is
the earliest public signal of government spending, months before a contract
exists, and the source sheet puts it in the critical tier for exactly that.
INAPROC is the other end: the national procurement portal, where the tenders
and their winners appear.

Neither can be collected today, and the reason is worth recording because it
changed recently. LKPP used to publish both through ISB, an open dataset feed
at `isb.lkpp.go.id`. That service now serves a closure notice directing callers
to an API gateway at Inaproc, `sirup.lkpp.go.id` no longer resolves from
outside, and `inaproc.id` answers a script with a challenge page. The data did
not stop being public; the route to it moved and now goes through a gateway
that issues credentials per consumer.

Both are registered so that the day a gateway credential exists, the slugs and
the datasets the warehouse expects are already here.
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


class Sirup(GatedSource):
    """The procurement plan."""

    meta = SourceMeta(
        slug="lkpp-sirup",
        name="SiRUP — Rencana Umum Pengadaan",
        organization="Lembaga Kebijakan Pengadaan Barang/Jasa Pemerintah",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.API,
        base_url="https://sirup.lkpp.go.id/",
        license="Public sector information",
        # Agencies publish their plan at the start of the fiscal year and
        # revise it through it.
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "Planned procurement packages per agency and region: value, method, "
            "budget source. The earliest public signal of government spending."
        ),
    )

    access = (
        "the ISB open-data feed that served this closed; requests now go through "
        "LKPP's API gateway at Inaproc, which issues a credential per consumer — "
        "request one, then this becomes an ApiSource over the RUP endpoints"
    )


class Inaproc(GatedSource):
    """The national procurement portal."""

    meta = SourceMeta(
        slug="lkpp-inaproc",
        name="INAPROC — Portal Pengadaan Nasional",
        organization="Lembaga Kebijakan Pengadaan Barang/Jasa Pemerintah",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.API,
        base_url="https://inaproc.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.DAILY,
        active=False,
        schedule=None,
        notes=(
            "Tenders, their winners and the catalogue of framework suppliers — "
            "the realised end of what SiRUP plans."
        ),
    )

    access = (
        "inaproc.id answers an unattended client with a challenge page; collect "
        "through the same LKPP API gateway credential as lkpp-sirup"
    )
