"""Satu Data Perdagangan — the trade ministry's portal, behind a key.

`kemendag-sp2kp-prices` already collects one thing from this ministry: the
daily staple prices from the SP2KP survey, through a Tableau view that only
yields to a real browser. This is the rest of what the ministry publishes —
export and import values by commodity and partner, domestic trade indicators,
and the licensing figures — from its Satu Data portal.

The portal has a JSON API and it answers `{"status":401,"error":"API Key not
found"}` without a key. Keys are issued by the ministry's data unit; set one as
`KEMENDAG_API_KEY` and this becomes active.

Indonesia's trade figures also arrive independently through
`comtrade-indonesia`, reported by Indonesia's partners rather than by Indonesia.
Holding both is the point: they disagree, and the disagreement is a finding.
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

API = "https://satudata.kemendag.go.id/api"


class TradeData(ApiSource):
    """The ministry's dataset catalogue and its published series."""

    meta = SourceMeta(
        slug="kemendag-satudata",
        name="Satu Data Perdagangan",
        organization="Kementerian Perdagangan",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://satudata.kemendag.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        active=False,
        schedule="0 6 12 * *",
        notes=(
            "Needs KEMENDAG_API_KEY from the ministry's data unit. Staple prices "
            "already arrive through kemendag-sp2kp-prices; this is the trade "
            "series beside them."
        ),
    )

    def request_headers(self) -> dict[str, str]:
        secret = credentials().kemendag_api_key
        if secret is None:
            raise AccessNotProvisioned(
                "kemendag-satudata needs KEMENDAG_API_KEY in .env; request a key "
                "from the trade ministry's data unit"
            )
        return {**super().request_headers(), "x-api-key": secret.get_secret_value()}

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        # The catalogue first: what a key opens is a question the key answers,
        # and every further endpoint is named by this response rather than
        # guessed at from outside.
        yield Endpoint(
            dataset="kemendag-catalogue",
            url=f"{API}/datasets",
            filename="datasets.json",
            metadata={"coverage": "every dataset the portal exposes to a keyed caller"},
        )
