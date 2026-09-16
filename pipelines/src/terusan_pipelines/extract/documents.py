"""HTML, PDF and plain text into Bronze documents.

PDF and HTML parsing pull heavy dependencies, so they are optional: the
extractor reports itself unavailable rather than failing at import, and the
runner records the artifact as unhandled. A missing library should leave a gap
that is visible in the run summary, not a crash that stops a corpus.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from .base import ExtractionError, Extractor, Landed
from .tabular import decode

HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
TEXT_SUFFIXES = {".txt", ".md", ".text"}

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def collapse(text: str) -> str:
    """Normalize whitespace without destroying paragraph structure.

    Paragraph breaks survive because they are the only structural signal left
    once tags are gone, and Silver's article splitting depends on them.
    """
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", text).strip()


class TextExtractor(Extractor):
    """Plain text, as-is."""

    target = "documents"

    def handles(self, landed: Landed) -> bool:
        return landed.path.suffix.lower() in TEXT_SUFFIXES

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        text = decode(landed.path.read_bytes())
        yield {
            "document_type": "web_page",
            "title": _first_line(text),
            "raw_text": collapse(text),
            "metadata": json.dumps(landed.extra) if landed.extra else None,
        }


class HtmlExtractor(Extractor):
    """HTML into text, keeping the markup alongside it.

    `raw_html` is retained because extraction is provisional: a later pass
    wanting a table or a byline should not have to go back to RAW and re-read
    the file to find it.
    """

    target = "documents"

    def handles(self, landed: Landed) -> bool:
        return landed.path.suffix.lower() in HTML_SUFFIXES or (landed.media_type or "").startswith(
            "text/html"
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        html = decode(landed.path.read_bytes())
        yield {
            "document_type": "web_page",
            "title": self._title(html),
            "raw_text": self._text(html),
            "raw_html": html,
            "metadata": json.dumps(landed.extra) if landed.extra else None,
        }

    @staticmethod
    def _title(html: str) -> str | None:
        match = _TITLE.search(html)
        return collapse(_TAG.sub("", match.group(1))) if match else None

    @staticmethod
    def _text(html: str) -> str:
        """Strip markup with a regex rather than a parser.

        Deliberate: this runs over every page of a large corpus, the output
        feeds a full-text index rather than a structured read, and a parser
        dependency for that is not worth it. Extractors that need real
        structure should subclass and use selectolax.
        """
        stripped = _SCRIPT_OR_STYLE.sub(" ", html)
        stripped = re.sub(r"</(p|div|br|li|tr|h[1-6])>", "\n", stripped, flags=re.IGNORECASE)
        return collapse(_unescape(_TAG.sub(" ", stripped)))


class PdfExtractor(Extractor):
    """PDF text, when pypdf is installed.

    Text only. Tables, figures and scanned pages need a different tool, and
    pretending otherwise would put silently empty rows into Bronze.
    """

    target = "documents"

    def handles(self, landed: Landed) -> bool:
        return (
            landed.path.suffix.lower() == ".pdf" or (landed.media_type or "") == "application/pdf"
        )

    @staticmethod
    def available() -> bool:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return False
        return True

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ExtractionError(
                str(landed.path),
                "pypdf is not installed; add the `extract` extra to read PDFs",
            ) from exc

        try:
            reader = PdfReader(landed.path)
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable PDF: {exc}") from exc

        info = reader.metadata or {}
        yield {
            "document_type": "report",
            "title": _clean(info.get("/Title")) or landed.original_filename,
            "raw_text": collapse("\n\n".join(pages)),
            "page_count": len(pages),
            "metadata": json.dumps(
                {
                    "author": _clean(info.get("/Author")),
                    "producer": _clean(info.get("/Producer")),
                    **landed.extra,
                }
            ),
        }


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:500]
    return None


def _clean(value: object) -> str | None:
    text = str(value).strip() if value else ""
    return text or None


def _unescape(text: str) -> str:
    from html import unescape

    return unescape(text)
