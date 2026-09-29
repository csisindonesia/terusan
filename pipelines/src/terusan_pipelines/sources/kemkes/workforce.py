"""Health workforce planning: the SATUSEHAT dashboard behind Kemenkes' SDMK plans.

SATUSEHAT's data room lists a dashboard called "Perencanaan Tenaga Medis dan
Tenaga Kesehatan", but the room holds only its catalogue entry. The dashboard
is an iframe onto `dreams.kemkes.go.id`, a PHP application of its own, and
that is where the figures are: how many health workers SISDMK counts at every
facility, which facilities lack a priority profession, how many workers each
province will need to 2032, and what its workload analysis says it is short.

It answers in three shapes, and each lands as served.

- **The workforce register** (`/empty-dataload/sebaran`) is a DataTables
  endpoint that answers whatever the register page's filter form last chose,
  which the server keeps in the session: a profession group (`pilihan`:
  doctors, health workers, support staff), a family within it (`rumpun`),
  and whether to count practice licences or people (`sip`). Chosen for a
  whole family and all of Indonesia, it answers one row per regency,
  profession and employment type with a count, as JSON. So each family is
  POSTed to the form and then read from the endpoint on the same client —
  seventy-odd pairs of requests — and each lands as its own artifact.

  People, not licences, are counted (`sip=2`, "Headcount berdasar Ijin
  Praktek"). The page's default counts practice licences, and a doctor may
  hold three, so its figures are not a number of doctors. The endpoint
  answers an empty body to a request without the session cookie and the
  headers a browser's XHR sends; both are sent.
- **The vacancy pages** (`/user/kekosongan_dev/...`) render one table each,
  server-side, per province: facilities with no worker of a priority
  profession, and the shortfall against the minimum staffing standard.
- **The planning pages** (`/user/demand/...`, `/user/abk/...`) are GET forms
  over a profession and, for the health-worker projections, a year. Each
  combination is a page of its own, so each lands as one.

What the dashboard does not put on the vacancy pages is which month they
describe. Its glossary says the cut-off is the first of every month, from
SISDMK, and the register page names the one it is showing ("Sumber : SISDMK 1
September 2026"). That date is read off the register page before anything else
and carried on every artifact of the run, because it is the only statement of
when these figures are true; a run that cannot find it refuses to land rather
than landing figures nobody can date.

Parsing is `extract/kemkes_workforce.py`'s. What this module reads off a page
is only what it must to know what to fetch next: the options of a form, and
whether a combination rendered a table at all — thirty of the three hundred
projections are professions the dashboard lists and has no figures for.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from html import unescape

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
from ..portals import PortalSource, provenance
from ..ratelimit import HostRateLimiter

log = structlog.get_logger(__name__)

SLUG = "kemkes-sdmk-planning"

PORTAL_URL = "https://satusehat.kemkes.go.id/data/dashboard/9a0069f5-1618-4c31-90ac-ebcd8df94d33"
BASE = "https://dreams.kemkes.go.id"

#: RAW and Bronze datasets, one per shape of figure.
DISTRIBUTION = "kemkes-sdmk-distribution"
VACANCIES = "kemkes-sdmk-vacancies"
DEMAND = "kemkes-sdmk-demand"
WORKLOAD = "kemkes-sdmk-workload"

XHR_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

#: A family of the register is up to ten thousand rows, which the ministry's
#: server takes its time over.
TIMEOUT_SECONDS = 180.0

#: The register's profession groups, as the filter form names them.
REGISTER_GROUPS = ("Named", "Nakes", "Penunjang")

#: Count people rather than practice licences.
HEADCOUNT = "2"

#: Where the register page names its SISDMK snapshot: "Sumber : SISDMK 1
#: September 2026".
_CUTOFF = re.compile(r"Sumber\s*:\s*SISDMK\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "januari",
            "februari",
            "maret",
            "april",
            "mei",
            "juni",
            "juli",
            "agustus",
            "september",
            "oktober",
            "november",
            "desember",
        ),
        start=1,
    )
}

_OPTION = re.compile(r'<option value="([^"]*)"[^>]*>\s*([^<]*?)\s*</option>')


@dataclass(frozen=True, slots=True)
class VacancyPage:
    """One vacancy table, and what the dashboard's menu calls it."""

    key: str
    path: str
    facility: str
    #: `kekosongan` counts facilities with no worker of a profession;
    #: `standar_minimal` the workers short of the minimum staffing standard.
    measure: str
    label: str


