"""The crawl: read the papers, keep what might be news of something.

One run visits a shard of the outlet list, asks each outlet for the profile's
search terms, fetches what comes back, and lands the page. Nothing is coded
here — coding is extraction's job, from the bytes this leaves in RAW — and that
separation is what lets a coding be redone later against an article that has
since been edited, paywalled or deleted.

**Sharding.** Sixty-seven outlets, each with a couple of dozen searches and a
screenshot per article that matters, does not fit in a scheduler slot. So the
list is cut into shards and one shard runs per firing, with the shard chosen by
the hour. A missed shard is not chased: the collection window is a week, so
tomorrow's firing at the same hour collects what today's would have.

**What "seen" means without a database.** There is no crawl state anywhere —
the decision was that RAW and Bronze are the only record. A URL's landing
directory is therefore made predictable: the hash of the URL is a partition
segment, so asking whether an article has been collected is asking whether a
directory exists, before anything is fetched. Content addressing still applies
underneath it, so an article that was edited since lands a second file beside
the first rather than overwriting it.

**What is kept, and what is only counted.** Every article an outlet published
in the window is read. Almost none of it is collective violence — a provincial
paper's day is council meetings, football and the price of chillies — so almost
none of it is kept. An article is landed only if it passes the keyword
dictionary and then the classifier's gate; everything else leaves no trace but
a number.

That is a deliberate trade and it costs something real. The counts say how many
articles a paper published and how many were violence, which is the denominator
every rate needs and which a keyword-bounded crawl could never produce. What is
lost is the ability to widen the lexicon and re-examine what was passed over:
those pages were never stored, and reaching them again means going back to an
outlet that may have moved them.

There is deliberately no override. A flag that stored everything "just for this
period" would make the corpus mean two things depending on when a row landed,
and nothing downstream could tell which — a count of articles about violence
would silently become a count of articles. Collect the issue or collect a
number.

**When the classifier cannot be reached** the gate falls back to the dictionary
alone and the article is landed as a candidate. Dropping articles on a
classifier's silence would put a hole in the record that looks exactly like a
quiet week.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from ...news.dictionary import Match
from ...news.jev import JevUnavailable
from ...news.jev import shared as jev
from ...news.outlets import Outlet, active_outlets
from ...news.profiles import Profile
from ...news.profiles import profile as get_profile
from ...storage import Layer, StorageConfig, StorageResolver
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
from ..ratelimit import HostRateLimiter
from . import browser
from .article import fetch as fetch_article
from .article import in_window
from .discover import discover

log = structlog.get_logger(__name__)

#: How far back a routine run collects. A week rather than a day so a missed
#: firing, a shard that failed and an outlet that was down all heal themselves
#: on the next run instead of leaving a hole nobody notices.
WINDOW_DAYS = 7

#: How many shards the outlet list is cut into. Twelve firings of five or six
#: outlets each, which is a working day.
SHARDS = 12

#: Politeness. One request per second per host, two in a burst so a listing
#: page and its first article do not wait on each other.
REQUESTS_PER_SECOND = 1.0
BURST = 2

#: How many times one article is tried before moving on.
#:
#: Two, against the five the shared client defaults to. That default is right
#: for a statistical release published once a quarter, where a retry is cheaper
#: than waiting three months. A newspaper publishes again tomorrow and the
#: window is a week, so a page that fails twice is better abandoned than paid
#: for with backoff — five attempts against a host that hangs rather than
#: refuses costs minutes per article, and there are sixty of them.
FETCH_ATTEMPTS = 2

#: How long one outlet may take before the rest of its articles are abandoned.
#:
#: The crawl's job is every outlet, not every article of one outlet. Without
#: this, a single unreachable host holds the shard and the papers behind it in
#: the list go uncollected — which reads afterwards as a quiet day in six
#: provinces rather than as a crawl that never got there.
OUTLET_BUDGET_SECONDS = 300.0


#: What a run records about what it read, per outlet and per publication date.
#: Landed as its own artifact so the counts inherit the same provenance as the
#: articles, and so a day nobody published is a row saying zero rather than a
#: gap that reads as a crawl that did not run.
TALLY_DATASET = "daily-tallies"


def _url_key(url: str) -> str:
    """A short, stable directory name for a URL.

    Not a slug of the URL: article slugs run to a hundred characters and nest
    arbitrarily, and a path built from them hits filesystem limits on the
    outlets that put the whole headline in the path.
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]  # noqa: S324 - a path key


