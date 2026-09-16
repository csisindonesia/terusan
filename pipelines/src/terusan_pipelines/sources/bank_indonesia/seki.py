"""SEKI — Bank Indonesia's monthly economic and financial statistics.

Statistik Ekonomi dan Keuangan Indonesia: an index page fanning out to roughly
108 Excel tables. Ported from an earlier warehouse, where three properties of
this source shaped the code and still do.

Bank Indonesia publishes legacy BIFF `.xls`, not `.xlsx`, and its endpoints are
intermittently flaky — a table that answers 302 with an empty body on one
request serves 130 kB of Excel on the next. So the bytes are checked against the
OLE2 signature at landing, and an HTML error page named `TABEL1_1.xls` is
refused rather than parsed months later.

A transient failure must not discard the other 107 tables, so per-table failures
are collected rather than raised. But a site-wide outage looks exactly like one
flaky table, one table at a time — so the run fails below a success ratio, and a
partial month never masquerades as a complete one.

The index page is landed alongside the tables. It is the only record of what was
offered on the day, and a bare `.xls` carries neither its title nor its section.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

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
from ..sniff import looks_like_html, matches_extension
from .seki_index import SekiTable, extract_tables

INDEX_URL = "https://www.bi.go.id/id/statistik/ekonomi-keuangan/seki/Default.aspx"

#: Below this share of tables the run fails. One dead link is noise; half the
#: site failing is an outage, and a partial month must not look like a full one.
MIN_SUCCESS_RATIO = 0.90

#: Tables fetched at once. One server, so this stays modest: high enough that
#: 108 sequential round-trips stop dominating the run, low enough to read as one
#: polite client rather than a stampede. The per-host limiter applies on top.
FETCH_CONCURRENCY = 8

#: Some tables run to a couple of megabytes and the site is not fast.
TIMEOUT_SECONDS = 120.0


class PartialRelease(RuntimeError):
    """Too few tables landed for the month to be treated as published."""


class Seki(Source):
    meta = SourceMeta(
        slug="bi-seki",
        name="SEKI — Statistik Ekonomi dan Keuangan Indonesia",
        organization="Bank Indonesia",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=INDEX_URL,
        license="Bank Indonesia terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=2.0,
        # SEKI lands mid-month for the prior month, so check daily from the
        # 10th rather than guessing one release date.
        schedule="0 4 10-20 * *",
        notes="Ported from the lake warehouse. ~108 legacy .xls tables per month.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher(timeout=TIMEOUT_SECONDS) as http:
            index = http.get(INDEX_URL)

            yield Artifact(
                content=index.content,
                filename="index.html",
                dataset="index",
                source_url=str(index.url),
                media_type="text/html",
            )

            tables = extract_tables(index.content, str(index.url), limit=ctx.limit)
            if not tables:
                raise ValueError(f"no Excel tables found on {INDEX_URL}")

            # The catalogue travels with the data: titles and section names live
            # only on the index, and a raw .xls carries neither.
            yield Artifact(
                content=json.dumps(
                    [
                        {
                            "table_id": t.table_id,
                            "number": t.number,
                            "title": t.title,
                            "section": t.section,
                            "url": t.url,
                        }
                        for t in tables
                    ],
                    ensure_ascii=False,
                    indent=2,
                ).encode(),
                filename="catalogue.json",
                dataset="index",
                source_url=str(index.url),
                media_type="application/json",
                metadata={"tables": len(tables)},
            )

            fetched, failures = self._fetch_all(http, tables)

            # Emitted in index order rather than completion order, so a month's
            # artifacts are laid out the same way however the requests raced.
            for table in tables:
                response = fetched.get(table.table_id)
                if response is None:
                    continue
                yield Artifact(
                    content=response.content,
                    filename=f"{table.table_id}.xls",
                    dataset="tables",
                    source_url=str(response.url),
                    media_type="application/vnd.ms-excel",
                    metadata={
                        "table_id": table.table_id,
                        "number": table.number,
                        "title": table.title,
                        "section": table.section,
                    },
                )

            self._check_completeness(len(tables), failures)

    # ---- internals -----------------------------------------------------

    def _fetch_all(self, http, tables: list[SekiTable]) -> tuple[dict, list[str]]:
        fetched: dict = {}
        failures: list[str] = []

        with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as pool:
            futures = {pool.submit(self._fetch_table, http, table): table for table in tables}
            for future in as_completed(futures):
                table = futures[future]
                response = future.result()
                if response is None:
                    failures.append(table.table_id)
                else:
                    fetched[table.table_id] = response

        return fetched, failures

    @staticmethod
    def _fetch_table(http, table: SekiTable):
        """One table, or None. Never raises: one bad link is not a failed month.

        The content checks happen here as well as at landing. A 302 to a
        friendly error page arrives as a 200 with HTML in it, and catching it
        now is what makes the success ratio below meaningful — otherwise a
        hundred error pages would count as a hundred successes.
        """
        response = http.try_get(table.url)
        if response is None or not response.content:
            return None
        if looks_like_html(response.content) or not matches_extension(response.content, "xls"):
            return None
        return response

    @staticmethod
    def _check_completeness(total: int, failures: list[str]) -> None:
        landed = total - len(failures)
        ratio = landed / total if total else 1.0
        if ratio < MIN_SUCCESS_RATIO:
            raise PartialRelease(
                f"only {landed}/{total} SEKI tables landed ({ratio:.0%} < "
                f"{MIN_SUCCESS_RATIO:.0%}) — treating as an upstream outage rather "
                "than a published month"
            )