#: The pages the dashboard's menu links to. `PKM/standarpkm/2` is served as
#: well, but nothing links to it and its vacancy table is the 13-profession
#: one again. `RSKAB` is the per-hospital listing behind the hospital page.
VACANCY_PAGES: tuple[VacancyPage, ...] = (
    VacancyPage("rs", "user/kekosongan_dev/RS", "rs", "kekosongan", "Rumah Sakit"),
    VacancyPage("labkes", "user/kekosongan_dev/LABKES", "labkes", "kekosongan", "Labkesmas"),
    VacancyPage(
        "pkm_9",
        "user/kekosongan_dev/PKM/standarpkm/1",
        "pkm",
        "kekosongan",
        "Puskesmas, 9 jenis tenaga",
    ),
    VacancyPage(
        "pkm_13",
        "user/kekosongan_dev/PKM/standarpkm/3",
        "pkm",
        "kekosongan",
        "Puskesmas, 13 jenis tenaga",
    ),
    VacancyPage("pustu", "user/kekosongan_dev/PUSTU", "pustu", "kekosongan", "Pustu"),
    VacancyPage("skm_rs", "user/kekosongan_dev/SKMRS", "rs", "standar_minimal", "Rumah Sakit"),
    VacancyPage(
        "skm_labkes",
        "user/kekosongan_dev/SKMLABKES",
        "labkes",
        "standar_minimal",
        "Labkesmas",
    ),
    VacancyPage(
        "skm_pkm_13",
        "user/kekosongan_dev/SKMPKM/standarpkm/3",
        "pkm",
        "standar_minimal",
        "Puskesmas, 13 jenis tenaga",
    ),
    VacancyPage(
        "rs_hospitals",
        "user/kekosongan_dev/RSKAB",
        "rs",
        "kekosongan",
        "Rumah Sakit, per rumah sakit",
    ),
)


@dataclass(frozen=True, slots=True)
class FormPage:
    """A planning form, iterated over every profession it offers."""

    key: str
    path: str
    dataset: str
    #: The profession group, as the menu names it.
    group: str
    #: Whether the form also takes a year. Only the health-worker projection
    #: does; the doctors' projection shows one year and has no way to ask for
    #: another.
    by_year: bool


FORM_PAGES: tuple[FormPage, ...] = (
    FormPage("demand_nakes", "user/demand/nakes", DEMAND, "Tenaga Kesehatan", True),
    FormPage("demand_medis", "user/demand/named", DEMAND, "Tenaga Medis", False),
    FormPage("abk_pemda", "user/abk/pemda", WORKLOAD, "Pemerintah Daerah", False),
    FormPage("abk_kl", "user/abk/kl", WORKLOAD, "Kementerian dan Lembaga Pusat", False),
)


class DashboardChanged(RuntimeError):
    """The dashboard answered in a shape this source does not recognise."""


def sisdmk_cutoff(page: str) -> date | None:
    """The SISDMK snapshot a page names, where it names one."""
    match = _CUTOFF.search(unescape(page))
    if not match:
        return None
    day, month, year = match.groups()
    number = _MONTHS.get(month.lower())
    if number is None:
        return None
    return date(int(year), number, int(day))


