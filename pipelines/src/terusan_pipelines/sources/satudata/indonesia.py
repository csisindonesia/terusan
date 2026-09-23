"""Satu Data Indonesia — the catalogue, not the data.

data.go.id is where every ministry and region is supposed to register what it
publishes. What it holds is therefore mostly metadata: a title, a publisher, a
period, and a link to a file that lives on the publisher's own server. A lake
that ingested data.go.id as a data source would end up with tens of thousands
of single-figure regional spreadsheets of wildly uneven quality — which is the
warning the source sheet itself carries.

So this is a discovery source. It lands the catalogue pages, and what they are
for is finding the publisher worth wiring up properly: a dataset here names the
agency behind it, and the agency is what becomes a source in this package.

The portal renders server-side and its backend answers only its own frontend,
so the pages are what land. Twenty pages by default, which is the recent end of
a catalogue in the tens of thousands; a full sweep is `--limit`.
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

CATALOGUE = "https://data.go.id/dataset"

#: Pages per run. Each is ~370 KB of rendered markup holding a dozen entries.
DEFAULT_PAGES = 20


class Catalogue(PageSource):
    """The dataset catalogue, page by page as rendered."""

    meta = SourceMeta(
        slug="satudata-indonesia",
        name="Satu Data Indonesia — katalog dataset",
        organization="Kementerian PPN/Bappenas",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://data.go.id/",
        license="Varies by publisher; stated per dataset",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        # Monthly. This is a discovery layer: knowing a ministry registered
        # something new is a monthly question, not a daily one.
        schedule="0 2 3 * *",
        notes=(
            "Discovery source. Lands catalogue pages to find publishers worth "
            "wiring up; the figures themselves come from the publisher's own "
            "portal, which is what the rest of this package collects."
        ),
    )

    dataset = "satudata-catalogue"

    def page_urls(self, ctx: ScrapeContext) -> Sequence[str]:
        pages = ctx.limit if ctx.limit is not None else DEFAULT_PAGES
        return [
            CATALOGUE if page == 1 else f"{CATALOGUE}?page={page}" for page in range(1, pages + 1)
        ]
