"""Checks a single figure must pass to be published.

Each returns a reason rather than a corrected value. The spec's rule is to
quarantine anomalies instead of silently coercing them, and the reason is what
lets a reader of `restriction_quarantine` decide whether the publisher or the
parser is wrong.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import taxonomy as tx

#: What a publisher writes for a figure it does not have. Komdigi uses `-`.
MISSING_MARKERS = frozenset({"-", "–", "x", "...", "…", "n/a", "na", "?"})

#: `<10`: Google's way of withholding a small count. Not missing — the figure
#: exists and is under the threshold — and not a number either.
_BELOW_THRESHOLD = re.compile(r"<\s*\d+")

REPORTED = "reported"
NOT_AVAILABLE = "not_available"
BELOW_THRESHOLD = "below_threshold"


def parse_value(raw: str, unit: tx.Unit) -> tuple[Decimal | None, str, str | None]:
    """A raw figure as (value, status, problem).

    A missing marker is a value of nothing with its status, never zero. An
    empty cell is a problem, because the extractors do not emit one: if it
    arrives, something upstream has changed.
    """
    text = (raw or "").strip()
    if not text:
        return None, NOT_AVAILABLE, "empty value"
    if text.lower() in MISSING_MARKERS:
        return None, NOT_AVAILABLE, None
    if _BELOW_THRESHOLD.fullmatch(text):
        return None, BELOW_THRESHOLD, None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, NOT_AVAILABLE, f"value {text!r} is not a number"
    if not value.is_finite():
        return None, NOT_AVAILABLE, f"value {text!r} is not a finite number"
    if value < 0:
        return None, NOT_AVAILABLE, f"value {text} is negative"
    if unit is tx.Unit.COUNT and value != value.to_integral_value():
        return None, NOT_AVAILABLE, f"count {text} is not a whole number"
    if unit is tx.Unit.PERCENT and value > 100:
        return None, NOT_AVAILABLE, f"percentage {text} is over 100"
    if unit is tx.Unit.RATIO and value > 1:
        return None, NOT_AVAILABLE, f"ratio {text} is over 1"
    return value, REPORTED, None
