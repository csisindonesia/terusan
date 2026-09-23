"""Mining: the company register, the one-data portal, and volcano reporting.

Three ESDM systems the source sheet asks for, and none of them serves an
unattended client today. They are registered together because they share a
ministry and a reason: each is a live system rather than a published file, and
each has put something between itself and a script.

**MODI** holds the mining business permits — who holds a concession, where, for
what mineral, and whether it is in good standing. It is the register behind
every question about Indonesian nickel. Its pages are rendered by a Vue app
against `quick_search.php`, and the host's WAF answers a script with "Sistem
Kami mengindikasikan adanya aktivitas mencurigakan".

**Minerba One Data** carries the benchmark prices — HBA for coal, HMA for
metals — that royalties are assessed on. The site answers "Sistem Sedang Dalam
Perbaikan": it is down, not blocked, which is a thing that ends.

**MAGMA** is the volcano observatory: activity levels, eruption reports and
hazard bulletins, updated through the day. Its public pages render for a
reader; its API answers `Token not provided`, and tokens are issued by PVMBG on
request.

Each says what would open it. None pretends to collect.
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


class Modi(GatedSource):
    """The mining permit register."""

    meta = SourceMeta(
        slug="esdm-modi",
        name="MODI — Minerba One Data Indonesia (perizinan)",
        organization="Kementerian ESDM — Direktorat Jenderal Minerba",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://modi.esdm.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "Mining business permits (IUP/IUPK): holder, commodity, area, status. "
            "The register behind nickel and coal ownership questions."
        ),
    )

    access = (
        "modi.esdm.go.id sits behind a WAF that rejects scripted requests; either "
        "request an access arrangement from Ditjen Minerba, or collect through a "
        "supervised browser session and land the exports here"
    )


class MinerbaPrices(GatedSource):
    """The benchmark coal and mineral prices."""

    meta = SourceMeta(
        slug="esdm-minerba-prices",
        name="Minerba — Harga Batubara Acuan dan Harga Mineral Acuan",
        organization="Kementerian ESDM — Direktorat Jenderal Minerba",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://www.minerba.esdm.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.MONTHLY,
        active=False,
        schedule=None,
        notes=(
            "HBA and HMA, the monthly benchmark prices royalties are assessed on. "
            "Thermal coal's market price already arrives through yahoo-thermal-coal; "
            "this is the administered price, which is a different figure."
        ),
    )

    access = (
        "minerba.esdm.go.id has served 'Sistem Sedang Dalam Perbaikan' since this "
        "source was written; re-check the portal and wire the monthly decree pages "
        "in here when it returns"
    )


class Magma(GatedSource):
    """Volcano activity levels and eruption reporting."""

    meta = SourceMeta(
        slug="esdm-magma",
        name="MAGMA Indonesia — aktivitas gunung api",
        organization="Kementerian ESDM — PVMBG",
        category=Category.GOVERNMENT,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://magma.esdm.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.REALTIME,
        active=False,
        schedule=None,
        notes=(
            "Activity level per volcano, eruption reports and hazard bulletins. "
            "BNPB's disaster figures count what eruptions did; this is the "
            "observatory's own record of the eruptions."
        ),
    )

    access = (
        "magma.esdm.go.id/api/v1 answers 'Token not provided'; request an API "
        "token from PVMBG and set it in .env, then this becomes an ApiSource over "
        "the magma-var and laporan endpoints"
    )
