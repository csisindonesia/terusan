"""Open Data Jabar — the regional portal that is worth one, and its gate.

Of Indonesia's thirty-eight provinces, two run open data portals that publish
figures rather than links: West Java and Jakarta. West Java's is the larger —
thousands of datasets from every provincial agency, with an API — and it is the
model for what regional data can look like, which is why it is in the source
sheet while thirty-six other provinces are not.

It is also behind the province's own cloud gateway, which answers this network
with a service-status page rather than the portal. That is an edge block, not a
maintenance window: the same page comes back for the API and for the catalogue
alike.

Registered inactive with the note, because the block may well be geographic —
a run from inside Indonesia may see the portal — and the day that is true this
becomes an ApiSource over `/api/bigdata/list_dataset` without a new slug.
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


class OpenDataJabar(GatedSource):
    """West Java's provincial open data."""

    meta = SourceMeta(
        slug="jabar-opendata",
        name="Open Data Jawa Barat",
        organization="Pemerintah Provinsi Jawa Barat",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.API,
        base_url="https://opendata.jabarprov.go.id/",
        license="Open data — attribution required",
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "Several thousand datasets from provincial agencies, with an API at "
            "/api/bigdata/list_dataset. The richest subnational publisher in the "
            "country."
        ),
    )

    access = (
        "the province's cloud gateway answers this network with a service-status "
        "page instead of the portal; retry from a network it accepts — the block "
        "may be geographic — then wire /api/bigdata/list_dataset in here"
    )
