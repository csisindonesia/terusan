"""Global Forest Watch — what the catalogue says, and what it costs to query.

GFW's data API is two things behind one host. The catalogue — every dataset,
its versions, its licence, its fields, what it was derived from — answers
anybody. The data itself, the per-hectare tree cover loss that the platform is
actually for, answers only a caller with an API key, and a key needs an account
and is issued per application.

So this lands the catalogue, which is a real holding and not a placeholder: it
is the authoritative record of which version of `umd_tree_cover_loss` existed
on which date, and a figure quoted from GFW without its version is a figure
nobody can reproduce. The datasets named in `TRACKED` are the ones an
Indonesian forest question reaches for, and their metadata is landed one by one
beside the full listing.

When a key is provisioned, the query endpoint becomes a second source beside
this one — `gfw-forest-change` for the figures — rather than a change here: a
catalogue and a query are different cadences and different failure modes.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..base import (
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import Fetcher
from ..portals import ApiSource, Endpoint

DATA_API = "https://data-api.globalforestwatch.org"

#: The datasets an Indonesian deforestation question is asked of. Named rather
#: than filtered by keyword: a search for "forest" ranks a hundred datasets and
#: the four that answer the question are not reliably the top four.
TRACKED = (
    "umd_tree_cover_loss",
    "umd_tree_cover_density_2000",
    "gfw_forest_carbon_gross_emissions",
    "gfw_integrated_alerts",
)


class ForestCatalogue(ApiSource):
    """The GFW dataset catalogue, and the versions of what matters here."""

    meta = SourceMeta(
        slug="gfw-catalogue",
        name="Global Forest Watch — data API catalogue",
        organization="World Resources Institute",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.API,
        base_url=DATA_API,
        # The catalogue is global; the tracked datasets are read for Indonesia.
        country=None,
        license="CC-BY-4.0 for most datasets; per-dataset licence in the metadata",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=1.0,
        # Weekly. Integrated alerts version daily, and a week is soon enough to
        # know which version a figure came from.
        schedule="0 6 * * 2",
        notes=(
            "Catalogue only: the query endpoint needs an API key from "
            "data-api.globalforestwatch.org. Landing the catalogue is what lets "
            "a figure be tied to the dataset version it came from."
        ),
    )

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        yield Endpoint(
            dataset="gfw-catalogue",
            url=f"{DATA_API}/datasets",
            filename="datasets.json",
            metadata={"coverage": "every dataset the GFW data API serves"},
        )

        for dataset in TRACKED:
            yield Endpoint(
                dataset="gfw-dataset-versions",
                url=f"{DATA_API}/dataset/{dataset}",
                filename=f"{dataset}.json",
                metadata={"dataset": dataset, "coverage": "versions, fields and provenance"},
            )
