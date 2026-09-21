"""BNPB's datastore pages into Bronze records.

The generic JSON reader cannot read these. CKAN answers
`{"success": true, "result": {"records": [...], "fields": [...]}}`, which a
generic reader sees as one object and turns into a single row holding the whole
table as a string.

So this claims the source's JSON and yields one record per row, keyed by the
column names BNPB's uploader wrote — `Nama Kabupaten/Kota`, `Jumlah Kejadian`,
`Meninggal`. They are Indonesian, they are inconsistent between datasets, and
that is correct for Bronze: deciding that `Meninggal` and `Jumlah Meninggal`
are one measure is Silver's job, and doing it here would bury the decision in a
parser (program.md §6).

CKAN's own `_id` is dropped. It is the datastore's row key, not a figure, and
it changes when a resource is re-uploaded.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import structlog

from .base import ExtractionError, Extractor, Landed
from .bnpb_impact import is_province_table, period_for, unrecognised
from .bnpb_impact import records as impact_records

log = structlog.get_logger(__name__)

SOURCE_SLUG = "bnpb-disaster"

#: The catalogue pages are `package_search` answers, not tables. They land
#: under their own dataset so they can be told apart from the figures without
#: parsing them first, and this extractor claims them only to keep the generic
#: JSON reader from making a Bronze row out of one.
CATALOGUE_DATASET = "bnpb-catalogue"

#: CKAN's row key, which is not data.
ROW_KEY = "_id"


class BnpbDatastoreExtractor(Extractor):
    """One Bronze record per row of a BNPB table."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".json"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        if landed.dataset == CATALOGUE_DATASET:
            # The catalogue is provenance, not figures. It is claimed here
            # rather than left to the generic JSON reader, which would put a
            # whole `package_search` answer into Bronze as one row of text and
            # call it a record.
            return

        try:
            body = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            raise ExtractionError(str(landed.path), "no CKAN result object")

        records = result.get("records")
        if not isinstance(records, list):
            raise ExtractionError(str(landed.path), "no records in the CKAN result")

        labels = _labels(result.get("fields"))

        # The page's own offset, so row numbers stay unique across the pages of
        # one table rather than restarting at 1 in each.
        offset = _offset(landed)

        resource = str(landed.extra.get("title") or "")
        period = period_for(landed.partition, str(landed.dataset or ""), resource)
        # One line per table, not one per province.
        reported = False

        for index, row in enumerate(records, start=1):
            if not isinstance(row, dict):
                continue

            columns = {
                str(key): "" if value is None else str(value)
                for key, value in row.items()
                if key != ROW_KEY
            }
            if not any(columns.values()):
                continue

            # A table of provinces by hazard is nine series on one row, and
            # reading it as cells would put nine different measures in one
            # Bronze record. `bnpb_impact` says what those series are.
            if period and is_province_table(columns):
                if not reported:
                    reported = True
                    unknown = unrecognised(columns)
                    if unknown:
                        log.info(
                            "bnpb.unrecognised_columns", resource=resource, columns=unknown[:8]
                        )
                yield from impact_records(
                    columns,
                    resource=resource,
                    period=period,
                    row_number=offset + index,
                )
                continue

            yield {
                "dataset": landed.dataset or SOURCE_SLUG,
                "row_number": offset + index,
                "columns": columns
                # What the table is, carried from the catalogue: a datastore
                # page names neither its resource nor the collection it
                # belongs to, and without them one province's row of numbers
                # is indistinguishable from another table's.
                | {
                    "resource": resource,
                    "resource_id": str(landed.extra.get("resource_id") or ""),
                    "package_title": str(landed.extra.get("package_title") or ""),
                }
                | labels,
            }


def _labels(fields: Any) -> dict[str, str]:
    """The column labels BNPB's uploader wrote, where they differ from the name.

    CKAN keeps a `label` and a `notes` per field — the definition of `Jumlah
    Kejadian` down to the BNPB regulation it comes from. They describe the
    table rather than the row, so they ride along once per row under a prefixed
    key rather than being folded into the values.
    """
    if not isinstance(fields, list):
        return {}

    out: dict[str, str] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("id") or "")
        if not name or name == ROW_KEY:
            continue
        info = field.get("info")
        label = str((info or {}).get("label") or "").strip() if isinstance(info, dict) else ""
        if label and label != name:
            out[f"label:{name}"] = label
    return out


def _offset(landed: Landed) -> int:
    """Where this page started in its table."""
    try:
        return int(landed.extra.get("row_offset") or 0)
    except (TypeError, ValueError):
        return 0
