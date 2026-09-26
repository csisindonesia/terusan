"""Re-reading a landed news page.

The crawl already parsed the page once, to decide whether it was in the
collection window and whether the lexicon matched. It kept the result in the
landing metadata and the original HTML in RAW.

Extraction parses it again from the bytes rather than trusting that metadata,
because the parser improves and RAW does not: an article landed a year ago
should be read by today's reader, and the landing record is what today's reader
is meant to be able to correct.
"""

from __future__ import annotations

from typing import Any

from ..sources.news.article import parse
from .base import Landed


def read_article(landed: Landed) -> dict[str, Any]:
    """Title, lead, body and the text a classifier reads, from RAW."""
    try:
        html = landed.path.read_bytes()
    except OSError:
        html = b""
    article = parse(html, landed.source_url or "")
    extra = landed.extra or {}
    title = article.title or str(extra.get("title") or "")
    lead = article.lead or str(extra.get("lead") or "")
    body = article.body
    return {
        "title": title,
        "lead": lead,
        "body": body,
        "text": f"{title}\n{body or lead}".strip(),
    }
