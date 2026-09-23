"""BPK JDIH — central legislation, the half the imported corpus does not hold.

`bpk-peraturan-daerah` holds a quarter of a million regional regulations,
imported as a finished corpus. What it does not hold is the law those regional
regulations are made under: the acts, the government regulations, the
presidential regulations and the ministerial ones. They come from the same
portal and are a much smaller body — thousands rather than hundreds of
thousands — which is why they can be collected here directly.

The search listing is what lands, one page at a time per regulation type. Each
entry links a `/Details/{id}/...` page carrying the document and its status —
in force, amended, repealed — and that status is the part a citation is wrong
without. Reading it is an extractor's job against these pages; landing them is
this source's.

The type codes are BPK's own, read off the search form's own select.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..base import (
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import PageSource

SEARCH = "https://peraturan.bpk.go.id/Search"

#: The central instruments, by BPK's `jenis` code. Regional types are left out:
#: they are what the imported corpus already holds.
TYPES = {
    8: "undang-undang",
    9: "perpu",
    10: "peraturan-pemerintah",
    11: "peraturan-presiden",
    12: "keputusan-presiden",
    13: "instruksi-presiden",
    15: "peraturan-menteri",
}

#: Listing pages per type per run, newest first. Ministerial regulations run to
#: tens of thousands; the rest are a few pages each.
DEFAULT_PAGES = 5


class PeraturanPusat(PageSource):
    """Central regulation listings, by type."""

    meta = SourceMeta(
        slug="bpk-peraturan-pusat",
        name="BPK JDIH — Peraturan perundang-undangan pusat",
        organization="Badan Pemeriksa Keuangan",
        category=Category.REGULATIONS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SEARCH,
        license="Public domain — Indonesian law is not subject to copyright",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        # Weekly. Laws are promulgated in ones and twos, and the listing is
        # ordered newest first, so a week never misses one.
        schedule="0 5 * * 6",
        notes=(
            "Acts, government and presidential regulations, and ministerial "
            "regulations. Regional instruments come from the imported corpus "
            "under bpk-peraturan-daerah."
        ),
    )

    dataset = "peraturan-pusat"

    def page_urls(self, ctx: ScrapeContext) -> Sequence[str]:
        pages = ctx.limit if ctx.limit is not None else DEFAULT_PAGES
        return [
            f"{SEARCH}?jenis={code}&page={page}" for code in TYPES for page in range(1, pages + 1)
        ]
