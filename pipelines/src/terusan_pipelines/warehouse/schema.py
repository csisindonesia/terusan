"""Arrow schemas for the analytical layers.

Schemas are declared rather than inferred. Inference reads types off whatever
happened to arrive first, so a column that is all-integers in January becomes a
string in February and the partition stops being readable as one dataset
(program.md §41).
"""

from __future__ import annotations

import pyarrow as pa

#: Columns every Bronze table carries, whatever the source material.
#: These are the fields listed in program.md §6 — they make a row traceable
#: back to the byte range it came from.
BRONZE_PROVENANCE_FIELDS: list[pa.Field] = [
    pa.field("document_id", pa.string(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("source_type", pa.string()),
    pa.field("source_url", pa.string()),
    pa.field("content_hash", pa.string(), nullable=False),
    pa.field("raw_path", pa.string(), nullable=False),
    pa.field("original_filename", pa.string()),
    pa.field("media_type", pa.string()),
    pa.field("published_at", pa.date32()),
    pa.field("retrieved_at", pa.timestamp("us", tz="UTC")),
    pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("parser_version", pa.string()),
    pa.field("pipeline_version", pa.string()),
]

#: Bronze documents: extracted text, still unnormalized (program.md §6, §13).
BRONZE_DOCUMENTS = pa.schema(
    [
        *BRONZE_PROVENANCE_FIELDS,
        pa.field("document_type", pa.string()),
        pa.field("title", pa.string()),
        pa.field("language", pa.string()),
        pa.field("raw_text", pa.string()),
        pa.field("raw_html", pa.string()),
        pa.field("page_count", pa.int32()),
        # Anything the extractor learned that has no column yet. Better here
        # than as a column per source, which would make the schema a union of
        # fifty extractors.
        pa.field("metadata", pa.string()),
    ]
)

#: Bronze tabular data: CSV/XLSX/JSON rows, one Bronze row per source row,
#: values still as text because Bronze does not decide types (§6).
BRONZE_RECORDS = pa.schema(
    [
        *BRONZE_PROVENANCE_FIELDS,
        pa.field("dataset", pa.string(), nullable=False),
        pa.field("row_number", pa.int64(), nullable=False),
        pa.field("columns", pa.map_(pa.string(), pa.string())),
    ]
)


def empty(schema: pa.Schema) -> pa.Table:
    """An empty table with the given schema.

    Used so a run that extracts nothing still writes a well-typed file rather
    than leaving a gap that later reads as a schema change.
    """
    return pa.Table.from_pylist([], schema=schema)


def table_from_rows(rows: list[dict], schema: pa.Schema) -> pa.Table:
    """Build a table from dicts against a declared schema.

    Goes through the schema rather than inferring and casting afterwards:
    inference reads a Python dict as a struct, and Arrow has no struct-to-map
    cast, so a map column like Bronze's `columns` cannot be recovered after the
    fact. Missing keys become nulls.
    """
    return pa.Table.from_pylist(rows, schema=schema)


def conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Reorder and cast a table to match a declared schema.

    Missing nullable columns are filled with nulls; a missing non-nullable
    column is an error, because silently nulling a provenance field is how a
    row loses its way back to the source.
    """
    columns = []
    for field in schema:
        if field.name in table.column_names:
            columns.append(table.column(field.name).cast(field.type))
        elif field.nullable:
            columns.append(pa.nulls(table.num_rows, type=field.type))
        else:
            raise ValueError(
                f"column {field.name!r} is required by the schema but missing from the table"
            )
    return pa.Table.from_arrays(columns, schema=schema)