class NewsMonitoring(Source):
    """Sixty-nine Indonesian newspapers, read for what they report."""

    meta = SourceMeta(
        slug="news-monitoring",
        name="Indonesian news monitoring",
        category=Category.NEWS,
        source_type=SourceType.NEWS,
        collection_method=CollectionMethod.SCRAPE,
        # No single publisher, which is unusual here and worth stating rather
        # than leaving blank: the bytes come from sixty-nine newspapers, each
        # its own publisher, and the list of them is reference data under
        # version control rather than a field on this record.
        organization="Indonesian newspapers (reference/news/outlets.csv)",
        base_url=None,
        country="ID",
        license=(
            "Articles remain the copyright of their publishers. Collected for research; "
            "the archive is not redistributed and the API serves title, lead and link."
        ),
        update_frequency=UpdateFrequency.DAILY,
        notes=(
            "Two titles per province plus two national papers, listed in "
            "reference/news/outlets.csv. One shard of the list per firing; the "
            "collection window is a week, so a missed shard heals on the next run."
        ),
        max_requests_per_second=REQUESTS_PER_SECOND,
        # Hourly through the working day. Which outlets run is decided by the
        # hour, so the cron says "often" and the shard says "which".
        schedule="10 7-19 * * *",
    )

    def __init__(self, resolver: StorageResolver | None = None) -> None:
        self._resolver = resolver or StorageResolver(StorageConfig())

    # -- shard and window ---------------------------------------------------

    def _outlets(self, ctx: ScrapeContext) -> list[Outlet]:
        """Which outlets this run reads.

        `--param outlet=host` names one, `--param shard=n` names a shard, and
        `--param all=true` takes the list whole for a backfill. Absent all
        three, the shard is the hour: an agent that fires hourly walks the list
        across the day without being told where it got to.
        """
        outlets = list(active_outlets())
        named = ctx.params.get("outlet")
        if named:
            wanted = {host.strip().lower() for host in str(named).split(",")}
            return [outlet for outlet in outlets if outlet.host in wanted]
        if str(ctx.params.get("all", "")).lower() in ("1", "true", "yes"):
            return outlets
        shard = ctx.params.get("shard")
        index = int(shard) if shard is not None else datetime.now().hour
        wanted_shard = index % SHARDS
        return [
            outlet for position, outlet in enumerate(outlets) if position % SHARDS == wanted_shard
        ]

    def _since(self, ctx: ScrapeContext) -> date:
        """The oldest publication date this run keeps."""
        if ctx.since:
            return ctx.since
        days = int(ctx.params.get("window_days", WINDOW_DAYS))
        return datetime.now(UTC).date() - timedelta(days=days)

    # -- the "have we got this" question ------------------------------------

    def _seen(self, outlet: Outlet, url: str, published: date | None) -> bool:
        """Whether this URL already has a landing directory.

        Asked before fetching, which is the entire reason the URL hash is a
        partition segment. Without it the answer would need the bytes, and a
        daily crawl would re-fetch every article it has ever collected.
        """
        directory = Path(self._resolver.resolve(Layer.RAW, *self._segments(outlet, url, published)))
        return directory.is_dir() and any(directory.iterdir())

    def _segments(self, outlet: Outlet, url: str, published: date | None) -> list[str]:
        month = (published or datetime.now(UTC).date()).strftime("%Y-%m")
        return [
            Category.NEWS.value,
            self.meta.slug,
            "articles",
            f"outlet={outlet.host}",
            f"month={month}",
            f"url={_url_key(url)}",
        ]

    # -- the run ------------------------------------------------------------

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        issue = get_profile(str(ctx.params.get("profile", "violence")))
        lexicon = issue.lexicon()
        # Two different lists, doing two different jobs: the mined lexicon is
        # what goes into an outlet's search box, and the dictionary is what
        # judges the pages that come back.
        dictionary = issue.dictionary()
        terms = lexicon.terms(limit=int(ctx.params.get("terms", issue.search_terms)))
        outlets = self._outlets(ctx)
        since = self._since(ctx)
        shots = str(ctx.params.get("screenshots", "true")).lower() not in ("0", "false", "no")
        scan_all = str(ctx.params.get("scan", "all")).lower() == "all"

        log.info(
            "news.run.start",
            outlets=len(outlets),
            terms=len(terms),
            since=since.isoformat(),
            profile=issue.slug,
            scan="all" if scan_all else "keywords",
        )

        limiter = HostRateLimiter(default_rate=REQUESTS_PER_SECOND, burst=BURST)
        produced = 0
        # Reported at the end because "landed 1" on a run that read two hundred
        # articles reads as a run that barely worked, when it is a run that
        # read two hundred articles and found one incident.
        totals: Counter[str] = Counter()
        render = _renderer()

        with fetcher(
            limiter=limiter, timeout=35.0, headers=_HEADERS, attempts=FETCH_ATTEMPTS
        ) as client:
            for outlet in outlets:
                deadline = time.monotonic() + OUTLET_BUDGET_SECONDS
                try:
                    hits = discover(outlet, terms, client, since, render, scan_all=scan_all)
                except Exception as error:  # noqa: BLE001 - one outlet must not end a run
                    log.warning("news.outlet.failed", outlet=outlet.host, error=str(error)[:200])
                    # A tally even here. An outlet whose discovery threw was
                    # visited, and the row saying it read nothing is what tells
                    # the difference between a paper that went quiet and one
                    # this crawl cannot reach any more.
                    yield from self._tallies(outlet, {}, issue)
                    continue

                # What this outlet published, by the day it published it. Every
                # article read increments `scanned`, whether or not it is kept.
                tally: dict[str, Counter[str]] = {}

                for hit in hits:
                    if time.monotonic() > deadline:
                        log.warning(
                            "news.outlet.budget-spent",
                            outlet=outlet.host,
                            scanned=sum(c["scanned"] for c in tally.values()),
                            of=len(hits),
                        )
                        break
                    if ctx.limit is not None and produced >= ctx.limit:
                        log.info("news.run.limit", produced=produced)
                        # The partial tally still goes out. A run cut short by
                        # a limit read those articles, and a count that is
                        # dropped because the run stopped early is a day that
                        # reads as quieter than it was.
                        yield from self._tallies(outlet, tally, issue, len(hits))
                        browser.close_shared()
                        return
                    # An article already landed is already counted, on the day
                    # it was landed. Counting it again on every run would make
                    # a week-long window report a paper publishing the same
                    # article seven times.
                    if self._seen(outlet, hit.url, None):
                        continue

                    article = fetch_article(hit.url, client, render=render)
                    if not article.ok:
                        log.info("news.article.unusable", url=hit.url, status=article.status)
                        continue
                    if not in_window(article, since):
                        continue
                    if self._seen(outlet, hit.url, article.published_at):
                        continue

                    day = (article.published_at or datetime.now(UTC).date()).isoformat()
                    counts = tally.setdefault(day, Counter())
                    counts["scanned"] += 1
                    totals["scanned"] += 1

                    found = dictionary.match(article.text)
                    if found.candidate:
                        counts["matched"] += 1
                        totals["matched"] += 1

                    verdict, probability, engine = self._gate(issue, article, found)
                    if verdict:
                        counts["recorded"] += 1
                        totals["recorded"] += 1

                    if not verdict:
                        # Read, counted, and not about this issue. Nothing is
                        # stored, which is the whole point of scanning
                        # everything: the number is the product, not the page.
                        continue

                    metadata: dict[str, Any] = {
                        "outlet": outlet.outlet,
                        "outlet_host": outlet.host,
                        "outlet_geo_id": outlet.geo_id,
                        "outlet_province": outlet.province,
                        "title": article.title,
                        "lead": article.lead,
                        "canonical_url": article.canonical_url,
                        "discovered_by": hit.term,
                        "matched_terms": list(found.terms),
                        # Which categories fired, and the rule that admitted
                        # the article. Without them a corpus row cannot say
                        # why it is in the corpus, and the dictionary is the
                        # part of this pipeline most likely to be argued with.
                        "matched_categories": sorted(found.categories),
                        "dictionary_score": found.score,
                        "dictionary_verdict": found.verdict,
                        "profile": issue.slug,
                        "gate_probability": probability,
                        "gate_engine": engine,
                        "http_status": article.status,
                    }
                    partition = tuple(self._segments(outlet, hit.url, article.published_at)[3:])

                    yield Artifact(
                        content=article.html,
                        filename="article.html",
                        dataset="articles",
                        source_url=hit.url,
                        media_type="text/html",
                        published_at=article.published_at,
                        retrieved_at=datetime.now(UTC),
                        partition=partition,
                        metadata=metadata,
                    )
                    produced += 1

                    if verdict and shots and render is not None:
                        image = browser.shared().screenshot(hit.url)
                        if image:
                            yield Artifact(
                                content=image,
                                filename="page.png",
                                dataset="screenshots",
                                source_url=hit.url,
                                media_type="image/png",
                                published_at=article.published_at,
                                retrieved_at=datetime.now(UTC),
                                partition=partition,
                                metadata={"outlet_host": outlet.host, "of": hit.url},
                            )

                yield from self._tallies(outlet, tally, issue, len(hits))

        browser.close_shared()
        log.info(
            "news.run.done",
            scanned=totals["scanned"],
            matched=totals["matched"],
            recorded=totals["recorded"],
            landed=produced,
        )

    # -- the keep-or-discard decision ---------------------------------------

    def _gate(self, issue: Profile, article: Any, found: Match) -> tuple[bool, float | None, str]:
        """Whether this article is about the issue, and how sure we are.

        Two stages, cheapest first. The dictionary is free and rejects the
        great majority — a paper's day is mostly council meetings and football. What
        survives is put to the classifier, one question, which is what
        separates a report of collective violence from the crime reporting that
        shares its vocabulary: a story about three thieves beaten by a crowd and
        a story about three thieves arrested both say `dikeroyok`.

        With the classifier unreachable, a dictionary candidate is kept.
        Discarding on silence would leave a hole shaped exactly like a quiet
        week, and a candidate can be re-read later; a page never stored cannot.
        """
        if not found.candidate:
            return False, None, "dictionary"

        client = jev()
        if not client.available():
            return True, None, "unavailable"
        try:
            answers = client.ask(article.text[:12_000], issue.gate())
        except JevUnavailable as error:
            log.warning("news.gate.unavailable", url=article.url, error=str(error)[:160])
            return True, None, "unavailable"

        answer = answers.get("gate")
        probability = answer.probability if answer else None
        if probability is None:
            return True, None, "unavailable"
        return probability >= client.settings.gate_threshold, probability, "jev"

    def _tallies(
        self,
        outlet: Outlet,
        tally: dict[str, Counter[str]],
        issue: Profile,
        discovered: int = 0,
    ) -> Iterator[Artifact]:
        """One artifact per outlet holding what it published, by day.

        Landed rather than logged, because a count that lives only in a log is
        a count nothing downstream can read. Content-addressed like everything
        else, so a re-run that reads the same articles writes nothing new.

        Emitted even when nothing was read, which is the point: an outlet that
        has a tally saying zero was visited and had nothing new, and an outlet
        with no tally at all has never been visited. Those look identical on a
        page that shows a blank either way, and they are not the same problem —
        one is a quiet week, the other is a gap in the crawl. `discovered`
        separates the third case: an outlet whose listing and search both
        returned nothing is one whose discovery is broken, not one that stopped
        publishing.
        """
        record = {
            "outlet": outlet.outlet,
            "outlet_host": outlet.host,
            "outlet_geo_id": outlet.geo_id,
            "outlet_province": outlet.province,
            "profile": issue.slug,
            # Candidates discovery turned up, before the window and the gate.
            # Zero here with a reachable site means the sitemap, the feed and
            # the section page all came back empty — which is a fault to fix,
            # not a paper with nothing to say.
            "discovered": discovered,
            "days": {
                day: {
                    "scanned": counts["scanned"],
                    "matched": counts["matched"],
                    "recorded": counts["recorded"],
                }
                for day, counts in sorted(tally.items())
            },
        }
        collected = datetime.now(UTC)
        if not tally:
            # Nothing read. Still recorded, against the day of the run, because
            # the fact being recorded is that this outlet was visited.
            record["days"] = {
                collected.date().isoformat(): {"scanned": 0, "matched": 0, "recorded": 0}
            }
        yield Artifact(
            content=json.dumps(record, indent=2, sort_keys=True).encode(),
            filename="tally.json",
            dataset=TALLY_DATASET,
            media_type="application/json",
            retrieved_at=collected,
            partition=(f"outlet={outlet.host}", f"collected={collected.date().isoformat()}"),
            metadata={"outlet_host": outlet.host, "profile": issue.slug},
        )


#: Sent on every request. The outlets that refuse a short User-Agent are the
#: same ones that refuse an empty Accept-Language, and being refused is not
#: politeness — it is an outlet missing from a province's coverage.
_HEADERS = {
    "User-Agent": browser.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}


def _renderer():
    """A render callable, or None when Chromium is not available."""
    try:
        instance = browser.shared()
    except browser.BrowserUnavailable as error:
        log.warning("news.browser.absent", error=str(error)[:200])
        return None
    log.info("news.browser.ready")
    return instance.render
