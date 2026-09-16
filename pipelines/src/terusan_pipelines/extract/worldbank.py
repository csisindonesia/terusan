"""World Bank indicator API into Bronze records.

The generic JSON extractor cannot read this source. The API answers
`[metadata, [records...]]` — a two-element list whose second element is the
payload — so a generic reader yields two rows: the metadata, and the whole
record array flattened into one string.

This is why the earlier warehouse kept a parser per source. Most sources need
nothing of the sort; the ones that publish an envelope of their own get an
extractor that claims their artifacts by source slug, registered ahead of the
generic readers.

Ported from that warehouse, minus the validation, which now belongs in Silver.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .base import ExtractionError, Extractor, Landed

#: Every World Bank series shares one envelope, so the extractor claims them by
#: prefix rather than being duplicated per indicator.
SOURCE_PREFIX = "worldbank-"


class WorldBankExtractor(Extractor):
    """One Bronze record per country-year observation."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return (
            landed.source_slug.startswith(SOURCE_PREFIX) and landed.path.suffix.lower() == ".json"
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        if not isinstance(body, list) or len(body) < 2 or body[1] is None:
            # An empty page is normal at the end of a paginated pull; a
            # malformed one is not, but neither is worth failing the corpus for.
            return

        for number, row in enumerate(body[1], start=1):
            iso3 = row.get("countryiso3code")
            if not iso3:
                # Aggregates — "World", "Euro area" — carry an empty iso3. They
                # are real figures, but they are sums of the rows around them,
                # and keeping them would double-count any total taken over the
                # dataset.
                continue

            value = row.get("value")
            yield {
                # Carried from the artifact rather than hardcoded: one
                # extractor serves every World Bank series.
                "dataset": landed.dataset or "worldbank",
                "row_number": number,
                "columns": {
                    "country_iso3": str(iso3),
                    "country_name": str((row.get("country") or {}).get("value") or ""),
                    "year": str(row.get("date") or ""),
                    "value": "" if value is None else str(value),
                    "indicator": str((row.get("indicator") or {}).get("id") or ""),
                },
            }
