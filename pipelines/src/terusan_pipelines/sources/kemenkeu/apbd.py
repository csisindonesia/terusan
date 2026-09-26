"""DJPK — regional government budgets (APBD), budgeted and realised.

The portal answers one month of one fiscal year for one filter per request, and
the filter is what decides what a figure means. There are three, and they are
landed as three collections because they are three different questions:

- **`apbd-national`** — `provinsi=--`, `pemda=--`: every regional government in
  the country summed. What the portal shows by default.
- **`apbd-provinces`** — `provinsi=NN`, `pemda=--`: every government inside one
  province summed — the provincial government and all its regencies and
  cities. Jawa Timur's is 122 trillion rupiah of revenue by August 2026.
- **`apbd-governments`** — `provinsi=NN`, `pemda=00` or `pemda=NN`: one
  government's own budget. `00` is the provincial government itself (Jawa
  Timur's own is 26 trillion of those 122), and every other code is a regency
  or a city. The list of them comes from the portal, per province and per year,
  because regencies have been created inside the window this covers.

Figures are cumulative within the fiscal year: `periode=8` is January to
August. A year the portal has closed answers every month with the year-end
file, byte for byte. So a past year is asked for December first and November
second, and when the two are identical the year is taken to be year-end only
and the other ten months are not asked for. Asking December first is also what
labels that file correctly: landing is content-addressed, so the first month
to arrive with those bytes is the month the document is filed under, and
filing a year-end figure under January would say a government had spent its
whole budget by the end of the month.

A run with `since` still reaches three months further back. The portal
restates recent months as regions file late, and a run on the tenth that asked
only for the current month would never collect the final figure for the last
one.

`--param scope=national,provinces,governments` picks collections (all three by
default), and `--param province=13` narrows the provincial two to one province.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import structlog

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
from ..http import Fetcher, fetcher
from ..ratelimit import HostRateLimiter
from .legacy import djpk_apbd

log = structlog.get_logger(__name__)

#: The portal's own year range starts here.
FIRST_YEAR = 2011

#: Requests in flight at once. See `collect`.
WORKERS = 3

#: How far behind `since` a run still reaches, in months. See the docstring.
RESTATEMENT_MONTHS = 3

PROVINCES_URL = "https://djpk.kemenkeu.go.id/portal/provinsi/{year}"
PEMDA_URL = "https://djpk.kemenkeu.go.id/portal/pemda/{province}/{year}"

#: What the portal's filters call "everything".
ALL = "--"

#: The pemda code of the provincial government itself.
PROVINCIAL_GOVERNMENT = "00"

NATIONAL = "national"
PROVINCES = "provinces"
GOVERNMENTS = "governments"
SCOPES = (NATIONAL, PROVINCES, GOVERNMENTS)

DATASETS = {
    NATIONAL: "apbd-national",
    PROVINCES: "apbd-provinces",
    GOVERNMENTS: "apbd-governments",
}


@dataclass(frozen=True, slots=True)
class Filter:
    """One combination of the portal's two region filters."""

    scope: str
    provinsi: str = ALL
    provinsi_name: str = ""
    pemda: str = ALL
    pemda_name: str = ""

    def filename(self, year: int, month: int) -> str:
        stamp = f"{year}-{month:02d}"
        if self.scope == NATIONAL:
            return f"apbd-{stamp}.xml"
        if self.scope == PROVINCES:
            return f"apbd-{self.provinsi}-{stamp}.xml"
        return f"apbd-{self.provinsi}-{self.pemda}-{stamp}.xml"

    def partition(self, year: int) -> tuple[str, ...]:
        # One directory per province for the governments: five hundred-odd
        # governments times twelve months in one year's directory is slow to
        # list on the NAS.
        if self.scope == GOVERNMENTS:
            return (f"year={year}", f"province={self.provinsi}")
        return (f"year={year}",)

    def metadata(self, year: int, month: int) -> dict[str, object]:
        return {
            "periode": month,
            "tahun": year,
            # `scope` was "national" before the other two collections existed,
            # and the national documents already landed say so.
            "scope": self.scope,
            "provinsi": self.provinsi,
            "provinsi_name": self.provinsi_name,
            "pemda": self.pemda,
            "pemda_name": self.pemda_name,
        }


def months_to_fetch(year: int, today: date, since: date | None) -> list[int]:
    """The months of `year` a run asks for, in the order it asks.

    A closed year is walked from December backwards, so its year-end file is
    the first to land; the current year from January forwards, so a month the
    portal has not yet published — which answers with the last one's bytes —
    is filed under the month that really produced them.
    """
    if year > today.year:
        return []
    last = today.month if year == today.year else 12

    first = 1
    if since is not None:
        reach = date(since.year, since.month, 1)
        for _ in range(RESTATEMENT_MONTHS):
            reach = (
                date(reach.year - 1, 12, 1)
                if reach.month == 1
                else reach.replace(month=reach.month - 1)
            )
        if year < reach.year:
            return []
        if year == reach.year:
            first = reach.month

    months = list(range(first, last + 1))
    return months if year == today.year else months[::-1]


def _listing(http: Fetcher, url: str) -> dict[str, str]:
    response = http.try_get(url)
    if response is None:
        return {}
    try:
        payload = response.json()
    except ValueError:
        log.warning("djpk.listing_unreadable", url=url, body=response.text[:200])
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): str(v).strip() for k, v in payload.items() if str(k) != ALL}


