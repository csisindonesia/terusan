"""CSV, TSV, JSON and JSONL into Bronze records.

Every value is carried across as text. Bronze does not decide types
(program.md §6): a column that is all-digits in one file and carries a footnote
marker in the next would flip type between partitions, and the dataset stops
reading as one table. Silver decides, once, with the whole column in view.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import Any

from .base import ExtractionError, Extractor, Landed

#: Guessed in order. UTF-8 first because it is right most of the time;
#: cp1252 last because it accepts almost any byte sequence and would mask a
#: genuine encoding problem if tried earlier.
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1", "cp1252")

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}

#: Candidate field separators, in the order ties are broken.
DELIMITERS = (";", "\t", "|", ",")

#: Rows sampled when deciding which delimiter a file uses.
_SNIFF_ROWS = 20


def decode(data: bytes) -> str:
    """Decode bytes, trying the usual suspects in order."""
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Every encoding failed, so the file is probably not text at all. Losing
    # characters beats losing the row entirely, and the mangling is visible.
    return data.decode("utf-8", errors="replace")


class CsvExtractor(Extractor):
    """Delimited text into one Bronze record per row."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.path.suffix.lower() in CSV_SUFFIXES or (landed.media_type or "").startswith(
            "text/csv"
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        text = decode(landed.path.read_bytes())
        if not text.strip():
            return

        reader = csv.DictReader(io.StringIO(text), delimiter=detect_delimiter(text))
        for number, row in enumerate(reader, start=1):
            yield {
                "dataset": landed.dataset or landed.path.stem,
                "row_number": number,
                "columns": {
                    _clean_key(k): "" if v is None else str(v)
                    for k, v in row.items()
                    if k is not None
                },
            }


class JsonExtractor(Extractor):
    """JSON and JSONL into Bronze records.

    A top-level list becomes one record per element; a top-level object becomes
    one record, unless it holds exactly one list value, which is how most APIs
    wrap a result set.
    """

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.path.suffix.lower() in JSON_SUFFIXES or (landed.media_type or "").startswith(
            "application/json"
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        text = decode(landed.path.read_bytes())
        if not text.strip():
            return

        dataset = landed.dataset or landed.path.stem
        for number, item in enumerate(self._items(landed, text), start=1):
            yield {
                "dataset": dataset,
                "row_number": number,
                "columns": {k: _stringify(v) for k, v in _flatten(item).items()},
            }

    def _items(self, landed: Landed, text: str) -> Iterator[Any]:
        if landed.path.suffix.lower() in {".jsonl", ".ndjson"}:
            for line in text.splitlines():
                if line.strip():
                    yield json.loads(line)
            return

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        if isinstance(document, list):
            yield from document
        elif isinstance(document, dict):
            lists = [v for v in document.values() if isinstance(v, list)]
            yield from lists[0] if len(lists) == 1 else [document]
        else:
            yield {"value": document}


def detect_delimiter(text: str) -> str:
    """Work out which character separates a file's fields.

    `csv.Sniffer` handles clean files well but raises on ragged ones, and
    ragged files are ordinary here: published CSVs carry trailing note rows,
    blank lines and badly exported merged cells. Falling back to a comma when
    it raises turns a ragged semicolon file into a single column, and every
    value in it becomes unparseable.

    Chosen on structure instead, which degrades rather than raising: the right
    delimiter is the one that splits the header into more than one field and
    gives the most rows that same count. Ties go to the candidate finding more
    columns, then to the earlier entry in `DELIMITERS`.
    """
    lines = [line for line in text.splitlines()[: _SNIFF_ROWS + 1] if line.strip()]
    if not lines:
        return ","

    best, best_score = ",", -1.0
    for delimiter in DELIMITERS:
        rows = list(csv.reader(lines, delimiter=delimiter))
        if not rows or len(rows[0]) < 2:
            continue
        expected = len(rows[0])
        consistent = sum(1 for row in rows[1:] if len(row) == expected)
        # Field count breaks ties: with two delimiters both perfectly
        # consistent, the one finding more columns is reading the real
        # structure rather than splitting on an incidental character.
        score = (consistent / max(1, len(rows) - 1)) + expected / 1000
        if score > best_score:
            best, best_score = delimiter, score

    return best


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested structures into dotted keys.

    Bronze records are a flat string map, so nesting has to go somewhere; a
    dotted key keeps the shape legible for whoever writes the Silver query.
    """
    if not isinstance(value, dict):
        return {prefix or "value": value}

    flat: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            flat.update(_flatten(item, name))
        else:
            flat[name] = item
    return flat


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _clean_key(key: str) -> str:
    return key.strip().lstrip("﻿")
