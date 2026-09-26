"""BPS — the figures behind every variable in the national catalogue.

`bps-webapi` lands the catalogue: 1,753 variables for the national domain,
across 51 subjects. This lands the numbers behind all of them, straight from
the publisher rather than second-hand through Trading Economics or Kemendagri's
profile page.

**How a variable is asked for.** `list/model/data` takes the variable and the
years, and BPS allows at most three years a request — a range of ten answers
`"The maximum allowed number of years for the 'th' parameter is 3"`. Years are
BPS's own ids, which are the year less 1900 (126 is 2026); `list/model/th`
lists the ones a variable has, paged ten at a time.

**What a run asks for.** Without parameters, the last two years of every
variable: one request each, because two year ids need no year list. That is
the monthly run, about an hour at BPS's pace — a year of overlap because BPS
revises, and last year's figure is final only once this year's is out. `since`
widens the window back to the year before it. `--param full=true` walks each
variable's own year list back to its first year, which is the backfill: some
nine thousand requests, several hours. `--param vars=543,192` narrows any of
these to a few variables.

**One bad variable is not a bad run.** A table that errors is logged and
skipped, and the run fails only when more than a tenth of them do — at which
point it is BPS that is down, not one table.

The key is in the URL path, as for the catalogue source, and is taken out of
what lands for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import httpx
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
from ..portals import BROWSER_HEADERS, Endpoint, wants
from ..ratelimit import HostRateLimiter
from .webapi import (
    API,
    NATIONAL,
    BpsError,
    api_key,
    catalogue_url,
    get_json,
    page_count,
    redacted,
)

log = structlog.get_logger(__name__)

#: The Bronze dataset every variable lands under. One collection, not one per
#: variable: the extractor names the series on every row, and normalization
#: splits them with `normalize-each --by indicator`.
DATASET = "bps-indicators"

#: The most years BPS answers in one request.
YEARS_PER_REQUEST = 3

#: BPS's year ids are the calendar year less this.
YEAR_ID_OFFSET = 1900

#: Above this share of variables failing, the run fails.
MAX_FAILURE_RATIO = 0.10


@dataclass(frozen=True, slots=True)
class Variable:
    var_id: int
    title: str = ""


def years_url(var_id: int, key: str, page: int = 1) -> str:
    return f"{API}/list/model/th/lang/ind/domain/{NATIONAL}/var/{var_id}/page/{page}/key/{key}/"


def data_url(var_id: int, year_ids: list[int], key: str) -> str:
    years = ";".join(str(y) for y in year_ids)
    return f"{API}/list/model/data/lang/ind/domain/{NATIONAL}/var/{var_id}/th/{years}/key/{key}/"


class Indicators(Source):
    """Every national-domain variable, for the years the run asks for."""

    meta = SourceMeta(
        slug="bps-indicators",
        name="BPS — tabel statistik",
        organization="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://webapi.bps.go.id/",
        license="BPS terms of use — attribution required",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        # Inflation is released on the first working day of the month, the
        # rest on their own calendars; the second catches the first.
        schedule="0 4 2 * *",
        notes=(
            "Needs BPS_API_KEY. Every variable in the national catalogue, the "
            "last two years by default; --param full=true for each variable's "
            "whole history (several hours), --param vars=543,192 for a few. "
            "Three years per request, which is BPS's ceiling."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        key = api_key()
        full = wants(ctx, "full")
        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)
        landed = 0
        failed: list[int] = []
        with fetcher(headers=dict(BROWSER_HEADERS), timeout=120.0, limiter=limiter) as http:
            variables = self._variables(ctx, http, key)
            log.info("bps.variables", count=len(variables), full=full)
            for number, variable in enumerate(variables, start=1):
                try:
                    for artifact in self._tables(ctx, http, key, variable, full=full):
                        if ctx.limit is not None and landed >= ctx.limit:
                            return
                        yield artifact
                        landed += 1
                except (
                    BpsError,
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    TypeError,
                    IndexError,
                ) as exc:
                    failed.append(variable.var_id)
                    log.warning(
                        "bps.variable_failed",
                        var_id=variable.var_id,
                        error=str(exc).replace(key, "{key}")[:200],
                    )
                if number % 100 == 0:
                    log.info("bps.progress", done=number, of=len(variables), landed=landed)

        if variables and len(failed) / len(variables) > MAX_FAILURE_RATIO:
            raise BpsError(
                f"{len(failed)} of {len(variables)} variables failed, first {failed[:10]}"
            )

    def _variables(self, ctx: ScrapeContext, http: Fetcher, key: str) -> list[Variable]:
        """The variables this run covers: those asked for, or the catalogue."""
        asked = str(ctx.params.get("vars") or "").strip()
        if asked:
            return [Variable(int(v)) for v in asked.split(",") if v.strip()]

        found: dict[int, Variable] = {}
        page, pages = 1, 1
        while page <= pages:
            _, body = get_json(http, catalogue_url("var", key, page=page), key)
            pages = page_count(body)
            for entry in body["data"][1]:
                found[int(entry["var_id"])] = Variable(int(entry["var_id"]), entry.get("title", ""))
            page += 1
        return sorted(found.values(), key=lambda v: v.var_id)

    def _tables(
        self, ctx: ScrapeContext, http: Fetcher, key: str, variable: Variable, *, full: bool
    ) -> Iterator[Artifact]:
        if full:
            years = self._years(http, variable.var_id, key)
        else:
            this_year = date.today().year
            # A year before `since`, or before this year: BPS revises, and last
            # year's figure is final only once this year's is out.
            start = (ctx.since.year if ctx.since else this_year) - 1
            years = {y - YEAR_ID_OFFSET: y for y in range(start, this_year + 1)}

        ids = sorted(years)
        for start in range(0, len(ids), YEARS_PER_REQUEST):
            chunk = ids[start : start + YEARS_PER_REQUEST]
            first, last = years[chunk[0]], years[chunk[-1]]
            endpoint = Endpoint(
                dataset=DATASET,
                url=data_url(variable.var_id, chunk, key),
                filename=f"var-{variable.var_id}-{first}-{last}.json",
                partition=(f"var={variable.var_id}",),
                metadata={"var_id": variable.var_id, "years": [years[t] for t in chunk]},
            )
            response, body = get_json(http, endpoint.url, key)
            if body.get("data-availability") != "available":
                # Years with nothing published. Landing an empty table would
                # count as a collection that happened.
                continue
            yield redacted(endpoint.artifact(response), key)

    @staticmethod
    def _years(http: Fetcher, var_id: int, key: str) -> dict[int, int]:
        """BPS's year ids for a variable, mapped to the calendar year."""
        years: dict[int, int] = {}
        page, pages = 1, 1
        while page <= pages:
            _, body = get_json(http, years_url(var_id, key, page), key)
            if body.get("data-availability") != "available":
                break
            pages = page_count(body)
            for entry in body["data"][1]:
                years[int(entry["th_id"])] = int(entry["th"])
            page += 1
        return years
