"""Satu Data Ketenagakerjaan — employment, wages and training.

The labour ministry's portal carries what the labour force survey does not:
registered unemployment, job vacancies filed with the ministry, minimum wages
by region, industrial relations cases, and the vocational training system's
output. BPS measures the labour market; this records what the ministry does
about it.

The portal is a single-page application whose API is at `/api` and answers only
its own frontend, so the rendered shell is what lands. That is thin — it is
the application, not the data — and it is landed anyway, because it is the
record that the portal existed and what it advertised, and because an
extractor reading the API path out of it is how this becomes a real API source
without a second round of reconnaissance.
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

SITE = "https://satudata.kemnaker.go.id"


class LabourData(PageSource):
    """The manpower data portal, as served."""

    meta = SourceMeta(
        slug="kemnaker-satudata",
        name="Satu Data Ketenagakerjaan",
        organization="Kementerian Ketenagakerjaan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        schedule="0 6 13 * *",
        notes=(
            "Vacancies, minimum wages, industrial relations and training output. "
            "The portal's own API at /api answers only its frontend; this lands "
            "the application shell until that changes."
        ),
    )

    dataset = "kemnaker-portal"

    urls = (f"{SITE}/", f"{SITE}/api")
