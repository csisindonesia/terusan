"""OJK — Statistik Perbankan Indonesia, from July 2025 onward.

`ojk-banking-spi` lands the monthly SPI workbook from the page OJK has always
published it on, and that page stopped at the June 2025 edition. Everything
since is on the Sistem Jaringan Komunikasi portal, `data.ojk.go.id/SJKPublic`,
which publishes no workbook at all: its tables are pivot grids over an OLAP
cube, and the only way to take the figures away is the export the grid itself
offers.

That export is a plain GET. `GetGridCSVData` answers the fact rows behind a
cube for the months named in its filter — one row per bank group, province,
currency and whatever else the cube is cut by — as JSON, to any client, with
no session or key. Those responses are what land here, one per cube and
month, so the figures are the ones OJK's own export hands a reader rather than
a recomputation of the pivot.

A month lands whole, in one response, rather than in pages. The credit cube is
sixty thousand rows a month and the portal takes about a minute to answer it,
which is slow but not failing; and a month split across pages is a month no
extractor can add up, because each page is read on its own. The Silver series
are sums over a month (`extract/ojk_sjk.py`), so a whole month per artifact is
what makes them possible at all.

What this cannot recover is the SPI's own table layout. The portal holds the
cubes the tables were computed from, not the tables, so there is no "Tabel
1.33.a" in RAW after June 2025 — only the rows it would be summed from. Three
of the old tables (BPR office counts, BPR credit, branch offices by location)
have no cube on the portal at all; the vendored `banking_spi_portal` module
records the search.

Figures arrive as JSON numbers, which OJK's serialiser writes as doubles.
Each row's Rupiah figure stays well below 2^53, so none loses a digit to that,
but it is the portal's representation and not a choice made here; the sums are
taken in decimal, since the national deposit total is within a whisker of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

import httpx
import structlog

from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import Fetcher, fetcher
from ..portals import ApiSource, Endpoint, wants

log = structlog.get_logger(__name__)

PORTAL = "https://data.ojk.go.id/SJKPublic"
GRID = f"{PORTAL}/Dataset/Dataset/GetGridCSVData"

#: A whole month of the credit cube is about 40 MB and a minute of the
#: portal's time. The default timeout would give up on it halfway.
MONTH_TIMEOUT_SECONDS = 300.0

#: How many months a routine run takes, ending at the newest one published.
#: OJK revises the last few months of a cube as late reports arrive, and a
#: revised month is a different content hash, so it lands beside the first.
RECENT_MONTHS = 3

#: How far back from today to look for the newest published month. The portal
#: has no endpoint that says, and the cubes run one to three months behind.
LATEST_LOOKBACK = 14

#: A full pull walks back until this many consecutive months are empty, which
#: is where a cube's history starts. One empty month is not enough: a cube with
#: a gap in it would stop there and never reach the years before.
EMPTY_RUN_ENDS_HISTORY = 3


@dataclass(frozen=True, slots=True)
class Cube:
    """One cube on the portal, and the dataset its rows land under."""

    table: str
    metric_id: int
    dataset: str
    #: What the old SPI workbook called the tables computed from it, so a
    #: reader moving from one vintage to the other can find their way.
    spi_tables: str


CUBES: tuple[Cube, ...] = (
    Cube("BU_DanaPihakKetiga", 341, "ojk-commercial-bank-deposits", "1.33.a, 1.34.a"),
    Cube(
        "BU_KreditdanPembiayaanEntitas",
        340,
        "ojk-commercial-bank-credit",
        "3.12.a, 3.13.a, 3.22.a",
    ),
    # National only: the cube carries head-office counts by bank group, and
    # its province column is always empty.
    Cube("BU_InformasiKantor", 330, "ojk-commercial-bank-offices", "1.43.a (national)"),
    Cube("BPR_DanaPihakKetiga", 10, "ojk-rural-bank-deposits", "2.8"),
)


class PortalRefused(RuntimeError):
    """The portal answered something other than its grid JSON."""


def grid_url(cube: Cube, month: str, *, skip: int, take: int, count: bool = False) -> str:
    """The export URL for one page of one month of a cube.

    The filter is spelt the way the portal's own grid spells it — the field,
    then `\\=\\`, then the value — because that is the only form it parses.
    """
    params: dict[str, str | int] = {
        "_filter": f"Bulan\\=\\{month}",
        "_field": "Bulan",
        "_data": month,
        "namaTable": cube.table,
        "metricID": cube.metric_id,
        "take": take,
        "skip": skip,
    }
    if count:
        params["requireTotalCount"] = "true"
    return f"{GRID}?{urlencode(params)}"


def month_before(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    if number == 1:
        return f"{year - 1}-12"
    return f"{year}-{number - 1:02d}"


def _this_month(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year}-{today.month:02d}"


def _json(response: httpx.Response) -> dict:
    """The grid's answer, or a refusal that says what came back instead.

    The portal sits behind a WAF that answers a blocked request with an HTML
    page and a 200. Landing would take that as a perfectly good `.json`,
    because JSON has no signature to check, so it is refused here.
    """
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        raise PortalRefused(f"{response.url}: expected JSON, got {content_type or 'nothing'}")
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise PortalRefused(f"{response.url}: no data list in the grid's answer")
    return body


class SjkBankingStatistics(ApiSource):
    """The SPI's cubes on the SJK portal, month by month."""

    meta = SourceMeta(
        slug="ojk-banking-sjk",
        name="OJK — Statistik Perbankan Indonesia (portal SJK)",
        organization="Otoritas Jasa Keuangan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.API,
        base_url=f"{PORTAL}/",
        license="OJK terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        schedule="0 4 15-25 * *",
        notes=(
            "The SPI from July 2025, where ojk-banking-spi's page stops. Fact "
            "rows from the portal's own grid export, not the SPI tables: "
            "deposits and credit of commercial banks by province, head offices "
            "nationally, and rural bank deposits by province.\n\n"
            "A routine run takes the newest three months of each cube, which "
            "is what picks up OJK's revisions. `--param full=true` walks each "
            "cube back to its first month (commercial banks from 2023, rural "
            "banks earlier); `--since` starts at that month instead."
        ),
    )

    headers = {"Accept": "application/json, text/javascript, */*; q=0.01"}

    timeout = MONTH_TIMEOUT_SECONDS

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        landed = 0
        with fetcher(headers=self.request_headers(), timeout=self.timeout) as http:
            for endpoint in self.endpoints_for(ctx, http):
                if ctx.limit is not None and landed >= ctx.limit:
                    return
                # `get`, not `try_get`: a cube missing from a month would leave
                # the run looking complete with nothing in RAW to say otherwise,
                # and a run that fails and is retried is the better outcome.
                response = http.get(endpoint.url)
                rows = len(_json(response)["data"])
                expected = endpoint.metadata["rows_in_month"]
                if rows != expected:
                    # The sums read off this month would be short, and nothing
                    # downstream could tell.
                    raise PortalRefused(
                        f"{endpoint.url}: {rows} rows where the count said {expected}"
                    )
                yield endpoint.artifact(response)
                landed += 1

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        reached = 0
        for cube in CUBES:
            latest = self._latest(http, cube)
            if latest is None:
                # One cube going quiet is OJK withdrawing a table, which is
                # worth a line in the log and not worth discarding the others.
                log.warning("ojk_sjk.cube_empty", table=cube.table)
                continue
            reached += 1
            for month, total in self._months(ctx, http, cube, latest):
                yield _month(cube, month, total)

        if reached == 0:
            # Every cube empty is the portal, not OJK: a run that landed
            # nothing must not pass for a month in which nothing was published.
            raise PortalRefused("no cube on the SJK portal answered with any month")

    def _count(self, http: Fetcher, cube: Cube, month: str) -> int:
        response = http.get(grid_url(cube, month, skip=0, take=1, count=True))
        return int(_json(response).get("totalCount") or 0)

    def _latest(self, http: Fetcher, cube: Cube) -> str | None:
        month = _this_month()
        for _ in range(LATEST_LOOKBACK):
            if self._count(http, cube, month):
                return month
            month = month_before(month)
        return None

    def _months(
        self, ctx: ScrapeContext, http: Fetcher, cube: Cube, latest: str
    ) -> Iterator[tuple[str, int]]:
        """The months this run takes, newest first, with their row counts."""
        floor = f"{ctx.since.year}-{ctx.since.month:02d}" if ctx.since else None
        full = wants(ctx, "full")

        month, taken, empty = latest, 0, 0
        while True:
            if floor is not None and month < floor:
                return
            if floor is None and not full and taken >= RECENT_MONTHS:
                return
            total = self._count(http, cube, month)
            if total:
                empty = 0
                yield month, total
            else:
                empty += 1
                if empty >= EMPTY_RUN_ENDS_HISTORY:
                    return
            taken += 1
            month = month_before(month)


def _month(cube: Cube, month: str, total: int) -> Endpoint:
    return Endpoint(
        dataset=cube.dataset,
        # `take` is the count just read, so the portal answers the whole month.
        url=grid_url(cube, month, skip=0, take=total),
        filename=f"{cube.table}-{month}.json",
        partition=(f"year={month[:4]}", f"month={month[5:7]}"),
        metadata={
            "table": cube.table,
            "metric_id": cube.metric_id,
            "month": month,
            "row_offset": 0,
            "rows_in_month": total,
            "spi_tables": cube.spi_tables,
        },
    )
