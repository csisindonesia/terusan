"""BPS — the statistics API, and why the website is not an option.

BPS is the primary statistical source for Indonesia and it publishes two ways.
`bps.go.id` renders tables for a reader and answers an unattended client with a
Cloudflare challenge, so it cannot be collected. `webapi.bps.go.id` answers
JSON, covers the same tables, and wants an application key — free, issued per
registered application at webapi.bps.go.id/developer.

This source is written against that API and collects nothing until the key is
in `.env` as `BPS_API_KEY`. It is registered inactive rather than left out: the
catalogue should record that the lake knows what BPS holds and is one
registration away from it, and the day the key arrives this becomes active
without anything downstream learning a new slug.

What it lands when it runs: the domain list (every province and regency BPS
keys figures by), the subject list, and the variable list for the national
domain — the three catalogues that say what can be asked for. The figures
themselves are one call per variable and belong in a source that knows which
variables are wanted, which is a question to answer with the catalogue in hand
rather than by guessing now.

Until the key exists, BPS figures reach this lake only second-hand: Trading
Economics and FRED both redistribute the headline series, and both are already
collected. That is a real holding and a poor substitute — it carries the
headline number without the regional breakdown, the revision history or the
definition, which is most of what BPS actually publishes.
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
from ..credentials import credentials
from ..http import Fetcher
from ..portals import AccessNotProvisioned, ApiSource, Endpoint

API = "https://webapi.bps.go.id/v1/api"

#: BPS's code for the national domain. Provinces are `31` and so on, and the
#: domain list this collects is what names the rest.
NATIONAL = "0000"


def catalogue_url(model: str, key: str, domain: str = NATIONAL) -> str:
    return f"{API}/list/model/{model}/lang/ind/domain/{domain}/key/{key}/"


class WebApi(ApiSource):
    """BPS's catalogues: domains, subjects, variables."""

    meta = SourceMeta(
        slug="bps-webapi",
        name="BPS — Web API statistik",
        organization="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://webapi.bps.go.id/",
        license="BPS terms of use — attribution required",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        # Inactive until a key exists. The schedule is the one it should run on
        # when it does, kept here so turning it on is one field.
        active=False,
        schedule="0 3 5 * *",
        notes=(
            "Needs BPS_API_KEY from webapi.bps.go.id/developer. Lands the domain, "
            "subject and variable catalogues; the figures are a second source "
            "once the catalogue says which variables are wanted."
        ),
    )

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        secret = credentials().bps_api_key
        if secret is None:
            raise AccessNotProvisioned(
                "bps-webapi needs BPS_API_KEY in .env; register an application at "
                "https://webapi.bps.go.id/developer"
            )
        key = secret.get_secret_value()

        for model, description in (
            ("domain", "every region BPS keys figures by, with its code"),
            ("subject", "the subject tree the tables are filed under"),
            ("var", "the variables published for the national domain"),
        ):
            yield Endpoint(
                dataset=f"bps-{model}-catalogue",
                url=catalogue_url(model, key),
                filename=f"{model}.json",
                metadata={"model": model, "domain": NATIONAL, "coverage": description},
            )
