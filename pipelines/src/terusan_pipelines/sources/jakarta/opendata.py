"""Jakarta Open Data — the capital's own figures, and the route that resets.

Jakarta publishes what a city government knows and a national ministry does
not: traffic, waste, flooding by neighbourhood, permits, and the budget at
district level. It is one of the two provincial portals in the source sheet
that publish figures rather than links to them.

Its front page answers. Its catalogue does not: every request to
`/dataset?page=N` is met with a connection reset — not a 403, not a challenge,
but the connection dropped mid-response, repeatably, through four retries with
backoff. The front page carries no dataset links either, so there is no route
from what answers to what is wanted.

West Java's portal refuses this network too, differently, which is the reason
to suspect both are geographic rather than broken: a run from inside Indonesia
may well see both portals. That is worth testing before anyone concludes the
capital's open data is gone.
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

SITE = "https://data.jakarta.go.id"


class OpenDataJakarta(GatedSource):
    """The city's open data catalogue."""

    meta = SourceMeta(
        slug="jakarta-opendata",
        name="Jakarta Open Data",
        organization="Pemerintah Provinsi DKI Jakarta",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Open data — attribution required",
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "City-level figures the national portals do not carry: traffic, "
            "waste, flooding, permits and district budgets."
        ),
    )

    access = (
        "the front page answers but /dataset resets the connection on every "
        "attempt; retry from an Indonesian network — jabar-opendata refuses this "
        "one too — and if it answers there, this becomes a PageSource over the "
        "catalogue pages"
    )
