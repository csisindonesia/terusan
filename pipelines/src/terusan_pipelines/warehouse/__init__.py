"""Parquet storage and analytical querying.

Writing goes through `ParquetWriter`, which enforces the two rules that keep a
lake queryable: partition on something that prunes (program.md §46), and do not
write a file per document (§47). Reading goes through `Warehouse`, a DuckDB
session already pointed at the right backend.
"""

from .compaction import (
    CompactionResult,
    compact_layer,
    compact_partition,
    find_partitions,
)
from .query import Warehouse, warehouse
from .schema import (
    BRONZE_DOCUMENTS,
    BRONZE_PROVENANCE_FIELDS,
    BRONZE_RECORDS,
    conform,
    empty,
    table_from_rows,
)
from .writer import (
    DEFAULT_ROW_GROUP_SIZE,
    DEFAULT_TARGET_BYTES,
    MAX_PARTITION_CARDINALITY,
    MAX_PARTITION_RATIO,
    BadPartitionKey,
    ParquetWriter,
    WriteResult,
    check_partition_keys,
)

__all__ = [
    "BRONZE_DOCUMENTS",
    "BRONZE_PROVENANCE_FIELDS",
    "BRONZE_RECORDS",
    "DEFAULT_ROW_GROUP_SIZE",
    "DEFAULT_TARGET_BYTES",
    "BadPartitionKey",
    "CompactionResult",
    "ParquetWriter",
    "Warehouse",
    "WriteResult",
    "MAX_PARTITION_CARDINALITY",
    "MAX_PARTITION_RATIO",
    "check_partition_keys",
    "compact_layer",
    "compact_partition",
    "conform",
    "empty",
    "find_partitions",
    "table_from_rows",
    "warehouse",
]
