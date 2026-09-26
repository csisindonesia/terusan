"""Finding what an outlet published.

Search first, because search is keyword-driven and keyword-driven discovery is
what bounds the crawl to articles that might be about something. Several
outlets in the list have a search box that ignores its query and returns the
latest news instead, and a couple return nothing at all — so when search cannot
be trusted, the same outlet is read through its news sitemap, its feed and the
section page the source list pointed at, and the lexicon does the filtering
afterwards instead of the search box doing it first.

A search box that ignores its query is the harder of the two to notice, because
it fails by returning plenty. Asked for `bentrok warga`, penasultra.id answers
with fourteen articles about banking, a TikTok celebrity and the provincial
budget — none of them matching anything, all of them recorded as found by a
violence term. So every outlet is asked a control question first: a nonsense
word that cannot match an article. An outlet that answers it with articles is
one whose search is furniture, and it goes down the listing route instead.

That fallback is not a lesser route. It is how an outlet with a broken search
stays in the dataset rather than silently contributing nothing, and a corpus
that quietly drops eight outlets is one whose province coverage is a fiction.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx
import structlog

from ...news.outlets import Outlet
from . import search
from .search import Hit

log = structlog.get_logger(__name__)

#: Where a news sitemap usually lives, best first.
SITEMAP_PATHS = (
    "/sitemap-news.xml",
    "/news-sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap.xml",
)
FEED_PATHS = ("/feed", "/rss", "/feed/", "/rss.xml", "/index.xml")

#: Search returning fewer than this from an outlet is taken as a search box
#: that does not work, not as an outlet with nothing to report.
SEARCH_FLOOR = 3

#: How long one outlet's discovery may take before the rest of its terms are
#: abandoned.
#:
#: A host that hangs rather than refuses is the expensive failure. Each request
#: waits out its timeout and is retried with backoff, and there are two dozen
#: terms to get through, so one unreachable origin can hold a shard for an hour
#: — which is what `radarlombok.co.id` did, silently, while the outlets behind
#: it in the list went uncollected. Whatever has been found by the deadline is
#: what that outlet contributes.
DISCOVERY_BUDGET_SECONDS = 120.0

#: Asked of every outlet before its real terms. Not a word, in any language, so
#: an outlet whose search works answers with nothing.
CONTROL_TERM = "zxqvkwjqx"

#: Articles returned for the control term, above which the search box is taken
#: to be ignoring its query. One or two can be a site that falls back to
#: "related" content; a page of them is a site that never read the query.
CONTROL_TOLERANCE = 2

#: Child sitemaps to follow from an index. News sitemaps are small; an archive
#: index can list hundreds of monthly files and following them all would walk
#: the outlet's entire history on every run.
MAX_CHILD_SITEMAPS = 4

#: Candidates one outlet may contribute to a run.
#:
#: A sitemap is an outlet's whole archive, and many of them carry no `lastmod`
#: — so there is no way to tell an article published this morning from one
#: published in 2019 without fetching it and reading its date. Uncapped, one
#: outlet with a big undated sitemap spends the entire shard fetching articles
#: from years ago to discover that they are old, and lands nothing.
#:
#: The cap costs recall on an outlet that published more than this in a week,
#: which is why dated entries are ordered ahead of undated ones: where a
#: sitemap says when, the cap falls on the oldest rather than on an arbitrary
#: slice.
MAX_CANDIDATES = 60

_LASTMOD = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _feed_date(value: str) -> date | None:
    """The date a sitemap or feed states, in either of the forms they use.

    Sitemaps write ISO-8601 and RSS writes RFC-822 — `Mon, 21 Sep 2026
    10:00:00 +0700` — and reading only the first leaves every feed entry
    undated. Undated is not fatal, because an article's own page is read for
    its date anyway, but it means fetching a year of archive to find out it is
    a year old.
    """
    value = value.strip()
    if not value:
        return None
    match = _LASTMOD.search(value)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def _strip_namespace(tag: str) -> str:
    """`{http://www.sitemaps.org/schemas/sitemap/0.9}loc` -> `loc`.

    Sitemaps and feeds declare namespaces and the readers here do not care
    which: a `<loc>` is a `<loc>` whether the document calls it sitemap 0.9,
    RSS or Atom.
    """
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _locs(text: str) -> list[tuple[str, date | None]]:
    """Every URL in a sitemap or feed, with its stated date where there is one.

    Parsed as XML, which sounds obvious and was not: the first version of this
    used the HTML parser the rest of the crawl uses, and an HTML parser treats
    `<link>` as a void element and drops what is inside it. Sitemaps survived
    that — `<loc>` is not a void tag — but nothing did, because the nesting
    an HTML parser infers for `<urlset><url><loc>` is not the nesting the
    document has. The route returned an empty list for every outlet, silently,
    and the outlets that still yielded articles were the ones whose section
    page is ordinary HTML.
    """
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return []

    found: list[tuple[str, date | None]] = []
    for node in root.iter():
        name = _strip_namespace(node.tag)
        if name not in ("url", "sitemap", "item", "entry"):
            continue
        url = ""
        when: date | None = None
        for child in node:
            child_name = _strip_namespace(child.tag)
            if child_name in ("loc", "guid") and not url:
                url = (child.text or "").strip()
            elif child_name == "link" and not url:
                # RSS puts the URL in the text, Atom in an href attribute.
                url = (child.get("href") or child.text or "").strip()
            elif child_name in ("lastmod", "pubdate", "updated", "publication_date"):
                when = _feed_date(child.text or "")
        if url:
            found.append((url, when))
    return found


#: Statuses worth a second attempt through the browser. A block is a page that
#: exists and was withheld from a robot; everything else is an answer.
BLOCKED_STATUS = frozenset({403, 406, 429, 503})


def _get(url: str, fetcher, render=None, *, speculative: bool = False) -> str:
    """Fetch a page as text, falling back to the browser when blocked.

    Only when blocked, and never for a speculative probe. Both halves matter.

    Rendering every failure sounds harmless and is not: each of the four
    sitemap paths and four feed paths tried below is absent on most outlets,
    and a browser render of a 404 costs seconds to be told again that the page
    is not there.

    `speculative` marks those path guesses, and for them the browser is not
    used at all. An outlet that answers 403 to a robot answers 403 to nine
    guesses in a row too, and by the time the crawl asked `korankaltim.com` for
    the section page it actually wanted, the site had stopped serving the
    session altogether — the same URL that returns a hundred kilobytes when
    asked once returned the block page when asked tenth. Goodwill spent
    guessing is goodwill not available for the page that matters.
    """
    try:
        # Through the Fetcher, not its client: that is what waits its turn on
        # the shared per-host limiter and retries a transient failure.
        return fetcher.get(url).text
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
    except (httpx.HTTPError, OSError) as error:
        log.debug("news.discover.fetch-failed", url=url, error=str(error)[:120])
        return ""

    if speculative:
        return ""
    if status in BLOCKED_STATUS:
        # Said out loud either way. An outlet that refuses robots and has no
        # browser behind it contributes nothing, and the run should not look
        # like a paper with nothing to say.
        if render is None:
            log.warning("news.discover.blocked-no-browser", url=url, status=status)
            return ""
        rendered, html = render(url)
        log.info("news.discover.rendered", url=url, status=rendered, bytes=len(html))
        return html if rendered == 200 else ""
    return ""


def search_works(outlet: Outlet, fetcher, render=None) -> bool:
    """Whether this outlet's search box reads its query.

    One request, and it decides how the outlet is read for the rest of the run.
    Worth it: the alternative is a corpus where an outlet's entire front page
    is recorded as having been found by a violence keyword, which is a lie
    about provenance even though the gate drops every one of those articles.
    """
    url = search.search_url(outlet.base_url, outlet.adapter, CONTROL_TERM)
    html = _get(url, fetcher, render)
    if not html:
        # No answer at all is not proof of a broken query — it is a blocked or
        # slow request. The hit floor below still catches an outlet that
        # returns nothing for everything.
        return True
    hits = search.extract_hits(html, outlet.base_url, outlet.host, CONTROL_TERM)
    responsive = len(hits) <= CONTROL_TOLERANCE
    if not responsive:
        log.info("news.discover.search-ignores-query", outlet=outlet.host, returned=len(hits))
    return responsive


def by_search(outlet: Outlet, terms: Iterable[str], fetcher, render=None) -> list[Hit]:
    """Ask an outlet's own search box for each term, within a time budget."""
    hits: dict[str, Hit] = {}
    deadline = time.monotonic() + DISCOVERY_BUDGET_SECONDS
    for term in terms:
        if time.monotonic() > deadline:
            log.warning(
                "news.discover.budget-spent", outlet=outlet.host, hits=len(hits), stage="search"
            )
            break
        url = search.search_url(outlet.base_url, outlet.adapter, term)
        html = _get(url, fetcher, render)
        if not html:
            continue
        for hit in search.extract_hits(html, outlet.base_url, outlet.host, term):
            hits.setdefault(hit.url, hit)
    log.info("news.discover.search", outlet=outlet.host, hits=len(hits))
    return list(hits.values())


