"""National holidays and collective leave, as the three ministers decree them.

Each year the Ministers of Religious Affairs, of Manpower and of State
Apparatus sign a joint decree (Keputusan Bersama Menteri, the "SKB 3 Menteri")
fixing the coming year's national holidays and cuti bersama. The dates of the
lunar holidays — Idul Fitri, Idul Adha, Isra Mikraj — move by about eleven days
a year, and the cuti bersama around them are a political choice made fresh
each time; neither can be computed, only read off the decree. It is also
amended: 2020's was changed four times as the pandemic moved Lebaran's leave to
December and then cancelled it.

KemenPANRB's JDIH publishes every one since 2019 (the 2020 calendar onwards).
The portal is a Laravel Livewire application. Its search page renders the
first four results into the HTML as a component snapshot —

    <div wire:snapshot="{&quot;data&quot;:{&quot;data&quot;:[[[{&quot;id&quot;:2131, …

— and answers the next page only to the component itself:

    POST /livewire/update
    {"_token": <csrf>, "components": [{"snapshot": <snapshot>, "updates": {},
      "calls": [{"path": "", "method": "searchDokumen", "params": [2]}]}]}

A document's page, `/dokumen-hukum/{slug}`, carries the same kind of snapshot
with the attachment's address on the publisher's file host:

    https://data-jdih.menpan.go.id/dokumen/2026skb002.pdf

The search is filtered to joint decrees, which also takes in the ministers'
decrees on civil servants' neutrality in elections; a decree is kept when its
title is about holidays. Titles are typed with a Cyrillic `а` in "Bersamа" on
some records, so they are compared after folding that back to Latin.

Two artifacts land per decree: the document page, which is the publisher's own
record of its number, date and status, and the PDF. The PDFs are scans with no
text layer; `extract/menpan.py` reads their tables by OCR.

An amendment restates the whole annex rather than listing changes, so a year's
calendar is the annex of the latest decree for that year. That is Silver's to
apply; every version lands here.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

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

log = structlog.get_logger(__name__)

SITE = "https://jdih.menpan.go.id"
SEARCH_URL = f"{SITE}/dokumen-hukum/hasil-pencarian"
UPDATE_URL = f"{SITE}/livewire/update"

#: The search filter the portal's own form sends for joint decrees.
DECREE_KIND = "keputusan bersama menteri"

#: The Livewire component that renders search results.
RESULTS_COMPONENT = "dokumen.dokumen-search-result"

#: The first year the portal holds a holiday decree for (signed 2019, for the
#: 2020 calendar). Earlier calendars were published elsewhere.
FIRST_DECREE_YEAR = 2019

#: Letters from other scripts that the portal's titles use for Latin ones.
_CONFUSABLES = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "і": "i"})

_SNAPSHOT = re.compile(r'wire:snapshot="([^"]*)"')
_CSRF = re.compile(r'data-csrf="([^"]*)"')
_TARGET_YEAR = re.compile(r"cuti bersama tahun (\d{4})", re.IGNORECASE)


def plain_title(title: str) -> str:
    """The title in plain Latin letters and single spaces."""
    folded = unicodedata.normalize("NFKC", title).translate(_CONFUSABLES)
    return " ".join(folded.split())


def is_holiday_decree(title: str) -> bool:
    return "hari libur" in plain_title(title).lower()


def target_year(title: str) -> int | None:
    """The calendar year a decree fixes — not the year it was signed."""
    found = _TARGET_YEAR.search(plain_title(title))
    return int(found.group(1)) if found else None


@dataclass(frozen=True, slots=True)
class Decree:
    """One search result, as the portal's record names it."""

    id: int
    slug: str
    title: str
    number: str | None
    enacted: date | None
    status: str | None

    @property
    def page_url(self) -> str:
        return f"{SITE}/dokumen-hukum/{self.slug}"


def snapshots(page: str) -> list[dict[str, Any]]:
    """Every Livewire component snapshot a page carries, decoded."""
    return [json.loads(html.unescape(raw)) for raw in _SNAPSHOT.findall(page)]


def _component(page: str, name: str) -> dict[str, Any] | None:
    for snapshot in snapshots(page):
        if snapshot.get("memo", {}).get("name") == name:
            return snapshot
    return None


def _decrees(snapshot: dict[str, Any]) -> list[Decree]:
    # Livewire writes a PHP array as `[items, {"s": "arr"}]`, and each item
    # the same way: the records are `data.data[0][n][0]`.
    rows = snapshot["data"]["data"][0]
    found = []
    for row in rows:
        record = row[0]
        enacted = record.get("tanggal_penetapan")
        found.append(
            Decree(
                id=int(record["id"]),
                slug=str(record["slug"]),
                title=plain_title(str(record.get("judul") or "")),
                number=record.get("nomor_peraturan"),
                enacted=date.fromisoformat(enacted) if enacted else None,
                status=record.get("status"),
            )
        )
    return found


