"""Reading one article page.

What is wanted from a news page is small — a title, a lead, the body text and
the date it was published — and everything else on the page is navigation,
related-article rails, comment widgets and advertising. Pulling the four out is
done by preferring what the page declares about itself over what it displays:
`article:published_time` and JSON-LD are written for machines and are right far
more often than a date rendered into the page furniture.

The publication date decides whether an article is in the collection window,
and the window is the only thing keeping a daily crawl from re-reading an
outlet's entire archive. An article whose date cannot be read is therefore kept
but marked, never assumed to be recent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
import structlog
from selectolax.parser import HTMLParser

log = structlog.get_logger(__name__)

#: Nodes that are never article text.
_STRIP = (
    "script", "style", "noscript", "nav", "header", "footer", "aside", "form",
    "iframe", "figure", "figcaption", "button",
)

#: Where a page states its own publication time, best first.
_DATE_META = (
    'meta[property="article:published_time"]',
    'meta[name="article:published_time"]',
    'meta[property="og:article:published_time"]',
    'meta[name="pubdate"]',
    'meta[name="publishdate"]',
    'meta[itemprop="datePublished"]',
    'meta[name="date"]',
)

_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12, "des": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "agu": 8, "sep": 9, "okt": 10, "nov": 11,
}

_ID_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})\b")
_URL_DATE = re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|$)")


@dataclass(frozen=True, slots=True)
class Article:
    """One fetched page, parsed."""

    url: str
    status: int
    html: bytes = b""
    title: str = ""
    lead: str = ""
    body: str = ""
    canonical_url: str | None = None
    published_at: date | None = None

    @property
    def ok(self) -> bool:
        """Whether there is enough here to code."""
        return self.status == 200 and bool(self.body or self.lead)

    @property
    def text(self) -> str:
        """Title and body, which is what the classifier reads."""
        return f"{self.title}\n{self.body or self.lead}".strip()


def _meta(tree: HTMLParser, selector: str, attribute: str = "content") -> str | None:
    node = tree.css_first(selector)
    return (node.attributes.get(attribute) or "").strip() or None if node else None


def _parse_date(value: str) -> date | None:
    """Read a date out of whatever form a page states it in."""
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    match = _ID_DATE.search(value)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day))
            except ValueError:
                return None
    return None


def _jsonld_date(tree: HTMLParser) -> date | None:
    """The date from a schema.org block, which most CMSs emit correctly."""
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text() or "{}")
        except (ValueError, TypeError):
            continue
        for entry in payload if isinstance(payload, list) else [payload]:
            if not isinstance(entry, dict):
                continue
            for key in ("datePublished", "dateCreated", "uploadDate"):
                found = _parse_date(str(entry.get(key) or ""))
                if found:
                    return found
    return None


#: How far ahead of today a stated publication date may be.
#:
#: One day, for a timezone: Jakarta is seven hours ahead of UTC, so a piece
#: published this evening is legitimately "tomorrow" by a UTC clock. Beyond
#: that a date is a CMS artefact — a scheduled post, a template default, a
#: mistyped year — and taking it would file the article in a month that has
#: not happened and skew that month's counts.
FUTURE_TOLERANCE_DAYS = 1


def published_date(tree: HTMLParser, url: str, body: str) -> date | None:
    """When the article says it was published.

    The URL is tried before the visible text: a path carrying `/2026/09/21/` is
    the CMS's own filing date, while a date in the body is as likely to be the
    date of the incident being reported — which is a different fact and one
    that must not decide the collection window.
    """
    for selector in _DATE_META:
        found = _parse_date(_meta(tree, selector) or "")
        if found:
            return found
    found = _jsonld_date(tree)
    if found:
        return found
    match = _URL_DATE.search(url)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            pass
    node = tree.css_first("time[datetime]")
    if node:
        found = _parse_date(node.attributes.get("datetime") or "")
        if found:
            return found
    return _parse_date(body[:400])


def _plausible(when: date | None) -> date | None:
    """Drop a date that cannot have happened yet."""
    if when is None:
        return None
    if (when - today()).days > FUTURE_TOLERANCE_DAYS:
        log.info("news.article.future-date", stated=when.isoformat())
        return None
    return when


def parse(html: bytes, url: str) -> Article:
    """Turn a fetched page into an article."""
    try:
        text = html.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = ""
    tree = HTMLParser(text)
    for selector in _STRIP:
        for node in tree.css(selector):
            node.decompose()

    title = _meta(tree, 'meta[property="og:title"]') or ""
    if not title:
        heading = tree.css_first("h1") or tree.css_first("title")
        title = " ".join((heading.text() or "").split()) if heading else ""

    lead = _meta(tree, 'meta[property="og:description"]') or _meta(
        tree, 'meta[name="description"]'
    ) or ""

    paragraphs = [
        " ".join((node.text() or "").split())
        for node in tree.css("article p") or tree.css("p")
    ]
    # Two-word paragraphs are captions, bylines and share prompts.
    body = "\n".join(p for p in paragraphs if len(p.split()) > 6)

    canonical = _meta(tree, 'link[rel="canonical"]', "href") or _meta(
        tree, 'meta[property="og:url"]'
    )

    return Article(
        url=url,
        status=200,
        html=html,
        title=title[:500],
        lead=lead[:1000],
        body=body[:60_000],
        canonical_url=canonical,
        published_at=_plausible(published_date(tree, url, body)),
    )


def fetch(url: str, fetcher, *, render=None) -> Article:
    """Fetch and parse one article.

    Falls back to the browser on the statuses that mean "not to a robot" —
    403 and 503 — rather than on every failure: rendering a page that 404s
    costs seconds and returns the same 404.
    """
    try:
        response = fetcher.get(url)
        return parse(response.content, str(response.url))
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status in (403, 503) and render is not None:
            rendered_status, html = render(url)
            if rendered_status == 200 and html:
                return parse(html.encode("utf-8", errors="replace"), url)
        return Article(url=url, status=status)
    except (httpx.HTTPError, OSError) as error:
        log.warning("news.fetch.failed", url=url, error=str(error)[:200])
        return Article(url=url, status=0)


def in_window(article: Article, since: date) -> bool:
    """Whether an article falls inside the collection window.

    An article with no readable date is admitted. The alternative is dropping
    every article from the several outlets whose CMS states no date anywhere,
    which loses those outlets entirely; a wrongly-admitted old article is
    visible in the corpus and can be filtered later, while a dropped one is
    invisible forever.
    """
    if article.published_at is None:
        return True
    return article.published_at >= since


def today() -> date:
    return datetime.now(UTC).date()