def options(page: str, name: str) -> list[tuple[str, str]]:
    """A form's `<select name=...>` choices, as (value, label), blanks dropped."""
    start = page.find(f'name="{name}"')
    if start < 0:
        return []
    segment = page[start:]
    end = segment.find("</select>")
    if end >= 0:
        segment = segment[:end]
    return [
        (value, " ".join(unescape(label).split()))
        for value, label in _OPTION.findall(segment)
        if value != ""
    ]


def has_table(page: str) -> bool:
    """Whether a page rendered the dashboard's main table at all."""
    return 'id="dtone"' in page


class WorkforcePlanning(PortalSource):
    """SATUSEHAT's health workforce planning dashboard, page by page."""

    meta = SourceMeta(
        slug=SLUG,
        name="Kemenkes — Perencanaan Tenaga Medis dan Tenaga Kesehatan (SATUSEHAT)",
        organization="Kementerian Kesehatan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=f"{BASE}/",
        license="Public sector information",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=2.0,
        schedule="0 5 3 * *",
        notes=(
            "The health workforce planning dashboard SATUSEHAT links to: the "
            "SISDMK register of health workers per regency, profession and "
            "employment type, as a headcount; facilities with no worker of a priority "
            "profession and the shortfall against minimum staffing standards, "
            "per province; supply-demand projections of need per profession "
            "and province to 2032; and the workload analysis of regional and "
            "central government. About 600 requests a run.\n\n"
            f"Catalogue entry: {PORTAL_URL}"
        ),
    )

    timeout = TIMEOUT_SECONDS

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        # The declared ceiling, enforced: `max_requests_per_second` is only
        # advisory until a source hands a limiter to its own client.
        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)
        landed = 0
        with fetcher(headers=self.request_headers(), timeout=self.timeout, limiter=limiter) as http:
            http.get(f"{BASE}/")
            for artifact in self._artifacts(http):
                if ctx.limit is not None and landed >= ctx.limit:
                    return
                yield artifact
                landed += 1

    def _artifacts(self, http: Fetcher) -> Iterator[Artifact]:
        register_page = f"{BASE}/user/sebaran/"
        page = http.get(register_page)
        cutoff = sisdmk_cutoff(page.text)
        if cutoff is None:
            raise DashboardChanged(
                f"{register_page}: no 'Sumber : SISDMK <date>' on the page, so "
                "nothing this run lands could be dated"
            )
        partition = (f"year={cutoff.year}", f"month={cutoff.month:02d}")
        dated = {"sisdmk_cutoff": cutoff.isoformat()}

        yield from self._register(http, register_page, cutoff, partition, dated)

        for vacancy in VACANCY_PAGES:
            response = http.try_get(f"{BASE}/{vacancy.path}")
            if response is None or not has_table(response.text):
                log.warning("kemkes_sdmk.vacancy_page_missing", path=vacancy.path)
                continue
            yield _artifact(
                response,
                dataset=VACANCIES,
                filename=f"{vacancy.key}.html",
                partition=partition,
                published_at=cutoff,
                metadata={
                    **dated,
                    "page": vacancy.key,
                    "facility": vacancy.facility,
                    "measure": vacancy.measure,
                    "label": vacancy.label,
                },
            )

        for form in FORM_PAGES:
            yield from self._form(http, form, partition, dated)

    def _register(
        self,
        http: Fetcher,
        page: str,
        cutoff: date,
        partition: tuple[str, ...],
        dated: dict[str, str],
    ) -> Iterator[Artifact]:
        url = f"{BASE}/empty-dataload/sebaran"
        reached = 0
        for group in REGISTER_GROUPS:
            chosen = http.post(page.rstrip("/"), data=_register_form(group, ""))
            families = [value for value, _ in options(chosen.text, "rumpun")]
            if not families:
                log.warning("kemkes_sdmk.register_group_empty", group=group)
                continue
            for family in families:
                # The filter lives in the session, so the endpoint must be read
                # right after the form that set it, on the same client.
                http.post(page.rstrip("/"), data=_register_form(group, family))
                response = http.get(url, headers={**XHR_HEADERS, "Referer": page})
                rows = _register_rows(url, response)
                if not rows:
                    log.info("kemkes_sdmk.register_family_empty", group=group, family=family)
                    continue
                reached += 1
                yield _artifact(
                    response,
                    dataset=DISTRIBUTION,
                    filename=f"sebaran-{group.lower()}-{_slug(family)}.json",
                    media_type="application/json",
                    partition=partition,
                    published_at=cutoff,
                    metadata={
                        **dated,
                        "page": "sebaran",
                        "group": group,
                        "family": family,
                        "count": "headcount",
                        "rows": rows,
                    },
                )
        if reached == 0:
            # Every family empty is the endpoint refusing the session, not a
            # month in which Indonesia had no health workers.
            raise DashboardChanged(f"{url}: no family of the register answered with rows")

    def _form(
        self,
        http: Fetcher,
        form: FormPage,
        partition: tuple[str, ...],
        dated: dict[str, str],
    ) -> Iterator[Artifact]:
        page = http.try_get(f"{BASE}/{form.path}")
        if page is None:
            return
        professions = options(page.text, "nakes")
        if not professions:
            # A form with nothing to choose is the page changing shape, and
            # only this family of figures is lost to it.
            log.warning("kemkes_sdmk.form_without_options", path=form.path)
            return
        years: list[str | None] = [None]
        if form.by_year:
            years = [v for v, _ in options(page.text, "tahun") if re.fullmatch(r"\d{4}", v)]
            if not years:
                log.warning("kemkes_sdmk.form_without_years", path=form.path)
                return

        empty = 0
        for year in years:
            for value, label in professions:
                params = {"nakes": value, "bsubmit": "Tampilkan"}
                if year:
                    params["tahun"] = year
                response = http.try_get(f"{BASE}/{form.path}", params=params)
                if response is None or not has_table(response.text):
                    empty += 1
                    continue
                suffix = f"-{year}" if year else ""
                yield _artifact(
                    response,
                    dataset=form.dataset,
                    filename=f"{form.key}-{value}{suffix}.html",
                    partition=partition,
                    metadata={
                        **dated,
                        "page": form.key,
                        "group": form.group,
                        "profession": label,
                        "profession_code": value,
                        **({"year": year} if year else {}),
                    },
                )
        if empty:
            log.info("kemkes_sdmk.combinations_without_table", path=form.path, count=empty)


