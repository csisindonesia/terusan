"""Health: the data portal, and the facility register behind it.

Kemenkes publishes health data through two front doors that now lead to the
same place. `data.kemkes.go.id` no longer resolves; `satudata.kemkes.go.id`
does, and links straight through to SATUSEHAT's data room, which is where the
indicators, the facility register and the programme dashboards are rendered.

Both pages land here, from the one source, because they are one holding reached
two ways — registering them separately would put the same tables under two
slugs and make "what health data do we hold" a question with two answers.

What lands is the rendered page. The data room is a Next.js application that
renders server-side, so the figures are in the HTML; its backend answers only
its own frontend. An extractor reads them out of RAW, which is also what keeps
the holding intact when Kemenkes rebuilds the room, as it has once already.
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


class HealthData(PageSource):
    """The health data room and the portal that points at it."""

    meta = SourceMeta(
        slug="kemkes-health-data",
        name="Kemenkes — Satu Data Kesehatan / SATUSEHAT",
        organization="Kementerian Kesehatan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://satusehat.kemkes.go.id/data",
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        schedule="0 6 9 * *",
        notes=(
            "Health indicators and the facility register, as the data room "
            "renders them. data.kemkes.go.id, which the source sheet names, no "
            "longer resolves."
        ),
    )

    dataset = "kemkes-health-data"

    urls = (
        "https://satusehat.kemkes.go.id/data",
        "https://satudata.kemkes.go.id/",
    )
