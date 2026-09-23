"""The document catalogue: what was collected, as opposed to what it said.

Every scraper lands its artifacts through `sources.landing`, which writes a
provenance sidecar beside each file. Those sidecars are already the answer to
"what material is this warehouse built on" — there are fifteen hundred of them
— but nothing read them back, so the material was invisible to a reader while
the figures derived from it were not.

This turns them into a Silver table. It reads sidecars rather than the content
files, for the same reason extraction does: an artifact landed without
provenance has no recorded origin and cannot be admitted (program.md §2.2).

The grain is the retrieval. Two editions of one handbook are two documents,
because they are two files with two content hashes, and an observation points
at one of them and not the other.

Nothing here reads a PDF. Deciding what a document *says* is extraction's job
and lives in `extract.documents`; this decides what a document *is*.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ..extract.base import Landed
from ..identifiers import dataset_code

log = structlog.get_logger(__name__)

#: Document types, extending program.md §13's list.
#:
#: The PRD's vocabulary is for unstructured material — regulation, news,
#: report, publication — and most of what actually lands is neither: a FRED
#: series download is a CSV, and calling it a report would be a lie told by the
#: catalogue about its own contents. `data_file` is that honest fifth option.
DOCUMENT_TYPES = (
    "regulation",
    "news",
    "report",
    "research",
    "publication",
    "press_release",
    "court_decision",
    "clipping",
    "web_page",
    "data_file",
)

#: The types that are a document in the sense a reader means it: something a
#: person sits down and reads. `web_page` and `data_file` are the other two —
#: material collected for a machine to parse, which is most of what lands.
#:
#: The distinction is what the portal's Documents page is for. A catalogue that
#: listed six hundred FRED search-result pages beside the ESDM handbook would
#: bury the one thing anyone came looking for.
READABLE_TYPES = tuple(t for t in DOCUMENT_TYPES if t not in {"web_page", "data_file"})

#: What a media type is, absent anything better. Only the head of the type is
#: matched, so `text/csv; charset=utf-8` classifies the same as `text/csv`.
#:
#: Word processor and presentation formats are a document by their nature — a
#: publisher does not issue a .docx for a machine to read. Spreadsheets are
#: deliberately absent: .xlsx is how an agency ships a table, and calling it a
#: report would put it on a page of things to read, which it is not.
_BY_MEDIA_TYPE = {
    "application/pdf": "publication",
    "application/epub+zip": "publication",
    "application/msword": "report",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "report",
    "application/vnd.oasis.opendocument.text": "report",
    "application/rtf": "report",
    "text/rtf": "report",
    "application/vnd.ms-powerpoint": "report",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "report",
    "application/vnd.oasis.opendocument.presentation": "report",
    "text/html": "web_page",
    "application/xhtml+xml": "web_page",
}


def is_readable(document_type: str) -> bool:
    """Whether this is something a person reads, rather than a machine parses.

    What separates the Documents page from the provenance record behind it. The
    record holds everything a scraper landed, because that is what a figure
    points back at; the page holds what somebody would actually open.
    """
    return document_type in READABLE_TYPES


#: Words a title-caser must leave alone. Without them the humanised form of a
#: filename reads "Handbook Of Energy And Economic Statistics Of Indonesia",
#: which is how a machine writes a title.
_MINOR_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "dan",
    "de",
    "di",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "van",
}

#: Acronyms a title-caser would otherwise reduce to Apbd, Pdb, Ihsg.
_ACRONYMS = {
    "apbd",
    "apbn",
    "bps",
    "esdm",
    "gdp",
    "ihk",
    "ihsg",
    "imf",
    "lpg",
    "ojk",
    "pdb",
    "pdrb",
    "seki",
    "spi",
    "usd",
}


def classify(landed: Landed, extracted_type: str | None = None) -> str:
    """What kind of document an artifact is.

    Ordered by how much the answer is actually known. A source that states the
    type knows best; an extractor that has read the bytes knows next best; the
    media type is a guess, and a good one only for the formats a human reads.

    Anything else is a `data_file`. That is not a failure to classify — a
    spreadsheet downloaded from a statistics portal *is* a data file, and
    promoting it to "report" because the catalogue prefers prose would put a
    row in the table that nobody could trust.
    """
    declared = landed.extra.get("document_type")
    if isinstance(declared, str) and declared in DOCUMENT_TYPES:
        return declared
    if extracted_type in DOCUMENT_TYPES:
        return extracted_type
    head = (landed.media_type or "").split(";", 1)[0].strip().lower()
    return _BY_MEDIA_TYPE.get(head, "data_file")


def humanize(stem: str) -> str:
    """Turn a filename stem into something a person would read.

    Deliberately plain: hyphens and underscores become spaces, words are
    title-cased, and the small words and known acronyms are left as they
    should be. It is a fallback, not a parser — a source that knows its
    document's real title should say so in the artifact's metadata, which
    `title_for` prefers over anything derived here.
    """
    words = [w for w in stem.replace("_", "-").replace(".", "-").split("-") if w]
    out: list[str] = []
    for index, word in enumerate(words):
        lowered = word.lower()
        if lowered in _ACRONYMS:
            out.append(lowered.upper())
        elif word.isdigit():
            out.append(word)
        elif index > 0 and lowered in _MINOR_WORDS:
            out.append(lowered)
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


#: Sidecar keys that hold a title outright, in the order they are trusted.
_TITLE_KEYS = ("title", "name", "label")

#: Sidecar keys that hold a filename the publisher chose, which is a title with
#: an extension on the end. HDX names its resources this way — "Movement
#: Distribution Maps_2026-07-13_to_2026-07-16.csv" — and the words are the
#: publisher's even though the shape is a file's.
_FILENAME_TITLE_KEYS = ("resource_name", "original_name")


def is_opaque(stem: str) -> bool:
    """Whether a filename stem is an identifier rather than a name.

    A CKAN resource is served under its UUID, so half of what lands is called
    `7321e08f-eac6-46e8-ba43-98b2bcce04b4`. Humanising that yields "7321e08f
    Eac6 46e8 Ba43 98b2bcce04b4", which is worse than the identifier it came
    from: it looks like a title, so a reader stops trying to work out what the
    document is.

    Judged by how much of the stem is hexadecimal, because that is what
    identifiers are made of and what real titles are not. A document called
    "Table 1a" survives; one called `d41d8cd98f00b204e9800998ecf8427e` does not.
    """
    parts = [p for p in stem.replace("_", "-").split("-") if p]
    if not parts:
        return True
    hexish = sum(
        1 for part in parts if len(part) >= 4 and all(c in "0123456789abcdefABCDEF" for c in part)
    )
    return hexish * 2 >= len(parts)


def title_for(landed: Landed, extracted_title: str | None = None) -> str:
    """The best title available for a landed artifact.

    Four fifths of sidecars carry one the scraper read off the page it was
    listed on, which is the publisher's own words and beats anything derivable.
    Failing that, an extractor may have read one out of the bytes. Failing
    both, the filename is humanised — unless the filename is an identifier, in
    which case the document is named for the collection it landed in, so that
    no row in the catalogue is either nameless or falsely named.
    """
    for key in _TITLE_KEYS:
        stated = landed.extra.get(key)
        if isinstance(stated, str) and stated.strip():
            return stated.strip()

    for key in _FILENAME_TITLE_KEYS:
        stated = landed.extra.get(key)
        if isinstance(stated, str) and stated.strip():
            # The extension only: the rest is the publisher's own wording and
            # humanising it would mangle the dates these names carry.
            return stated.strip().rsplit(".", 1)[0] or stated.strip()

    if extracted_title and extracted_title.strip():
        return extracted_title.strip()

    if landed.original_filename:
        stem = landed.original_filename.rsplit(".", 1)[0]
        if stem and not is_opaque(stem):
            humanized = humanize(stem)
            if humanized:
                return humanized

    dataset = humanize(landed.dataset or "") or landed.dataset or "Document"
    return f"{dataset} ({landed.document_id})"


def subtitle_for(landed: Landed) -> str | None:
    """What tells two artifacts of one collection apart.

    The partition, spelled for a reader: `edition=2025` becomes `edition 2025`.
    A handbook's fifteen editions carry near-identical filenames, and without
    this the catalogue lists fifteen rows a reader cannot distinguish.
    """
    if not landed.partition:
        return None
    return ", ".join(segment.replace("=", " ") for segment in landed.partition)


def page_count(path: Path) -> int | None:
    """How many pages a PDF has, read from the file itself.

    Asked here rather than left to extraction, because extraction is about what
    a document *says* and most documents here are never read: a ministry's
    #: statistical handbook is
    claimed by the handbook's own table parser, so the generic PDF reader never
    sees it and the page count would be blank beside a viewer showing 172.

    Only the page tree is parsed, not the text, so this costs a seek rather
    than a read. A file that will not open returns None and is logged: a
    catalogue entry missing a page count is a small gap, and refusing to
    catalogue the document over it would be a large one.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        return len(PdfReader(path).pages)
    except Exception as exc:  # noqa: BLE001 - any malformed PDF, surfaced with its path
        log.warning("documents.unreadable_pdf", path=str(path), error=str(exc))
        return None


