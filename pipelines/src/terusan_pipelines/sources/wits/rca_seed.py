"""The HS six-digit RCA base years, handed over as Stata files.

The WITS API stops at sector level (see `tradestats.py`), so the product-level
history this warehouse starts from is two files the CSIS trade team built by
hand and put in `tmp/rca`:

- **`rca clean_EG lists.dta`** — Indonesia's exports to the world by HS 1988/92
  subheading, 1995–2025, from a WITS bulk download (UN Comtrade underneath),
  with the world's exports of each product beside it and the RCA computed from
  the two. Merged onto it are the environmental goods lists — TESSD, APEC,
  ACCTS, SAGEA, EU–NZ, UK–NZ, OECD, UNCTAD — which became
  `reference/trade/environmental-goods.csv`. Landed as received: 27 MB.

- **`RCA_country_product_year_hs6_raw.dta`** — the Harvard Growth Lab's Atlas
  of Economic Complexity at HS92 six digits, every country, 1995–2024, with the
  country's ECI, COI, diversity and growth projection and the world's totals
  merged on. 3.7 GB, which is not landed whole: the rows for the economies in
  `trade.REPORTERS` are, as Parquet. The original is public — Harvard
  Dataverse, doi:10.7910/DVN/T4CHWJ, CC0 — so what the subset drops can be
  fetched again rather than being lost, and a warehouse scoped to Indonesia and
  its peers should not carry two hundred other countries' product trade in RAW
  to say so. That is a departure from landing bytes as received (program.md
  §2.1), taken knowingly; the metadata records the file it was cut from, its
  size, and the filter.

Which file is which is read off the variables Stata stored, not the filename,
so a renamed copy still lands under the right collection.

Drop the files in `tmp/rca` and run:

    terusan sources run rca-seed

`RCA_DROP_DIR` in `.env`, or `--param dir=...`, to look elsewhere. Manual only:
the years after these come from `wits-tradestats`.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ...storage.root import env_file, project_root, resolve_path
from ...trade import REPORTERS
from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)

log = structlog.get_logger(__name__)

DEFAULT_DROP_DIR = "tmp/rca"

DROP_DIR_ENV = "RCA_DROP_DIR"

#: Indonesia's HS6 exports and RCA from WITS, with the goods lists merged on.
WITS_DATASET = "rca-indonesia-hs6"

#: The Atlas panel, cut to the economies this warehouse compares.
ATLAS_DATASET = "rca-atlas-hs6"

#: Variables that identify each file. Stata stores them in the header, which
#: is readable without reading the rows.
_WITS_VARIABLES = {"reporteriso3", "idnexport", "wldexport", "hs92"}
_ATLAS_VARIABLES = {"country_iso3_code", "product_hs92_code", "export_value"}

#: Rows per read. The Atlas file is some twenty million rows; a million at a
#: time keeps the read under a couple of gigabytes of memory.
CHUNK_ROWS = 1_000_000

DATA_FILE_MEDIA_TYPE = "application/x-stata-dta"


class RcaSettings(BaseSettings):
    """Where this source looks for the files it is handed."""

    model_config = SettingsConfigDict(
        env_file=env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    directory: str | None = Field(default=None, alias=DROP_DIR_ENV)


@cache
def settings() -> RcaSettings:
    return RcaSettings()


def drop_dir(ctx: ScrapeContext) -> Path:
    """`--param dir=...`, then `RCA_DROP_DIR`, then `tmp/rca`."""
    override = ctx.params.get("dir") or settings().directory
    return resolve_path(str(override)) if override else project_root() / DEFAULT_DROP_DIR


def variables(path: Path) -> set[str]:
    """The variable names a Stata file declares, without reading its rows."""
    import pandas as pd

    with pd.read_stata(path, iterator=True) as reader:
        return set(reader.variable_labels())


def kind(path: Path) -> str | None:
    """`wits`, `atlas`, or None for a file that is neither."""
    names = variables(path)
    if names >= _WITS_VARIABLES:
        return "wits"
    if names >= _ATLAS_VARIABLES:
        return "atlas"
    return None


def atlas_subset(path: Path, reporters: tuple[str, ...] = REPORTERS) -> tuple[bytes, int, int]:
    """The Atlas rows for `reporters`, as Parquet, and how many rows in and out.

    Sorted, so the same file and the same reporters make the same bytes and a
    re-run deduplicates rather than landing a second copy.
    """
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    keep = set(reporters)
    frames = []
    rows_in = 0
    with pd.read_stata(path, chunksize=CHUNK_ROWS, convert_categoricals=False) as reader:
        for chunk in reader:
            rows_in += len(chunk)
            frames.append(chunk[chunk["country_iso3_code"].isin(keep)])

    subset = pd.concat(frames, ignore_index=True)
    subset = subset.sort_values(["country_iso3_code", "product_hs92_code", "year"])
    table = pa.Table.from_pandas(subset, preserve_index=False)

    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue(), rows_in, len(subset)


class RcaSeed(Source):
    """The two RCA working files in the drop directory, landed once."""

    meta = SourceMeta(
        slug="rca-seed",
        name="RCA base years — WITS and Atlas HS6 working files",
        organization="CSIS Indonesia",
        category=Category.STATISTICS,
        source_type=SourceType.MANUAL_UPLOAD,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        country=None,
        license=(
            "WITS / UN Comtrade terms of use (Indonesia HS6 file); "
            "Atlas of Economic Complexity, CC0 (country panel)"
        ),
        update_frequency=UpdateFrequency.IRREGULAR,
        schedule=None,
        notes=(
            f"Put the .dta files in {DEFAULT_DROP_DIR} (or set {DROP_DIR_ENV}) and run "
            "the source. The Atlas panel is cut to Indonesia, ASEAN and peers before "
            "landing; the WITS file lands as received."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        directory = drop_dir(ctx)
        if not directory.is_dir():
            log.info("rca_seed.no_drop_dir", directory=str(directory))
            return

        for path in sorted(directory.glob("*.dta")):
            if path.name.startswith((".", "~$")):
                continue
            which = kind(path)
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

            if which == "wits":
                yield Artifact(
                    content=path.read_bytes(),
                    filename=path.name,
                    dataset=WITS_DATASET,
                    media_type=DATA_FILE_MEDIA_TYPE,
                    retrieved_at=datetime.now(UTC),
                    metadata={
                        "title": "Indonesia HS6 exports and RCA, with environmental goods lists",
                        "document_type": "data_file",
                        "publisher": "World Bank WITS (UN Comtrade), compiled by CSIS",
                        "delivered_as": path.name,
                        "file_modified": modified,
                    },
                )
            elif which == "atlas":
                content, rows_in, rows_out = atlas_subset(path)
                log.info("rca_seed.atlas_subset", rows_in=rows_in, rows_out=rows_out)
                yield Artifact(
                    content=content,
                    filename="atlas-hs92-country-product-year-6-reporters.parquet",
                    dataset=ATLAS_DATASET,
                    media_type="application/vnd.apache.parquet",
                    source_url="https://doi.org/10.7910/DVN/T4CHWJ",
                    retrieved_at=datetime.now(UTC),
                    metadata={
                        "title": "Atlas of Economic Complexity, HS92 6-digit, Indonesia and peers",
                        "document_type": "data_file",
                        "publisher": "Growth Lab at Harvard University",
                        "delivered_as": path.name,
                        "delivered_size_bytes": path.stat().st_size,
                        "file_modified": modified,
                        "subset": "country_iso3_code in reporters",
                        "reporters": list(REPORTERS),
                        "rows_delivered": rows_in,
                        "rows_landed": rows_out,
                    },
                )
            else:
                log.warning("rca_seed.unrecognised", path=str(path))
