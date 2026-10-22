"""A research export of the observatory's tables.

Dated, so an export cited in a paper stays the export it was: a later build
writes beside it rather than over it. The data dictionary and the methodology
travel with the figures, because a CSV of counts with no definition of a count
is how a request becomes an item in someone else's chart.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..storage import Layer, StorageResolver
from ..storage.root import project_root
from ..warehouse import Warehouse
from . import model as m

_TABLES = (
    m.OBSERVATIONS_TABLE,
    m.REPORTS_TABLE,
    m.METHODOLOGY_TABLE,
    m.QUARANTINE_TABLE,
    m.RUNS_TABLE,
)

_DOCS = ("data_dictionary.md", "methodology.md", "source_registry.md")


def export(resolver: StorageResolver, fmt: str = "csv") -> Path:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    target = Path(resolver.resolve_for_write(Layer.EXPORTS, "observatory", stamp))
    option = "(FORMAT csv, HEADER)" if fmt == "csv" else "(FORMAT parquet, COMPRESSION zstd)"

    with Warehouse(resolver) as w:
        for table in _TABLES:
            source = w.source(Layer.SILVER, table)
            # The observations export is the current figures: the revisions are
            # in Silver for anyone auditing, and a researcher summing an export
            # should not sum a figure and its correction.
            where = "WHERE is_current" if table == m.OBSERVATIONS_TABLE else ""
            path = target / f"{table}.{fmt}"
            w.connection.execute(f"COPY (SELECT * FROM {source} {where}) TO '{path}' {option}")

    docs = project_root() / "docs" / "observatory"
    for name in _DOCS:
        if (docs / name).is_file():
            shutil.copy2(docs / name, target / name)
    return target
