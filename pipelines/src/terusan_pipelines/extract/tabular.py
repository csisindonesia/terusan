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

        sample = text[:8192]
        try:
            dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            # Sniffing fails on single-column files and on ragged headers.
            # Comma is the right guess far more often than it is wrong.
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
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
