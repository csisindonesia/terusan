"""SP2KP's national price series, straight off the API.

The other SP2KP source drives a headless browser to download a Tableau
crosstab, because the regency table exists nowhere else. This one needs no
browser at all: the dashboard's own JSON API answers without a key, and it
carries the one thing the crosstab cannot.

**History.** The crosstab exports the day it is showing, so a day not collected
is a day lost for good. `hnt/history-series` takes a date range and returns the
whole of it — 2024-02-01 onward, about 655 trading days per commodity — so this
series can be rebuilt from nothing at any time. That is the difference between
a dataset that must be watched and one that can be repaired.

It is also wider. The crosstab prices seventeen goods; the API's staple list
runs to fifty-six, of which forty-two carry a series — Bulog's SPHP rice and
imported soybeans among them, both absent from the crosstab, and both the kind
of thing a subsidy question turns on.

What it does not have is place. Every figure here is the *harga nasional
tertimbang*, the national weighted price: one number per commodity per day for
the whole country. The province and regency breakdowns the API offers are
single-date endpoints with no range, so they cannot be backfilled and are not
collected here. For where a price is high rather than how it has moved, the
crosstab source is still the one.

The endpoints were found by watching what the dashboard itself requests. Guessed
paths are no use: this API answers 401 for a route it does not have, so a wrong
guess looks exactly like a locked door.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta

from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import fetcher

API = "https://api-sp2kp.kemendag.go.id"

#: The staple basket. `Barang Kebutuhan Pokok` against the ministry's other
#: types — `Barang Penting` is building materials and steel, `Barang Pasar
#: Induk` the wholesale markets — which are real series and not food.
STAPLE_TYPE = "Barang Kebutuhan Pokok"

#: The first day the series carries. Asking for anything earlier returns the
#: same rows, so this is the floor rather than a preference.
HISTORY_BEGINS = date(2024, 2, 1)

#: Days of overlap on an incremental run. The ministry revises a day's weighted
#: price for a while after publishing it, and normalization resolves a
#: re-collected day by retrieval time, so overlapping is correcting.
OVERLAP_DAYS = 14


def variants_url() -> str:
    return f"{API}/master/api/variant?take=100000&is_active=true"


def history_url(variant_id: int, start: date, end: date) -> str:
    return (
        f"{API}/report/api/hnt/history-series"
        f"?tanggal_start={start.isoformat()}&tanggal_end={end.isoformat()}"
        f"&variant_id={variant_id}"
    )


class NationalFoodPrices(Source):
    """Daily national weighted prices, one series per staple good."""

    meta = SourceMeta(
        slug="kemendag-sp2kp-national",
        name="SP2KP — Harga Nasional Tertimbang",
        organization="Kementerian Perdagangan",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url=f"{API}/report/api/hnt/history-series",
        license="Kementerian Perdagangan open data",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=2.0,
        # After the ministry's own 08:00 publication, and an hour behind the
        # crosstab source so the two do not contend for the same host.
        schedule="0 9 * * *",
        notes=(
            "No browser and no key, unlike `kemendag-sp2kp-prices`, and unlike "
            "it this can be rebuilt from nothing: the series endpoint takes a "
            "date range back to 2024-02-01. National weighted price only — the "
            "API's province and regency views are single-date and cannot be "
            "backfilled, so the crosstab source remains the one for place."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        end = date.today()
        # An incremental run still overlaps, because a published day is revised.
        start = (
            max(ctx.since - timedelta(days=OVERLAP_DAYS), HISTORY_BEGINS)
            if ctx.since
            else HISTORY_BEGINS
        )

        with fetcher() as http:
            catalogue = http.get(variants_url())
            body = catalogue.json()
            variants = [
                variant
                for variant in (body.get("data") or [])
                if ((variant.get("tipe_komoditas") or {}).get("nama")) == STAPLE_TYPE
            ]
            if not variants:
                # The master list is what names every series; without it this
                # source collects nothing and would report success doing so.
                raise ValueError(f"no {STAPLE_TYPE!r} variants in the master list")

            # Landed too: it is the only place the variant's name and unit are
            # stated, and a replay that could not name a series would be a
            # replay of numbers.
            yield Artifact(
                content=json.dumps(body, separators=(",", ":")).encode(),
                filename="variants.json",
                # Its own dataset, not the prices': the generic JSON reader
                # claims it and would otherwise put ninety rows of master data
                # beside twenty-seven thousand price observations, where every
                # mapping has to step over them.
                dataset="sp2kp-variants",
                source_url=str(catalogue.url),
                media_type="application/json",
                metadata={"variants": len(variants), "role": "catalogue"},
            )

            for variant in sorted(variants, key=lambda v: int(v["id"])):
                variant_id = int(variant["id"])
                response = http.get(history_url(variant_id, start, end))
                series = response.json()

                if not (series.get("data") or []):
                    # Fourteen of the fifty-six are listed and never priced.
                    # Landing an empty series would put a document in RAW that
                    # says nothing and count as a collection that happened.
                    continue

                yield Artifact(
                    content=json.dumps(series, separators=(",", ":")).encode(),
                    filename=f"hnt-{variant_id}.json",
                    dataset="sp2kp-national-prices",
                    source_url=str(response.url),
                    media_type="application/json",
                    # The response is a bare list of date and price: which good
                    # it prices is in the request, and nowhere in the bytes.
                    # The extractor reads it back from here.
                    metadata={
                        "variant_id": variant_id,
                        "variant_nama": variant.get("nama"),
                        "komoditas": (variant.get("komoditas") or {}).get("nama"),
                        "satuan": (variant.get("satuan") or {}).get("display"),
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                    },
                )
