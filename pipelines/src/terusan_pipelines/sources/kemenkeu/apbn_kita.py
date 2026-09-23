"""APBN KiTa — the monthly report on how the state budget is running.

DJPK's realisation figures, already collected as `djpk-apbd`, are the regional
half of Indonesian public finance. This is the central half: every month the
finance ministry publishes what the state budget took in and spent against what
it planned, as a report with the tables in it.

It cannot be collected yet, and the reason is specific enough to act on. The
page at `kemenkeu.go.id/informasi-publik/keuangan-negara/apbn-kita` is an
Angular application: it serves the same 27 KB shell on every route, carrying
four links, none of them a report. Its content comes from
`media.kemenkeu.go.id`, which is reachable — `/menu/getmenu?lang=id` answers
with the site's whole navigation, and it names this section
`/Informasi-Publik/Keuangan-Negara/APBN-Kita`. What was not found is the route
that lists the editions: `/SinglePage/CustomPage` answers `Wrong Path` for it,
so the listing belongs to a lazily-loaded chunk that has not been read yet.

That is half an hour of work against a running browser's network tab, not a
research problem — which is why this is registered with the trail written down
rather than left as a gap for someone to rediscover.
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

LISTING = "https://www.kemenkeu.go.id/informasi-publik/keuangan-negara/apbn-kita"

#: The backend the application reads. Verified to answer; its publication
#: listing route is the missing piece.
CONTENT_API = "https://media.kemenkeu.go.id"


class ApbnKita(GatedSource):
    """The monthly budget reports."""

    meta = SourceMeta(
        slug="kemenkeu-apbn-kita",
        name="Kemenkeu — APBN KiTa",
        organization="Kementerian Keuangan",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=LISTING,
        license="Kementerian Keuangan open data",
        update_frequency=UpdateFrequency.MONTHLY,
        active=False,
        schedule=None,
        notes=(
            "Central government revenue, spending, deficit and financing against "
            "the budget, monthly. The regional side already arrives through "
            "djpk-apbd."
        ),
    )

    access = (
        f"the editions are behind an Angular app backed by {CONTENT_API}; read the "
        "publication listing route out of its lazily-loaded chunk (the menu route "
        "/menu/getmenu?lang=id already answers) and turn this into an ApiSource"
    )