def _register_form(group: str, family: str) -> dict[str, str]:
    """The register page's filter: one family, all of Indonesia, headcount."""
    return {
        "kab": "",
        "pilihan": group,
        "rumpun": family,
        "sdmk": "0",
        "tipe": "",
        "sip": HEADCOUNT,
    }


def _register_rows(url: str, response: httpx.Response) -> int:
    """How many rows the register answered, refusing what is not its JSON.

    Landing would take an empty body, or an HTML error page, as a `.json`:
    JSON has no signature to check.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise DashboardChanged(f"{url}: not JSON ({len(response.content)} bytes)") from exc
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise DashboardChanged(f"{url}: no data list in the register's answer")
    expected = body.get("recordsTotal")
    if expected is not None and int(expected) != len(rows):
        raise DashboardChanged(f"{url}: {len(rows)} rows where recordsTotal said {expected}")
    return len(rows)


def _slug(label: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", label.lower()).strip("-") or "all"


def _artifact(
    response: httpx.Response,
    *,
    dataset: str,
    filename: str,
    partition: tuple[str, ...],
    metadata: dict,
    media_type: str = "text/html",
    published_at: date | None = None,
) -> Artifact:
    return Artifact(
        content=response.content,
        filename=filename,
        dataset=dataset,
        source_url=str(response.url),
        media_type=media_type,
        published_at=published_at,
        partition=partition,
        metadata={**metadata, **provenance(response)},
    )