def decrees_in(page: str) -> tuple[list[Decree], int]:
    """The decrees a search page lists, and how many pages there are."""
    snapshot = _component(page, RESULTS_COMPONENT)
    if snapshot is None:
        raise ValueError(f"page carries no {RESULTS_COMPONENT} component")
    return _decrees(snapshot), int(snapshot["data"].get("totalPage") or 1)


def attachments_in(page: str) -> list[str]:
    """The attachment URLs a document page names, in the order it names them."""
    urls: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            url = value.get("dokumen_lampiran")
            if isinstance(url, str) and url and url not in urls:
                urls.append(url)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for snapshot in snapshots(page):
        walk(snapshot.get("data"))
    return urls


def list_decrees(http: Fetcher) -> list[Decree]:
    """Every joint decree the portal lists, following its pages."""
    first = http.get(SEARCH_URL, params={"jenis": DECREE_KIND})
    page = first.text
    decrees, pages = decrees_in(page)
    if pages == 1:
        return decrees
    csrf = _CSRF.search(page)
    if csrf is None:
        raise ValueError("search page carries no CSRF token to page with")

    # The token is valid for the session cookie the first page set, which the
    # client keeps.
    state = html.unescape(_raw_results_snapshot(page))
    for number in range(2, pages + 1):
        response = http.post(
            UPDATE_URL,
            json={
                "_token": csrf.group(1),
                "components": [
                    {
                        "snapshot": state,
                        "updates": {},
                        "calls": [{"path": "", "method": "searchDokumen", "params": [number]}],
                    }
                ],
            },
            headers={"X-Livewire": "", "Referer": str(first.url)},
        )
        component = response.json()["components"][0]
        decrees.extend(_decrees(json.loads(component["snapshot"])))
    return decrees


def _raw_results_snapshot(page: str) -> str:
    # Sent back byte-for-byte: Livewire checks the snapshot against its
    # checksum, and a re-serialised copy would not match.
    for raw in _SNAPSHOT.findall(page):
        if json.loads(html.unescape(raw)).get("memo", {}).get("name") == RESULTS_COMPONENT:
            return str(raw)
    raise ValueError(f"page carries no {RESULTS_COMPONENT} component")


class NationalHolidays(Source):
    """Every SKB 3 Menteri on national holidays and cuti bersama, 2019 on."""

    meta = SourceMeta(
        slug="menpan-hari-libur",
        name="Hari Libur Nasional dan Cuti Bersama — SKB 3 Menteri",
        organization="Kementerian Pendayagunaan Aparatur Negara dan Reformasi Birokrasi",
        category=Category.REGULATIONS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SEARCH_URL,
        license="Public domain — Indonesian law is not subject to copyright",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        # The next year's decree is signed in September or October and
        # amendments come whenever; weekly finds either within days.
        schedule="0 6 * * 1",
        notes=(
            "Joint decrees of the Ministers of Religious Affairs, Manpower and "
            "State Apparatus fixing each year's national holidays and cuti "
            "bersama, from JDIH KemenPANRB (Livewire). Lands each decree's "
            "document page and its scanned PDF; amendments restate the whole "
            "annex, so the latest decree for a year is its calendar."
        ),
    )

    dataset = "skb-hari-libur"
    pages_dataset = "skb-hari-libur-pages"

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        landed = 0
        with fetcher() as http:
            decrees = [d for d in list_decrees(http) if is_holiday_decree(d.title)]
            log.info("menpan.holiday_decrees", found=len(decrees))
            for decree in decrees:
                # A decree amended since would carry a new record of its own,
                # so one enacted before `since` has nothing new to say.
                if ctx.since and decree.enacted and decree.enacted < ctx.since:
                    continue
                if ctx.limit is not None and landed >= ctx.limit:
                    return
                page = http.get(decree.page_url)
                metadata = {
                    "decree_id": decree.id,
                    "title": decree.title,
                    "number": decree.number,
                    "enacted": decree.enacted.isoformat() if decree.enacted else None,
                    "status": decree.status,
                    "target_year": target_year(decree.title),
                    "amends": "perubahan" in decree.title.lower(),
                    "page_url": decree.page_url,
                }
                partition = (f"year={metadata['target_year'] or 'unknown'}",)
                yield Artifact(
                    content=page.content,
                    filename=f"skb-{decree.id}.html",
                    dataset=self.pages_dataset,
                    source_url=decree.page_url,
                    media_type="text/html",
                    published_at=decree.enacted,
                    partition=partition,
                    metadata=metadata,
                )
                urls = attachments_in(page.text)
                if not urls:
                    log.warning("menpan.no_attachment", decree=decree.id, title=decree.title)
                for url in urls:
                    document = http.get(url)
                    yield Artifact(
                        content=document.content,
                        filename=url.rsplit("/", 1)[-1],
                        dataset=self.dataset,
                        source_url=url,
                        media_type="application/pdf",
                        published_at=decree.enacted,
                        partition=partition,
                        metadata=metadata,
                    )
                landed += 1
