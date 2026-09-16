"""Checking that bytes are what they claim to be.

The commonest silent failure in scraping: a server answers 200 with an HTML
error page, and it lands as `TABEL1_1.xls`. Nothing complains until a parser
explodes months later, and by then nobody knows when the data went bad — or
which months are affected.

So the bytes are checked, not the extension and not the Content-Type header,
which lies routinely. This runs at landing, before anything reaches RAW, because
RAW is immutable and a bad file there is permanent.

Ported from an earlier warehouse, where it caught exactly this failure against
Bank Indonesia's endpoints.
"""

from __future__ import annotations

_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF-",),
    # xlsx and docx are zip containers; xls is the older OLE2 compound file,
    # which Bank Indonesia still publishes.
    "xlsx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "gz": (b"\x1f\x8b",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "parquet": (b"PAR1",),
}

_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")

#: How far into a file to look. Enough for a leading comment or BOM before the
#: real markup starts.
_WINDOW = 512


class ContentMismatch(ValueError):
    """Bytes that do not match the format they were landed as."""


def looks_like_html(content: bytes) -> bool:
    head = content[:_WINDOW].lstrip().lower()
    return any(head.startswith(marker) or marker in head for marker in _HTML_MARKERS)


def matches_extension(content: bytes, extension: str) -> bool:
    """Whether the bytes are consistent with the extension.

    Text formats — csv, json, txt, html — have no signature, so they always pass
    here and are validated by parsing instead. A false pass is better than
    refusing to land a perfectly good CSV because it starts with a blank line.
    """
    signatures = _SIGNATURES.get(extension.lower().lstrip("."))
    if signatures is None:
        return True
    return content[:16].startswith(signatures)


def describe(content: bytes) -> str:
    """Best-effort label, for an error message worth reading."""
    if not content:
        return "empty"
    if looks_like_html(content):
        return "html"
    for name, signatures in _SIGNATURES.items():
        if content[:16].startswith(signatures):
            return name
    return "unknown"


def verify(content: bytes, filename: str) -> None:
    """Raise if `content` is not what `filename` says it is.

    Called at landing. A binary format that arrives as HTML is the error this
    exists to catch; text formats pass through, since they have no signature to
    check against.
    """
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not extension or extension not in _SIGNATURES:
        return
    if not content:
        raise ContentMismatch(f"{filename}: empty response")
    if not matches_extension(content, extension):
        raise ContentMismatch(
            f"{filename}: expected {extension}, got {describe(content)} — "
            "the server most likely answered with an error page"
        )
