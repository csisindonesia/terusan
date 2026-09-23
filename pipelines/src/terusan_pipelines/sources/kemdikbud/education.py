"""Schools: the reference register, and the report card that is not public.

`referensi.data.kemdikbud.go.id` is the register of every Indonesian school —
each with its NPSN identifier, its address, its level and the region it sits
in. It is the join key for any question about education by place, and the
roadmap's village panel needs it to count schools per village. The ministry was
renamed in 2024 and the register moved with it, to
`referensi.data.kemendikdasmen.go.id`; the old host no longer resolves, which
is worth stating because the source sheet still carries it.

The register renders per region: a page per level of the hierarchy, drilling
from the national page down through province and regency. The national pages
land here, and the drill-down is an extractor's job once it has read the region
codes off them — which is a second source, not a deeper crawl, because it is
one request per regency and there are 514 of them.

Rapor Pendidikan, the school report card, is the other half the sheet asks for.
It shows each school its own assessment results, and it shows them behind a
login: the scores are not public per school, and its host does not answer from
outside either. Registered, gated, and honest about which of the two it is.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import GatedSource, PageSource

REFERENCE = "https://referensi.data.kemendikdasmen.go.id"


class SchoolRegister(PageSource):
    """The national reference pages of the school register."""

    meta = SourceMeta(
        slug="kemdikbud-referensi",
        name="Referensi Data Pendidikan — registri satuan pendidikan",
        organization="Kementerian Pendidikan Dasar dan Menengah",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=REFERENCE,
        license="Public sector information",
        # Schools open, close and are re-registered continuously; the published
        # snapshot follows the semester.
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        schedule="0 6 11 * *",
        notes=(
            "NPSN, name, level and location per school. Moved from "
            "kemdikbud.go.id to kemendikdasmen.go.id with the 2024 ministry "
            "rename; the old host no longer resolves."
        ),
    )

    dataset = "school-register"

    urls = (
        f"{REFERENCE}/pendidikan/dikdas",
        # The national roll-up: every province with its school counts by level.
        f"{REFERENCE}/pendidikan/dikdas/000000/1/all/all/all",
    )


class RaporPendidikan(GatedSource):
    """The school report card."""

    meta = SourceMeta(
        slug="kemdikbud-rapor-pendidikan",
        name="Rapor Pendidikan — capaian satuan pendidikan",
        organization="Kementerian Pendidikan Dasar dan Menengah",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://raporpendidikan.kemdikbud.go.id/",
        license="Restricted; per-school results are not published openly",
        update_frequency=UpdateFrequency.ANNUAL,
        active=False,
        schedule=None,
        notes=(
            "Literacy, numeracy, climate and equity scores per school. The "
            "national and regional aggregates are published; the per-school "
            "detail is shown to the school itself."
        ),
    )

    access = (
        "each school sees its own report behind a login, and the host in the "
        "source sheet no longer resolves; request the published aggregate files "
        "from the ministry and land them under this slug"
    )
