"""GDELT 2.0 — world news events, fifteen minutes at a time.

GDELT codes the world's news into events, the mentions of those events, and a
knowledge graph of the themes and people in them, and republishes all three
every fifteen minutes. `lastupdate.txt` names the current triple, which is why
this source reads that file rather than composing a timestamped URL: composing
one means guessing which quarter-hour has finished publishing, and guessing
wrong lands a 404 every fourth run.

Only the events and the mentions are landed by default. The knowledge graph
file is thirty times their size — six megabytes a quarter-hour, a third of a
terabyte a year — and nothing downstream reads it yet, so it is collected only
when a run asks for it with `params={"gkg": True}`.

The files are global. Indonesia is selected at extraction, for the same reason
as UCDP: what GDELT publishes is one file, and a source that filtered it here
would land something GDELT never published and could not be re-filtered when
the question changes.
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
from ..http import Fetcher
from ..portals import ApiSource, Endpoint, filename_of, wants

LAST_UPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

#: What each of the three lines is. `lastupdate.txt` is three lines of
#: `size hash url`, in this order, and the kind is read off the filename
#: rather than the line number — the order has been stable for a decade, and
#: reading the name costs nothing and survives it changing.
KINDS = {
    "export": ("events", "Events: one row per coded event, CAMEO-classified"),
    "mentions": ("mentions", "Mentions: every article mentioning each event"),
    "gkg": ("knowledge-graph", "GKG: themes, persons, organizations and tone"),
}

#: Landed only when asked for. See the module docstring.
OPTIONAL = {"gkg"}


def current_files(body: str) -> tuple[tuple[str, str], ...]:
    """The `(kind, url)` pairs named by `lastupdate.txt`.

    Lines that are not `size hash url` are skipped rather than failing the run:
    GDELT occasionally serves a maintenance notice in this file, and a run that
    raised on it would page someone at three in the morning about a file that
    will be correct at quarter past.
    """
    found: list[tuple[str, str]] = []
    for line in body.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[2].startswith("http"):
            continue
        url = parts[2]
        name = filename_of(url).lower()
        for key in KINDS:
            if f".{key}." in name:
                found.append((key, url))
                break
    return tuple(found)


class GlobalEvents(ApiSource):
    """The current quarter-hour's events and mentions."""

    meta = SourceMeta(
        slug="gdelt-events",
        name="GDELT 2.0 — Events and Mentions",
        organization="The GDELT Project",
        category=Category.NEWS,
        source_type=SourceType.NEWS,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url="https://www.gdeltproject.org/",
        # Global. Indonesia is selected at extraction.
        country=None,
        license="CC-BY-4.0",
        update_frequency=UpdateFrequency.REALTIME,
        # GDELT asks for one request every five seconds; this takes three.
        max_requests_per_second=0.2,
        # Hourly, which lands four of the twenty-four files a day publishes.
        # The rest are still on GDELT's server, and a backfill is a run with
        # its own file list rather than a reason to poll every quarter-hour.
        schedule="12 * * * *",
        notes=(
            "Reads lastupdate.txt and lands the files it names. Events and "
            "mentions by default; the knowledge graph with params={'gkg': True}."
        ),
    )

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        listing = http.get(LAST_UPDATE)
        files = current_files(listing.text)
        if not files:
            raise ValueError(f"{LAST_UPDATE} named no files; it served: {listing.text[:200]!r}")

        want_gkg = wants(ctx, "gkg")

        for kind, url in files:
            if kind in OPTIONAL and not want_gkg:
                continue
            dataset, description = KINDS[kind]
            name = filename_of(url)
            # `20260922164500.export.CSV.zip` — the stamp is the quarter-hour
            # the file covers, and the year in it is the partition.
            stamp = name.split(".")[0]
            yield Endpoint(
                dataset=f"gdelt-{dataset}",
                url=url,
                filename=name,
                media_type="application/zip",
                partition=(f"year={stamp[:4]}",) if stamp[:4].isdigit() else (),
                metadata={
                    "kind": kind,
                    "coverage": description,
                    "timestamp": stamp,
                    "listing_url": LAST_UPDATE,
                },
            )
