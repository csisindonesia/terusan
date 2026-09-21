# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: PIHPS regional food prices (Bank Indonesia)
Source: https://www.bi.go.id/hargapangan/TabelHarga/PasarTradisionalDaerah

Endpoints reverse-engineered from the page's DevExtreme config (the inline
`OnBeforeSend` handler and each dropdown's `loadUrl`). No auth, no session, no
token: a plain GET returns JSON.

The grid endpoint is
`GET /hargapangan/WebSite/TabelHarga/GetGridDataDaerah`, and its parameters are
the dropdowns above the table:

  price_type_id  1 Pasar Tradisional, 2 Pasar Modern, 3 Pedagang Besar,
                 4 Produsen. Four different markets for the same food, not four
                 readings of one — a wholesaler's price and a farmgate price
                 are not the same series and must not be averaged together.
  comcat_id      commodity/category id, empty = every commodity
  province_id    empty = national
  regency_id     empty = the whole province
  market_id      empty = every market
  tipe_laporan   1 = per-commodity report
  start_date     YYYY-MM-DD, day need not be zero-padded
  end_date       YYYY-MM-DD

Response: `{"data": [{"no", "name", "level", "<dd/mm/yyyy>": "price", ...}],
"totalCount"}` — a wide table whose columns are the dates in the window, and
whose rows are commodities. `level` 1 is a category ("Beras"), `level` 2 a
variety within it ("Beras Kualitas Medium I"). A price not yet posted is "-".

Two facts about the endpoint govern how the source above calls it.

First, coverage starts 2017-03-01. Every window before that returns an empty
`data`, including windows that straddle the boundary, so a backfill that asks
for 2016 gets nothing rather than an error.

Second, response time grows faster than the window: measured against the live
endpoint, one month takes ~0.6s, three ~2.9s, six ~11s and a year ~43s. The
month is therefore the unit to page by — a year fetched as twelve requests
costs a fifth of the same year fetched as one.
"""
from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import requests

ROOT = "https://www.bi.go.id/hargapangan/WebSite/TabelHarga"
BASE = f"{ROOT}/GetGridDataDaerah"
HEADERS = {"User-Agent": "Mozilla/5.0"}

#: Earliest window the endpoint answers with data. Established by bisection
#: against the live endpoint: 2017-02 and every month before it return an empty
#: `data`, 2017-03 returns a full table.
COVERAGE_START = date(2017, 3, 1)

#: The four markets the portal prices, by `price_type_id`. Kept here rather
#: than fetched, because the source's indicator slugs are built from these keys
#: and a silently renamed price type should fail loudly rather than quietly
#: file a farmgate price under a retail indicator.
PRICE_TYPES: dict[int, str] = {
    1: "traditional",
    2: "modern",
    3: "wholesale",
    4: "producer",
}

#: What Bank Indonesia calls each of them, for the record written beside the
#: bytes. The English keys above are ours; these are the source's own words.
PRICE_TYPE_NAMES: dict[int, str] = {
    1: "Pasar Tradisional",
    2: "Pasar Modern",
    3: "Pedagang Besar",
    4: "Produsen",
}


def fetch(
    start_date: str,
    end_date: str,
    province_id: str | int = "",
    regency_id: str | int = "",
    price_type_id: int = 1,
    market_id: str | int = "",
    comcat_id: str | int = "",
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """One window of the price grid, as the portal's own table returns it."""
    params = {
        "price_type_id": price_type_id,
        "comcat_id": comcat_id,
        "province_id": province_id,
        "regency_id": regency_id,
        "market_id": market_id,
        "tipe_laporan": 1,
        "start_date": start_date,
        "end_date": end_date,
        "requireTotalCount": "true",
    }
    get = session.get if session is not None else requests.get
    # A year-wide window takes the better part of a minute to come back, and a
    # backfill that times out halfway through is worse than one that waits.
    r = get(BASE, params=params, headers=HEADERS, timeout=180)
    r.raise_for_status()
    return r.json()["data"]


def _ref(endpoint: str, session: requests.Session | None = None, **params: Any) -> list[dict]:
    get = session.get if session is not None else requests.get
    r = get(f"{ROOT}/{endpoint}", params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()["data"]


def provinces(session: requests.Session | None = None) -> list[dict[str, Any]]:
    """The 34 provinces the portal prices, as `{"id", "name"}`.

    Fetched rather than hardcoded: the province list is the portal's own, and a
    province added or renamed there should reach us without an edit here. The
    names come back in the form the geography registry already knows — `Jawa
    Barat`, `DKI Jakarta` — so they resolve without a translation table.
    """
    return _ref("GetRefProvince", session=session)


def regencies(province_id: int, price_type_id: int = 1, session=None) -> list[dict[str, Any]]:
    """The regencies and cities priced within one province."""
    return _ref(
        "GetRefRegency", session=session, price_type_id=price_type_id, ref_prov_id=province_id
    )


def markets(regency_id: int, price_type_id: int = 1, session=None) -> list[dict[str, Any]]:
    """The individual markets sampled within one regency."""
    return _ref(
        "GetRefMarket", session=session, price_type_id=price_type_id, ref_regency_id=regency_id
    )


def commodities(session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Commodities and the categories they sit in.

    Returns both in one list, told apart by the `id` prefix: `cat_1` is the
    category `Beras`, `com_1` the variety `Beras Kualitas Bawah I` whose
    `cat_id` points back at it. The hierarchy the grid's `level` column encodes
    positionally is named here, which is what makes the grid's rows mappable to
    a commodity dimension at all.
    """
    return _ref("GetRefCommodityAndCategory", session=session)


def price_types(session: requests.Session | None = None) -> list[dict[str, Any]]:
    """The four markets, as the portal names them."""
    return _ref("GetRefPriceType", session=session)


def month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split a span into calendar months, clipped to `start` and `end`.

    The month is the paging unit because the endpoint's cost grows faster than
    the window (see the module docstring), and the calendar month rather than a
    rolling 30 days because it makes a run's windows identical between runs —
    which is what lets content-addressed landing recognise a month already
    fetched instead of storing it twice under a shifted boundary.
    """
    if end < start:
        return
    cursor = start.replace(day=1)
    while cursor <= end:
        nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(cursor, start), min(nxt - timedelta(days=1), end)
        cursor = nxt


def parse(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Unpivot the wide grid into one record per commodity and date.

    Kept beside the fetcher because the shape it undoes is the endpoint's, not
    the warehouse's. The extractor calls it on the landed bytes.
    """
    for row in rows:
        name = row.get("name")
        level = row.get("level")
        for key, value in row.items():
            if "/" not in key:
                continue
            yield {"commodity": name, "level": level, "date": key, "price": value}


def save_csv(rows, out_path):
    if not rows:
        return
    fields = sorted({k for row in rows for k in row.keys()}, key=lambda k: (k not in ("no", "name", "level"), k))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
