"""Finding articles through an outlet's own search box.

Every title in the list runs somebody's CMS, and there are only a handful of
them: WordPress's `?s=`, Tribun's `/search?q=`, and a few house builds. So the
search URL is a template chosen by the outlet's `adapter`, and a wrong guess
costs nothing — a search that returns no article links falls through to the
site's sitemap, which `discover` handles.

What comes back is a listing page, and listing pages are mostly navigation. The
filters below are the difference between a hundred article URLs and a hundred
category, tag and author pages: an article URL on an Indonesian news site
almost always carries either a date or a long hyphenated slug, and almost never
sits under `/tag/` or `/penulis/`.

Search pages carry no usable date, so the collection window is not applied
here. It is applied once each article's own publication date has been read,
which is the only date worth trusting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse

import structlog
from selectolax.parser import HTMLParser

log = structlog.get_logger(__name__)

#: One search URL per CMS. `{base}` is the outlet's host, `{q}` the term.
SEARCH_TEMPLATES: dict[str, str] = {
    "wp_query": "{base}/?s={q}",
    "tribun": "{base}/search?q={q}",
    "jawapos": "{base}/search?q={q}",
    "prokal": "{base}/?s={q}",
    "disway": "{base}/search?q={q}",
    "antara": "{base}/search?q={q}",
    "kompas": "{base}/search?q={q}",
    "cnn": "{base}/search?query={q}",
    "blogger": "{base}/search?q={q}",
}

#: Paths that are navigation rather than an article.
#:
#: Matched as whole path segments. Written as a bare substring it rejected
#: every article on any outlet that serves `/news/<slug>/index.html` — the
#: `index` in the filename read as the `index` of a section listing, and
#: `ajnn.net` contributed nothing at all while appearing to be crawled
#: normally.
SKIP_PATH = re.compile(
    r"/(?:tag|tags|category|kategori|kanal|author|penulis|page|search|topic|topik|"
    r"indeks|index|video|foto|gallery|galeri|tentang|kontak|redaksi|privacy|"
    r"feed|rss|wp-content|amp)(?:/|$)",
    re.I,
)

#: Filenames a CMS appends to a directory-style article URL. Stripped before
#: the path is judged: they say nothing about what the page is.
INDEX_FILE = re.compile(r"/index\.(?:html?|php|aspx?)$", re.I)

#: `/2026/09/` — the date most Indonesian CMSs put in an article path.
DATE_IN_PATH = re.compile(r"/20\d{2}[/-]\d{1,2}")

#: A slug of four or more hyphenated words, which is what a headline becomes.
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){3,}")


@dataclass(frozen=True, slots=True)
class Hit:
    """One candidate article, before it has been fetched."""

    url: str
    title: str
    host: str

    #: Which lexicon term found it, or the route that did. Carried onto the
    #: Bronze row: a corpus where nobody can say why an article is in it
    #: cannot be reasoned about when recall is questioned.
    term: str


def search_url(base_url: str, adapter: str, term: str) -> str:
    """Where to ask an outlet for articles matching a term."""
    template = SEARCH_TEMPLATES.get(adapter, SEARCH_TEMPLATES["wp_query"])
    return template.format(base=base_url.rstrip("/"), q=quote_plus(term))


def is_article(url: str, host: str) -> bool:
    """Whether a link looks like an article on this host.

    Rejecting off-host links matters more than it sounds: search pages on these
    sites carry syndication widgets pointing at every other title in the same
    network, and following them silently attributes one paper's reporting to
    another.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    netloc = parsed.netloc.lower().removeprefix("www.")
    if netloc != host.removeprefix("www."):
        return False
    path = INDEX_FILE.sub("", parsed.path)
    if not path or path == "/":
        return False
    if SKIP_PATH.search(path):
        return False
    return bool(DATE_IN_PATH.search(path) or SLUG.search(path.rsplit("/", 1)[-1]))


def extract_hits(html: str, base_url: str, host: str, term: str) -> list[Hit]:
    """Pull article links out of a listing or search page."""
    tree = HTMLParser(html)
    seen: dict[str, Hit] = {}
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base_url, href).split("#")[0].rstrip("/")
        if not is_article(url, host) or url in seen:
            continue
        title = " ".join((node.text() or "").split())
        # A link whose text is an image or an arrow tells us nothing; the
        # article's own title is read at fetch time anyway.
        seen[url] = Hit(url=url, title=title[:300], host=host, term=term)
    return list(seen.values())
