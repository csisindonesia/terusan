"""Bawaslu — what the election supervisor found.

KPU runs the election; Bawaslu polices it. Its publications and announcements
are where violations, rulings and the supervision of each stage are recorded,
which is the oversight half of the political data the roadmap asks for — an
election result without it says who won and nothing about how.

Bawaslu publishes as news and announcement pages rather than as data, so pages
are what land. The sections are named rather than crawled from the root: the
site's navigation includes every provincial office, and following it would land
thirty-four sites' worth of local news under a national slug.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import PageSource

SITE = "https://www.bawaslu.go.id"


class Publications(PageSource):
    """The national publication and announcement sections."""

    meta = SourceMeta(
        slug="bawaslu-publications",
        name="Bawaslu — publikasi dan pengumuman",
        organization="Badan Pengawas Pemilihan Umum",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        schedule="0 5 * * 4",
        notes=(
            "National sections only. The provincial offices publish separately "
            "and would be their own sources."
        ),
    )

    dataset = "bawaslu-publications"

    urls = (
        f"{SITE}/",
        f"{SITE}/berita-bawaslu",
        f"{SITE}/id/berita/berita-pusat",
        f"{SITE}/id/berita/press-release",
        f"{SITE}/id/publikasi/agenda-kegiatan",
    )