class RegionalBudgets(Source):
    """APBD budgets and realisation: national, per province, per government."""

    meta = SourceMeta(
        slug="djpk-apbd",
        name="DJPK — Realisasi APBD",
        organization="Kementerian Keuangan (DJPK)",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=djpk_apbd.PORTAL_PAGE_URL,
        license="Kementerian Keuangan open data",
        update_frequency=UpdateFrequency.MONTHLY,
        # A government server answering a few thousand requests a run: one a
        # second, which the portal takes about a second and a half to answer
        # anyway.
        max_requests_per_second=1.0,
        schedule="0 3 10 * *",
        notes=(
            "Three collections: the national total, each province's governments "
            "summed, and each government on its own (the provincial government and "
            "every regency and city). One request per filter and fiscal month; "
            "figures are cumulative within the year."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        today = date.today()
        first_year = ctx.since.year - 1 if ctx.since else FIRST_YEAR
        scopes = self._scopes(ctx)
        only = {p.strip().zfill(2) for p in str(ctx.params.get("province", "")).split(",") if p}

        years = self._years(ctx, max(first_year, FIRST_YEAR), today.year)

        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)
        emitted = 0
        with (
            fetcher(limiter=limiter, headers=djpk_apbd.HEADERS) as http,
            ThreadPoolExecutor(max_workers=WORKERS) as pool,
        ):
            # Newest first: a backfill that is stopped part-way should have
            # stopped short of 2011, not short of this year.
            for year in years:
                months = months_to_fetch(year, today, ctx.since)
                if not months:
                    continue
                regions = list(self._filters(http, year, scopes, only))
                # A few governments at once, all behind the one limiter: the
                # portal takes a second and a half to answer, so one request
                # at a time runs at half the declared ceiling. The limiter,
                # not the pool, is what holds the rate.
                batches = pool.map(
                    lambda region: list(self._year(http, region, year, months)),  # noqa: B023
                    regions,
                )
                for batch in batches:
                    for artifact in batch:
                        yield artifact
                        emitted += 1
                        if ctx.limit is not None and emitted >= ctx.limit:
                            return

    @staticmethod
    def _years(ctx: ScrapeContext, first: int, last: int) -> list[int]:
        """The fiscal years to walk, newest first. `--param years=2012-2026`
        or `years=2024` narrows them, for resuming a backfill."""
        asked = str(ctx.params.get("years") or "").strip()
        if asked:
            low, _, high = asked.partition("-")
            first, last = max(first, int(low)), min(last, int(high or low))
        return list(range(last, first - 1, -1))

    @staticmethod
    def _scopes(ctx: ScrapeContext) -> tuple[str, ...]:
        asked = ctx.params.get("scope")
        if not asked:
            return SCOPES
        wanted = tuple(s.strip() for s in str(asked).split(",") if s.strip())
        unknown = [s for s in wanted if s not in SCOPES]
        if unknown:
            raise ValueError(f"unknown scope {unknown}; expected any of {', '.join(SCOPES)}")
        return wanted

    def _filters(
        self, http: Fetcher, year: int, scopes: tuple[str, ...], only: set[str]
    ) -> Iterator[Filter]:
        if NATIONAL in scopes:
            yield Filter(NATIONAL)
        if PROVINCES not in scopes and GOVERNMENTS not in scopes:
            return

        # The portal's own list for the year, which has four more provinces
        # from 2022 on. The vendored table is the fallback when it will not
        # answer, not the source of truth.
        provinces = _listing(http, PROVINCES_URL.format(year=year)) or dict(djpk_apbd.PROVINCES)
        for code, name in sorted(provinces.items()):
            if only and code not in only:
                continue
            # "Provinsi Aceh" in recent years, "Prov. Aceh" in older ones.
            name = name.removeprefix("Provinsi ").removeprefix("Prov. ").strip()
            if PROVINCES in scopes:
                yield Filter(PROVINCES, provinsi=code, provinsi_name=name)
            if GOVERNMENTS in scopes:
                governments = _listing(http, PEMDA_URL.format(province=code, year=year))
                if not governments:
                    log.warning("djpk.no_governments", province=code, year=year)
                for pemda, pemda_name in sorted(governments.items()):
                    yield Filter(
                        GOVERNMENTS,
                        provinsi=code,
                        provinsi_name=name,
                        pemda=pemda,
                        pemda_name=pemda_name,
                    )

    def _year(
        self, http: Fetcher, region: Filter, year: int, months: list[int]
    ) -> Iterator[Artifact]:
        closed = months[0] > months[-1]
        previous: bytes | None = None
        for index, month in enumerate(months):
            response = http.try_get(
                djpk_apbd.BASE_URL,
                params={
                    "type": "apbd",
                    "periode": month,
                    "tahun": year,
                    "provinsi": region.provinsi,
                    "pemda": region.pemda,
                },
            )
            if response is None:
                continue
            content = response.content
            # A government with no report for the period is answered with the
            # sheet's header row and then the portal's error page, as one
            # body. Landing it would file an error page as a budget — and
            # every such answer is the same bytes, so all of them would
            # collapse into one document named after whichever came first.
            if b"<!DOCTYPE html" in content:
                log.info(
                    "djpk.no_report",
                    provinsi=region.provinsi,
                    pemda=region.pemda,
                    tahun=year,
                    periode=month,
                )
                continue

            # December and November identical: the portal has closed the year
            # and serves its year-end file for every month.
            if closed and index == 1 and previous == content and months[0] == 12:
                return
            previous = content

            yield Artifact(
                content=content,
                filename=region.filename(year, month),
                dataset=DATASETS[region.scope],
                source_url=djpk_apbd.BASE_URL,
                # SpreadsheetML: an XML dialect, not a real workbook.
                media_type="application/xml",
                partition=region.partition(year),
                metadata=region.metadata(year, month),
            )
