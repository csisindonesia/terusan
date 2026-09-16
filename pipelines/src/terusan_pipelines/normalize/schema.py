"""Arrow schemas for the Silver layer.

Silver is where types are decided, once, with the whole column in view — Bronze
deliberately keeps everything as text so a value cannot change type between
partitions (program.md §6, §7).

Observations use `decimal128` rather than `double`. Published statistics are
decimal quantities: a figure like 1234.56 has no exact binary representation,
and summing a million of them in float drifts in a way that is impossible to
explain to whoever has to defend the total.
"""

from __future__ import annotations

import pyarrow as pa

#: Currency and index figures need more than the two decimal places money uses;
#: exchange rates are quoted to four and trade volumes run to twelve digits.
VALUE_TYPE = pa.decimal128(38, 9)

#: Provenance carried from Bronze so a Silver row still answers "where did this
#: come from" without a join (program.md §17).
SILVER_PROVENANCE_FIELDS: list[pa.Field] = [
    pa.field("source_id", pa.string(), nullable=False),
    # The page or endpoint the figure came from. `raw_path` points at the
    # preserved copy; this points at where it was published, which is what
    # "where did this number come from" usually means (program.md §2.2).
    pa.field("source_url", pa.string()),
    pa.field("document_id", pa.string()),
    pa.field("dataset_id", pa.string()),
    pa.field("content_hash", pa.string()),
    pa.field("raw_path", pa.string()),
    pa.field("retrieved_at", pa.timestamp("us", tz="UTC")),
    pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("pipeline_version", pa.string()),
]

#: Indicators: what is being measured (program.md §10).
SILVER_INDICATORS = pa.schema(
    [
        pa.field("indicator_id", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("canonical_name", pa.string()),
        pa.field("description", pa.string()),
        pa.field("category", pa.string()),
        pa.field("subcategory", pa.string()),
        pa.field("unit", pa.string()),
        pa.field("frequency", pa.string()),
        pa.field("methodology", pa.string()),
        pa.field("source_id", pa.string()),
        pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

#: Observations: the fact table. Indicator + time + geography + dimensions
#: (program.md §10).
SILVER_OBSERVATIONS = pa.schema(
    [
        pa.field("observation_id", pa.string(), nullable=False),
        pa.field("indicator_id", pa.string(), nullable=False),
        # Canonical label plus explicit bounds: a period string cannot be
        # compared or filtered, and most questions here are about ranges.
        pa.field("period", pa.string(), nullable=False),
        pa.field("period_start", pa.date32(), nullable=False),
        pa.field("period_end", pa.date32(), nullable=False),
        pa.field("temporal_resolution", pa.string(), nullable=False),
        pa.field("value", VALUE_TYPE),
        pa.field("unit", pa.string()),
        # Why a value is absent. "Not collected" and "collected and zero" are
        # different facts, and a null alone cannot tell them apart.
        pa.field("status", pa.string(), nullable=False),
        # False where the number was read under an assumption that could have
        # gone the other way — see normalize.values.
        pa.field("value_unambiguous", pa.bool_(), nullable=False),
        pa.field("raw_value", pa.string()),
        pa.field("geo_id", pa.string()),
        pa.field("geo_name_raw", pa.string()),
        pa.field("commodity_id", pa.string()),
        pa.field("commodity_name_raw", pa.string()),
        pa.field("release_date", pa.date32()),
        pa.field("revision", pa.int32()),
        *SILVER_PROVENANCE_FIELDS,
    ]
)

#: Documents: unstructured material, normalized (program.md §13).
SILVER_DOCUMENTS = pa.schema(
    [
        pa.field("document_id", pa.string(), nullable=False),
        pa.field("document_type", pa.string()),
        pa.field("title", pa.string()),
        pa.field("subtitle", pa.string()),
        pa.field("content", pa.string()),
        pa.field("language", pa.string()),
        pa.field("author", pa.string()),
        pa.field("publisher", pa.string()),
        pa.field("published_at", pa.date32()),
        pa.field("word_count", pa.int32()),
        pa.field("source_url", pa.string()),
        *SILVER_PROVENANCE_FIELDS,
    ]
)

#: Geography dimension (program.md §11).
SILVER_GEOGRAPHY = pa.schema(
    [
        pa.field("geo_id", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("official_name", pa.string()),
        pa.field("geo_type", pa.string(), nullable=False),
        pa.field("parent_geo_id", pa.string()),
        pa.field("country_code", pa.string()),
        pa.field("province_code", pa.string()),
        pa.field("regency_code", pa.string()),
        pa.field("bps_code", pa.string()),
        pa.field("iso_code", pa.string()),
        # Administrative changes must not overwrite earlier definitions
        # (program.md §11), so validity is part of the row.
        pa.field("valid_from", pa.date32()),
        pa.field("valid_to", pa.date32()),
        pa.field("aliases", pa.list_(pa.string())),
    ]
)

#: Commodity dimension (program.md §12).
SILVER_COMMODITIES = pa.schema(
    [
        pa.field("commodity_id", pa.string(), nullable=False),
        pa.field("canonical_name", pa.string(), nullable=False),
        pa.field("description", pa.string()),
        pa.field("category", pa.string()),
        pa.field("subcategory", pa.string()),
        pa.field("hs_code", pa.string()),
        pa.field("hs_version", pa.string()),
        pa.field("unit_default", pa.string()),
        pa.field("aliases", pa.list_(pa.string())),
    ]
)

SILVER_SCHEMAS: dict[str, pa.Schema] = {
    "indicators": SILVER_INDICATORS,
    "observations": SILVER_OBSERVATIONS,
    "documents": SILVER_DOCUMENTS,
    "geography": SILVER_GEOGRAPHY,
    "commodities": SILVER_COMMODITIES,
}
