"""PODES — the village census, which is not downloadable.

Village Potential Statistics is the only source that describes all 83,000
Indonesian villages at once: what facilities each has, what it is reachable by,
what it lives on, and what has happened to it. It is the spine of any village
panel, which is why the source sheet puts it in the critical tier and the
roadmap builds a Village Observatory on it.

It also cannot be collected. BPS publishes PODES as printed publications and as
microdata through SILASTIK, which issues files per approved request to a
registered account — and `bps.go.id` itself answers an unattended client with a
Cloudflare challenge, so even the publication PDFs are out of reach of a
scheduled run.

Registered here so the catalogue records the gap rather than hiding it, and so
that the day an account exists, the files land under a slug the warehouse
already knows.
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


class Podes(GatedSource):
    """The village census, awaiting a SILASTIK account."""

    meta = SourceMeta(
        slug="bps-podes",
        name="BPS — Pendataan Potensi Desa (PODES)",
        organization="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://www.bps.go.id/",
        license="BPS microdata terms — per-request approval",
        # Roughly every three years, on the census cycle.
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "Village-level census of facilities, infrastructure, economy and "
            "disasters. Held back by access, not by parsing: see "
            "silastik.bps.go.id."
        ),
    )

    access = (
        "PODES microdata is released per approved request to a SILASTIK account "
        "(silastik.bps.go.id); register, request the rounds wanted, and land the "
        "approved files through this source"
    )