def row(
    landed: Landed,
    *,
    raw_root: str,
    pipeline_version: str,
    publisher: str | None = None,
    links: tuple[int, int] = (0, 0),
    extracted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `SILVER_DOCUMENTS` row.

    `links` is (indicators, observations) counted from the figures themselves,
    passed in rather than looked up here so a build scans the observations once
    instead of once per document.

    `extracted` is the Bronze row for this document where an extractor has read
    it — the page count, the word count, the language. Absent for most: the
    lake lands far more material than it parses, and a catalogue that only
    listed the parsed part would understate what is held.
    """
    extracted = extracted or {}
    indicators, observations = links
    slug = landed.dataset

    pages = extracted.get("page_count")
    if pages is None and (landed.media_type or "").startswith("application/pdf"):
        pages = page_count(landed.path)
    return {
        "document_id": landed.document_id,
        "document_type": classify(landed, extracted.get("document_type")),
        "title": title_for(landed, extracted.get("title")),
        "subtitle": subtitle_for(landed),
        "language": extracted.get("language"),
        "author": landed.extra.get("author"),
        "publisher": publisher,
        "published_at": landed.published_at,
        "source_url": landed.source_url,
        "original_filename": landed.original_filename,
        "media_type": landed.media_type,
        "size_bytes": landed.size_bytes,
        "page_count": pages,
        "word_count": extracted.get("word_count"),
        "indicator_count": indicators,
        "observation_count": observations,
        "dataset_id": dataset_code(slug) if slug else None,
        "dataset_slug": slug,
        "partition": list(landed.partition),
        "source_id": landed.source_slug,
        "content_hash": landed.content_hash,
        # Relative to the lake root, so the catalogue survives the lake moving
        # between a mount and a bucket (program.md §45.4). The same form Bronze
        # records, so the two agree about where a document is.
        "raw_path": str(landed.path).removeprefix(raw_root).lstrip("/"),
        "retrieved_at": landed.retrieved_at,
        "processed_at": datetime.now(UTC),
        "pipeline_version": pipeline_version,
    }
