"""BPS — the statistics API, and why the website is not an option.

BPS is the primary statistical source for Indonesia and it publishes two ways.
`bps.go.id` renders tables for a reader and answers an unattended client with a
Cloudflare challenge, so it cannot be collected. `webapi.bps.go.id` answers
JSON, covers the same tables, and wants an application key — free, issued per
registered application at webapi.bps.go.id/developer.

This source is written against that API and needs the key in `.env` as
`BPS_API_KEY`; without it a run stops with `AccessNotProvisioned` rather than
landing nothing and calling it a collection.

What it lands when it runs: the domain list (every province and regency BPS
keys figures by), the subject list, and the variable list for the national
domain — the three catalogues that say what can be asked for. The domain list
is not a `list/model` route — BPS answers `Model domain is not recognized` to
that — but `/domain/type/all/`, which returns all 549 in one page. The other two
are paged ten at a time with no way to ask for more (`perpage` is ignored), so
every page is walked: the variable list alone is 176 of them.

The key travels in the URL path, which is the only place BPS accepts it. So the
URL recorded beside each landed file has it replaced by `{key}`: a sidecar is
read by anyone with the lake, and a credential written into it is published. The figures
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
from dataclasses import replace

import httpx

from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..credentials import credentials
from ..http import Fetcher, fetcher
from ..portals import AccessNotProvisioned, ApiSource, Endpoint
from ..ratelimit import HostRateLimiter

API = "https://webapi.bps.go.id/v1/api"

#: BPS's code for the national domain. Provinces are `31` and so on, and the
#: domain list this collects is what names the rest.
NATIONAL = "0000"


def catalogue_url(model: str, key: str, domain: str = NATIONAL, page: int = 1) -> str:
    return f"{API}/list/model/{model}/lang/ind/domain/{domain}/page/{page}/key/{key}/"


def domains_url(key: str) -> str:
    return f"{API}/domain/type/all/key/{key}/"


class BpsError(RuntimeError):
    """BPS answered 200 with `"status": "Error"` — a revoked key, a bad route.

    Raised rather than landed: the body is an error message, and landing it
    would record a catalogue that says nothing as one that was collected.
    """


def page_count(body: dict) -> int:
    """How many pages the listing runs to, from its `data[0]` header."""
    if body.get("status") != "OK":
        raise BpsError(str(body.get("message") or body)[:300])
    data = body.get("data") or []
    header = data[0] if data and isinstance(data[0], dict) else {}
    return int(header.get("pages") or 1)


def api_key() -> str:
    """The key from `.env`, or the reason a run cannot start without it."""
    secret = credentials().bps_api_key
    if secret is None:
        raise AccessNotProvisioned(
            "BPS sources need BPS_API_KEY in .env; register an application at "
            "https://webapi.bps.go.id/developer"
        )
    return secret.get_secret_value()


def redacted(artifact: Artifact, key: str) -> Artifact:
    """The artifact with the key taken out of the URL it records."""
    return replace(artifact, source_url=(artifact.source_url or "").replace(key, "{key}"))


#: Attempts at a body that is not a JSON object before the table is given up.
BODY_ATTEMPTS = 3


def get_json(http: Fetcher, url: str, key: str) -> tuple[httpx.Response, dict]:
    """Fetch, and refuse an error body served with a 200.

    BPS sometimes answers 200 with `null`, or with an HTML page, for a table
    that answers properly a moment later — a transport retry never sees it,
    because nothing failed at the transport. So a body that is not a JSON
    object is asked for again, and after that is a `BpsError`, which the
    figures source skips per table rather than letting it end the run.

    httpx names the URL in its messages, and a run's failure is recorded in the
    catalog verbatim, so the key is taken out of those too.
    """
    body: object = None
    for _ in range(BODY_ATTEMPTS):
        try:
            response = http.get(url)
        except httpx.HTTPError as exc:
            raise BpsError(str(exc).replace(key, "{key}")) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            break
    else:
        raise BpsError(f"not a JSON object after {BODY_ATTEMPTS} attempts: {str(body)[:100]}")
    if body.get("status") != "OK":
        raise BpsError(str(body.get("message") or body)[:300])
    return response, body


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
        schedule="0 3 5 * *",
        notes=(
            "Needs BPS_API_KEY from webapi.bps.go.id/developer. Lands the domain, "
            "subject and variable catalogues; the figures are a second source "
            "once the catalogue says which variables are wanted."
        ),
    )

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        """Page one of each catalogue; `collect` follows the rest."""
        key = api_key()
        yield Endpoint(
            dataset="bps-domain-catalogue",
            url=domains_url(key),
            filename="domain.json",
            metadata={"model": "domain", "coverage": "every region BPS keys figures by"},
        )
        for model, description in (
            ("subject", "the subject tree the tables are filed under"),
            ("var", "the variables published for the national domain"),
        ):
            yield Endpoint(
                dataset=f"bps-{model}-catalogue",
                url=catalogue_url(model, key),
                filename=f"{model}-p1.json",
                metadata={"model": model, "domain": NATIONAL, "coverage": description, "page": 1},
            )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        key = api_key()
        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)
        landed = 0
        with fetcher(headers=self.request_headers(), timeout=self.timeout, limiter=limiter) as http:
            for first in self.endpoints_for(ctx, http):
                model = first.metadata["model"]
                pages = None
                page = 1
                endpoint = first
                while True:
                    if ctx.limit is not None and landed >= ctx.limit:
                        return
                    # Every page is checked, not only the first: a key revoked
                    # halfway through a walk answers 200 with an error body.
                    response, body = get_json(http, endpoint.url, key)
                    count = page_count(body)
                    pages = pages or count
                    yield redacted(endpoint.artifact(response), key)
                    landed += 1
                    page += 1
                    if model == "domain" or page > pages:
                        break
                    endpoint = replace(
                        first,
                        url=catalogue_url(model, key, page=page),
                        filename=f"{model}-p{page}.json",
                        metadata={**first.metadata, "page": page, "pages": pages},
                    )