def by_listing(outlet: Outlet, fetcher, since: date, render=None) -> list[Hit]:
    """Read the outlet's sitemap, feed and section page.

    Used when search fails. The date on a sitemap entry is the CMS's own and is
    applied here, because a sitemap lists an outlet's whole archive and
    fetching all of it to read the date off each page is the thing this is
    avoiding.
    """
    hits: dict[str, Hit] = {}

    for path in SITEMAP_PATHS:
        text = _get(
            urljoin(outlet.base_url + "/", path.lstrip("/")), fetcher, render, speculative=True
        )
        entries = _locs(text)
        if not entries:
            continue
        # A sitemap index can list a file per section — Tribun's lists
        # fifty-four — and only a few can be followed. Take the ones that say
        # they changed most recently, and prefer the ones that call themselves
        # news: an outlet's `sitemap_news.xml` is the recent end of its
        # archive, which is the only part a daily crawl wants.
        children = [(when, url) for url, when in entries if url.endswith((".xml", ".xml.gz"))]
        children.sort(
            key=lambda pair: (
                "news" not in pair[1].lower(),
                -(pair[0].toordinal() if pair[0] else 0),
            )
        )
        for _, child in children[:MAX_CHILD_SITEMAPS]:
            # Named by the index rather than guessed, so this one is not
            # speculative: the outlet said the file is there.
            entries.extend(_locs(_get(child, fetcher, render)))
        dated: list[tuple[date | None, str]] = []
        for url, when in entries:
            if url.endswith((".xml", ".xml.gz")):
                continue
            if when is not None and when < since:
                continue
            if search.is_article(url.rstrip("/"), outlet.host):
                dated.append((when, url.rstrip("/")))
        # Dated entries first, newest first; undated last. A sitemap that says
        # when is a sitemap whose recent end can be taken.
        dated.sort(key=lambda pair: (pair[0] is None, -(pair[0].toordinal() if pair[0] else 0)))
        for _, url in dated:
            hits.setdefault(url, Hit(url, "", outlet.host, "sitemap"))
        if hits:
            break

    if not hits:
        for path in FEED_PATHS:
            feed = urljoin(outlet.base_url + "/", path.lstrip("/"))
            for url, when in _locs(_get(feed, fetcher, render, speculative=True)):
                if when is not None and when < since:
                    continue
                if search.is_article(url.rstrip("/"), outlet.host):
                    hits.setdefault(url.rstrip("/"), Hit(url.rstrip("/"), "", outlet.host, "feed"))
            if hits:
                break

    # The section the source list pointed at, and failing that the front page.
    #
    # The front page is the weaker source — it is a day's news of every kind,
    # where a crime desk is already narrowed — but an outlet with no sitemap,
    # no feed and no section has otherwise nothing to read, and three papers
    # were contributing nothing for exactly that reason. A newspaper's front
    # page links to what it published today, which is what this needs.
    for listing, label in ((outlet.section_url, "section"), (outlet.base_url, "front-page")):
        if not listing or hits:
            continue
        html = _get(listing, fetcher, render)
        if not html:
            continue
        for hit in search.extract_hits(html, outlet.base_url, outlet.host, label):
            hits.setdefault(hit.url, hit)

    found = list(hits.values())[:MAX_CANDIDATES]
    log.info("news.discover.listing", outlet=outlet.host, hits=len(hits), considered=len(found))
    return found


