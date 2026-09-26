"""VEWS yearly datasets, handed over as files rather than fetched.

The Violence Early Warning System codes collective violence in Indonesia one
incident at a time: a coder reads a report, files an incident under a district
with its date, its two sides, the form the violence took, what it was about,
who was hurt and whether anyone intervened. A second coder verifies it. The
result is published once a year as one spreadsheet per year — a Google Form's
responses, exported.

Nothing about that is fetchable. There is no portal, no listing page and no
stable URL: the files arrive by hand, one year at a time, and the only thing a
pipeline can do is pick them up from wherever they were put and land them with
the same provenance everything else in RAW carries. That is what this source
is — `MANUAL_UPLOAD`, in the registry's own terms, and with no `schedule`,
because there is nothing to run on a timer.

Drop the files in `tmp/vews` and run:

    terusan sources run vews-collective-violence

Somewhere else is fine too — `VEWS_DROP_DIR` in `.env`, or `--param dir=...`
for a one-off.

**The year comes from the filename**, because it is not reliably inside the
file. VEWS names its exports for the year they cover — `Yearly Dataset 2025 -
VEWS Dataset (v.1.0).xlsx` — and the year decides which figures the file is
authoritative for: a yearly export carries a tail of incidents dated to
neighbouring years (the 2021 file reaches back to 2017 and forward into 2022),
and counting those would let a file holding six weeks of a year overwrite the
file that holds all of it. So the year is read off the name, landed as the
partition, and the extractor counts only that year. A file whose name carries
no year is skipped and said so, rather than landed under a year that was
guessed.

Landing is content-addressed, so re-running over a directory whose files have
not changed writes nothing. A corrected re-export of a year lands beside the
one it corrects rather than replacing it — both are real, and which one the
figures came from is on every row.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ...storage.root import env_file, project_root, resolve_path
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

#: Where the files are looked for when nothing says otherwise. Under the
#: project rather than the lake: this is an inbox, not a layer — what lands in
#: RAW is the copy that matters, and the inbox can be emptied afterwards.
DEFAULT_DROP_DIR = "tmp/vews"

DROP_DIR_ENV = "VEWS_DROP_DIR"

#: The Bronze and RAW collection these land under.
DATASET = "collective-violence-early-warning"

#: What VEWS exports. The CSV is the 2021 export; later years are workbooks.
SUFFIXES = {".csv", ".xlsx", ".xlsm"}

#: The year an export covers, as VEWS writes it into the filename. Anchored to
#: `20` rather than any four digits so a version number cannot be read as a
#: year, and first-match so `2024 Yearly Dataset` and `Yearly Dataset (2023)`
#: both answer.
_YEAR = re.compile(r"(20\d{2})")


class VewsSettings(BaseSettings):
    """Where this source looks for the files it is handed.

    Read the way storage configuration and credentials are read — from the
    project's `.env`, with a real environment variable taking precedence — so
    a scheduled run on the NAS and a command in a terminal resolve the same
    directory. `os.getenv` alone would see only the second.
    """

    model_config = SettingsConfigDict(
        env_file=env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    directory: str | None = Field(default=None, alias=DROP_DIR_ENV)


@cache
def settings() -> VewsSettings:
    """The process's settings. Cached because it reads a file."""
    return VewsSettings()


def drop_dir(ctx: ScrapeContext) -> Path:
    """Where to look for the exports, most specific first.

    `--param dir=...` for one run, `VEWS_DROP_DIR` for a machine, and
    `tmp/vews` when nothing says otherwise. Relative paths anchor to the
    project root rather than the working directory, so the answer does not
    depend on which directory the command was typed in.
    """
    override = ctx.params.get("dir") or settings().directory
    return resolve_path(str(override)) if override else project_root() / DEFAULT_DROP_DIR


def edition(filename: str) -> str | None:
    """The year an export covers, or None if its name does not say."""
    match = _YEAR.search(filename)
    return match.group(1) if match else None


def exports(directory: Path) -> Iterator[Path]:
    """The export files in a drop directory, oldest year first.

    Excel's lock files (`~$...xlsx`) are real files with a workbook's suffix
    and none of a workbook's bytes, so a directory someone has a spreadsheet
    open in would otherwise fail to land.
    """
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith((".", "~$")):
            continue
        if path.suffix.lower() in SUFFIXES:
            yield path


class CollectiveViolenceEarlyWarning(Source):
    """Every VEWS export in the drop directory, landed as received."""

    meta = SourceMeta(
        slug="vews-collective-violence",
        name="VEWS — Violence Early Warning System",
        organization="CSIS Indonesia",
        category=Category.RESEARCH,
        source_type=SourceType.MANUAL_UPLOAD,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        country="ID",
        update_frequency=UpdateFrequency.ANNUAL,
        # Manual only. A timer over an inbox nobody has put anything in is a
        # run that fails every night for a year and is ignored by February.
        schedule=None,
        notes=(
            "One verified export per year, coded incident by incident from "
            f"media reports. Put the files in {DEFAULT_DROP_DIR} (or set "
            f"{DROP_DIR_ENV}) and run the source; the year is read off the "
            "filename."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        directory = drop_dir(ctx)
        if not directory.is_dir():
            # Not an error: a machine that has never been given the files is
            # the ordinary case, and the run should say so rather than fail.
            log.info("vews.no_drop_dir", directory=str(directory))
            return

        found = 0
        for path in exports(directory):
            year = edition(path.name)
            if year is None:
                log.warning(
                    "vews.unnamed_year",
                    path=str(path),
                    reason="filename carries no year, so the export's own year is unknown",
                )
                continue

            content = path.read_bytes()
            media_type, _ = mimetypes.guess_type(path.name)
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)

            found += 1
            yield Artifact(
                content=content,
                filename=path.name,
                dataset=DATASET,
                media_type=media_type,
                # The file's own timestamp, not a publication date: VEWS states
                # no release date and a copied file's mtime is when it was
                # copied. Kept in the metadata, where it reads as what it is.
                retrieved_at=datetime.now(UTC),
                partition=(f"year={year}",),
                metadata={
                    "title": f"VEWS yearly dataset {year}",
                    "document_type": "data_file",
                    "edition": year,
                    "file_modified": modified.isoformat(),
                    "delivered_as": path.name,
                },
            )

        log.info("vews.collected", directory=str(directory), files=found)
