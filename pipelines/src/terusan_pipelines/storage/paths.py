"""Path segment hygiene.

NAS and object storage disagree about which characters are safe, and source
titles are hostile input: they carry colons, slashes, accents and newlines.
Every segment that reaches a physical path goes through `slugify` first
(program.md §45.6).
"""

from __future__ import annotations

import re
import unicodedata

_UNSAFE = re.compile(r"[^a-z0-9._=-]+")
_REPEATED_DASH = re.compile(r"-{2,}")

#: Longest single path segment we will emit. Most filesystems allow 255
#: bytes; we stay well under so that suffixes like `.parquet` or a part
#: number can always be appended.
MAX_SEGMENT_LENGTH = 180


class UnsafePathSegment(ValueError):
    """Raised when a caller supplies a segment that cannot be made safe."""


def slugify(value: str, *, allow_partition: bool = True) -> str:
    """Reduce `value` to an ASCII-safe path segment.

    `allow_partition` keeps `=` so that Hive-style partition segments such as
    `year=2026` survive unchanged (program.md §46).
    """
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = _UNSAFE.sub("-", ascii_only)
    if not allow_partition:
        cleaned = cleaned.replace("=", "-")
    cleaned = _REPEATED_DASH.sub("-", cleaned).strip("-.")
    cleaned = cleaned[:MAX_SEGMENT_LENGTH].rstrip("-.")
    if not cleaned:
        raise UnsafePathSegment(f"segment {value!r} is empty after sanitising")
    return cleaned


def check_segment(segment: str) -> str:
    """Validate a segment the caller believes is already safe.

    Used for segments that must round-trip exactly — dataset names, partition
    expressions — where silently rewriting the value would produce a path that
    no longer matches what the catalog recorded.
    """
    if not segment or segment in {".", ".."}:
        raise UnsafePathSegment(f"segment {segment!r} is not a usable path component")
    if "/" in segment or "\\" in segment:
        raise UnsafePathSegment(f"segment {segment!r} must not contain a path separator")
    if segment != slugify(segment):
        raise UnsafePathSegment(
            f"segment {segment!r} is not path-safe; expected {slugify(segment)!r}"
        )
    return segment
