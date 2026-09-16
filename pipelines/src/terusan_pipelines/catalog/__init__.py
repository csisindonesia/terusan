"""The PostgreSQL application catalog.

Operational state only: the source registry, dataset metadata, run history,
permissions and audit (program.md §48). The analytical warehouse stays in
Parquet — duplicating it here would mean two answers to every question.
"""

from .connection import (
    CatalogUnavailable,
    connect,
    database_url,
    is_available,
    register_enums,
)
from .datasets import (
    DatasetStats,
    list_datasets,
    next_version,
    record_version,
    upsert_dataset,
)
from .reporting import (
    BRONZE_DATASETS,
    CatalogReporter,
    NullReporter,
    Reporter,
    reporter,
)
from .runs import (
    RunHandle,
    definition_for_source,
    fail_run,
    recent_runs,
    record_run,
    stale_runs,
    upsert_definition,
)
from .sources import SyncResult, source_id, sync_sources

__all__ = [
    "BRONZE_DATASETS",
    "CatalogReporter",
    "CatalogUnavailable",
    "DatasetStats",
    "NullReporter",
    "Reporter",
    "RunHandle",
    "SyncResult",
    "connect",
    "database_url",
    "definition_for_source",
    "fail_run",
    "is_available",
    "list_datasets",
    "next_version",
    "recent_runs",
    "reporter",
    "register_enums",
    "record_run",
    "record_version",
    "source_id",
    "stale_runs",
    "sync_sources",
    "upsert_dataset",
    "upsert_definition",
]