def discover(
    outlet: Outlet,
    terms: Iterable[str],
    fetcher,
    since: date,
    render=None,
    *,
    scan_all: bool = True,
) -> list[Hit]:
    """Every candidate article for one outlet, by whichever route works.

    `scan_all` is the normal mode and means what it says: read everything the
    outlet published in the window, from its sitemap, its feed and its section
    page, and let the gate decide what is worth keeping. Counting what a paper
    published is only possible if the crawl has seen all of it, and a
    keyword-bounded crawl has by construction not.

    Set it false to go back to keyword search, which fetches far less. The
    figures then describe what the search terms found rather than what the
    paper published, and the daily totals are not totals.
    """
    terms = list(terms)
    if scan_all:
        listed = {hit.url: hit for hit in by_listing(outlet, fetcher, since, render)}
        # Search is a supplement here, not the route: it reaches pages a
        # sitemap sometimes omits, and costs little once the listing is in.
        if search_works(outlet, fetcher, render):
            for hit in by_search(outlet, terms, fetcher, render):
                listed.setdefault(hit.url, hit)
        return list(listed.values())[:MAX_CANDIDATES]

    if not search_works(outlet, fetcher, render):
        return by_listing(outlet, fetcher, since, render)

    hits = by_search(outlet, terms, fetcher, render)
    if len(hits) >= SEARCH_FLOOR:
        return hits[:MAX_CANDIDATES]
    # Keep whatever search did find; the listing route adds to it rather than
    # replacing it, because the two reach different parts of a site.
    found = {hit.url: hit for hit in hits}
    for extra in by_listing(outlet, fetcher, since, render):
        found.setdefault(extra.url, extra)
    return list(found.values())[:MAX_CANDIDATES]
