"""Parsing numbers out of published statistics.

Indonesian sources write `1.234,56` where Anglophone ones write `1,234.56`.
Reading one as the other is wrong by a factor of a thousand and looks entirely
plausible in a chart, so this module refuses to guess when a value is genuinely
ambiguous rather than picking the more likely reading.

Published tables also carry values that are not numbers: an em dash for "no
data", an ellipsis for "not yet available", a footnote marker glued to a
figure. These have to be distinguished from each other — "not collected" and
"collected and zero" are different facts (program.md §7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class NumberFormat(StrEnum):
    """Which convention a source writes numbers in."""

    #: 1.234,56 — dot groups, comma decimal. BPS, BI, most ministries.
    INDONESIAN = "id"
    #: 1,234.56 — comma groups, dot decimal.
    ANGLO = "en"
    #: Work it out per value, refusing what cannot be settled.
    AUTO = "auto"


class ValueStatus(StrEnum):
    """Why a cell holds no number.

    Kept distinct because they mean different things downstream: a suppressed
    figure exists and is withheld, a missing one was never collected, and
    treating either as zero would be a fabrication.
    """

    OK = "ok"
    MISSING = "missing"
    SUPPRESSED = "suppressed"
    NOT_APPLICABLE = "not_applicable"
    PROVISIONAL = "provisional"
    UNPARSEABLE = "unparseable"


#: Markers that mean "no value", by what they mean rather than how they look.
_MARKERS: dict[str, ValueStatus] = {
    "-": ValueStatus.MISSING,
    "--": ValueStatus.MISSING,
    "—": ValueStatus.MISSING,
    "–": ValueStatus.MISSING,
    "": ValueStatus.MISSING,
    "n/a": ValueStatus.NOT_APPLICABLE,
    "na": ValueStatus.NOT_APPLICABLE,
    "n.a.": ValueStatus.NOT_APPLICABLE,
    "tidak ada": ValueStatus.NOT_APPLICABLE,
    "...": ValueStatus.MISSING,
    "…": ValueStatus.MISSING,
    "..": ValueStatus.MISSING,
    "x": ValueStatus.SUPPRESSED,
    "c": ValueStatus.SUPPRESSED,
    "rahasia": ValueStatus.SUPPRESSED,
    "confidential": ValueStatus.SUPPRESSED,
}

#: Trailing marks that qualify a figure without changing it. `r` is revised,
#: `p`/`*` provisional — common in BPS releases.
_PROVISIONAL_SUFFIXES = ("*", "**", "p", "r", "e")

#: A unit written into the cell rather than the column header: `12,5%`,
#: `1.234 ton`. Split out rather than discarded — the number alone is not the
#: fact, and a percentage read as an absolute is a silent error.
_TRAILING_UNIT = re.compile(r"(?<=[\d\s\)])(%|[A-Za-z]{2,}(?:/[A-Za-z]{1,4})?)\.?$")

_TRAILING_NOTE = re.compile(r"[\*†‡\)\]]+$")
_LEADING_SIGN = re.compile(r"^[+\-−]")
_DIGITS_ONLY = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class ParsedValue:
    """One cell, resolved."""

    value: Decimal | None
    status: ValueStatus
    raw: str

    #: A unit found in the cell itself, if any. Usually the unit lives in the
    #: column header instead, and this is None.
    unit: str | None = None

    #: False when the number was read under an assumption that could have gone
    #: the other way. Carried into Silver so a downstream reader can exclude
    #: uncertain figures rather than discovering the problem in a chart.
    unambiguous: bool = True

    @property
    def ok(self) -> bool:
        return self.status is ValueStatus.OK and self.value is not None

    def as_float(self) -> float | None:
        return float(self.value) if self.value is not None else None


def parse_value(raw: str, number_format: NumberFormat = NumberFormat.AUTO) -> ParsedValue:
    """Read one published cell."""
    text = (raw or "").strip()
    lowered = text.lower()

    if lowered in _MARKERS:
        return ParsedValue(None, _MARKERS[lowered], raw)

    # Units come off first so the steps below see a bare number: `(2,5)%` has
    # to lose its percent before the brackets read as an accounting negative.
    unit = None
    unit_match = _TRAILING_UNIT.search(text)
    if unit_match:
        unit = unit_match.group(1).strip(".")
        text = text[: unit_match.start()].strip()

    # Accounting negatives: (1.234) means -1234.
    negated = False
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
        negated = True

    status = ValueStatus.OK
    stripped = text

    for suffix in _PROVISIONAL_SUFFIXES:
        if stripped.lower().endswith(suffix) and len(stripped) > len(suffix):
            candidate = stripped[: -len(suffix)].strip()
            if candidate and candidate[-1].isdigit():
                stripped = candidate
                status = ValueStatus.PROVISIONAL
                break

    stripped = _TRAILING_NOTE.sub("", stripped).strip()
    stripped = stripped.replace(" ", "").replace(" ", "")
    stripped = stripped.replace("−", "-")  # U+2212 minus, common in PDFs

    if not stripped or stripped in {"-", "+"}:
        return ParsedValue(None, ValueStatus.MISSING, raw)

    sign = -1 if _LEADING_SIGN.match(stripped) and stripped[0] in "-−" else 1
    stripped = _LEADING_SIGN.sub("", stripped)
    if negated:
        sign = -sign

    digits, unambiguous = _to_decimal_text(stripped, number_format)
    if digits is None:
        return ParsedValue(None, ValueStatus.UNPARSEABLE, raw, unit=unit)

    try:
        value = Decimal(digits) * sign
    except InvalidOperation:
        return ParsedValue(None, ValueStatus.UNPARSEABLE, raw, unit=unit)

    return ParsedValue(value, status, raw, unit=unit, unambiguous=unambiguous)


def _to_decimal_text(text: str, number_format: NumberFormat) -> tuple[str | None, bool]:
    """Turn a grouped number into plain decimal text.

    Returns the text and whether the reading was forced by the data rather than
    assumed.
    """
    if _DIGITS_ONLY.match(text):
        return text, True

    if not re.fullmatch(r"[\d.,]+", text):
        return None, True

    has_dot, has_comma = "." in text, "," in text

    if has_dot and has_comma:
        # Both present: whichever comes last is the decimal separator. No
        # convention puts the group separator after the decimal point, so this
        # is settled by the data itself.
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        return _split_on(text, decimal_sep), True

    if not has_dot and not has_comma:
        return text, True

    separator = "." if has_dot else ","
    parts = text.split(separator)

    if len(parts) > 2:
        # Repeated separator can only be grouping: 1.234.567.
        return text.replace(separator, ""), True

    tail = parts[1]

    if len(tail) != 3:
        # Not a group of three, so it must be a decimal fraction: 1,5 or 1.5.
        return _split_on(text, separator), True

    # A single separator with exactly three digits after it — `1.234` — is
    # genuinely ambiguous: one thousand two hundred and thirty four, or one
    # point two three four. Only an explicit format settles it.
    if number_format is NumberFormat.INDONESIAN:
        return (text.replace(".", ""), True) if separator == "." else (_split_on(text, ","), True)
    if number_format is NumberFormat.ANGLO:
        return (text.replace(",", ""), True) if separator == "," else (_split_on(text, "."), True)

    # AUTO: read as grouping, which is the commoner intent in published
    # statistics, and mark the value as assumed so it can be excluded.
    return text.replace(separator, ""), False


def _split_on(text: str, decimal_sep: str) -> str:
    """Drop group separators, keep one decimal point."""
    group_sep = "," if decimal_sep == "." else "."
    return text.replace(group_sep, "").replace(decimal_sep, ".")
