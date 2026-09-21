"""Short, stable identifiers for things a publisher names badly.

An indicator identifier is a path segment, a URL parameter, a Parquet partition
and a filename before it is anything else. Most series can carry a readable one
— `apbd_expenditure_realisasi` — but a bulk source cannot: FRED's titles run to
a hundred and seventy characters, and a hundred of them differ only in their
last few words, so any readable identifier is either too long for a URL or too
short to tell two series apart.

So those get a code instead: eight lowercase characters, derived from what the
publisher already calls the series. Derived rather than drawn at random, which
is the whole point — normalization rebuilds an indicator from scratch every run,
and an identifier that changed between runs would orphan the previous run's
partition, break every link anyone had saved, and leave the warehouse unable to
say whether two files describe the same series.

The name a reader sees does not live here. It lives in the Silver indicators
table, beside the unit and the publisher's own code.
"""

from __future__ import annotations

import hashlib
import re

#: Digits and lowercase letters. No uppercase (identifiers are lowercased all
#: through the lake) and no punctuation (these end up in paths and URLs).
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

#: Eight characters of base36 is 2.8 × 10¹² identifiers. At a hundred thousand
#: indicators the chance of any two colliding is under a percent, and a
#: collision is caught rather than silently merged: two series landing on one
#: identifier disagree about the same observation, which Silver refuses to
#: write (see `normalize.runner.CollapsedDimension`).
DEFAULT_LENGTH = 8


def short_id(namespace: str, key: str, *, length: int = DEFAULT_LENGTH) -> str:
    """A stable short identifier for `key` within `namespace`.

    The namespace is usually the source slug, so two publishers using the same
    series code — which they do — do not land on one identifier.
    """
    digest = hashlib.sha256(f"{namespace}|{key}".encode()).digest()
    number = int.from_bytes(digest[:16], "big")

    characters = []
    for _ in range(length):
        number, remainder = divmod(number, len(_ALPHABET))
        characters.append(_ALPHABET[remainder])
    return "".join(reversed(characters))


#: The shape of a derived code: eight characters of base36, nothing else.
#: Shape alone does not prove a string came from `short_id` — a readable slug
#: could in principle look like one — so this is used where the alternative is
#: a slug carrying an underscore or a hyphen, which every readable identifier
#: in this warehouse does.
CODE_PATTERN = re.compile(rf"^[{_ALPHABET}]{{{DEFAULT_LENGTH}}}$")


def is_code(value: str | None) -> bool:
    """Whether `value` already reads as a derived code."""
    return bool(value) and CODE_PATTERN.match(value) is not None  # type: ignore[arg-type]


def indicator_code(source_id: str, key: str) -> str:
    """The catalogue identifier for one series.

    Namespaced by the source because publishers reuse each other's series
    codes: `GDP` means one thing in the World Bank's API and another in Trading
    Economics', and two series sharing an identifier would have Silver write
    one over the other.

    The readable key stays in the indicators table as `slug`. It is what the
    mapping was declared with and what a maintainer searches for; it is not
    what the URL or the partition carries, because a publisher renaming a
    series would then orphan every link and every partition written before the
    rename (program.md §10).
    """
    return short_id(source_id, key)


def dataset_code(slug: str) -> str:
    """The catalogue identifier for one collection.

    Not namespaced by source: a dataset belongs to exactly one, and its slug is
    already unique across the lake — `consumer-survey` is Bank Indonesia's and
    nobody else's.
    """
    return short_id("dataset", slug)
