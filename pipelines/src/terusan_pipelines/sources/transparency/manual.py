"""Meta's and X's reports, landed from files a researcher saves by hand.

Neither can be collected by machine, for different reasons, both checked on
2026-10-22:

- **Meta.** The content restrictions report at
  https://transparency.meta.com/reports/content-restrictions/country/ID/ renders
  its figures in the browser — the served HTML holds no number at all — and
  Meta's robots.txt states that automated collection is prohibited without
  express written permission. Nothing here asks for the page by machine.
- **X.** The country page at https://transparency.x.com/en/reports/countries/id
  draws its tables from an internal endpoint its JavaScript calls, which X does
  not document. The observatory's rules forbid building on an undocumented
  endpoint, and a scraper that did would break without notice.

So both are drop directories. A researcher opens the report, saves what it
offers — an export where there is one, the page or a PDF print where there is
not — and runs the source, which lands every file as received with the date it
was saved and who the report belongs to. Landing is content-addressed, so
running it again over the same files writes nothing.

No extractor reads these yet: what Meta's export looks like has not been
verified, and a reader written against a guess would be wrong in ways nothing
would catch. The files sit in RAW, with provenance, until one is.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import structlog

from ...storage.root import project_root, resolve_path
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

#: Where the files are put, one directory per provider beneath it.
DEFAULT_DROP_ROOT = "tmp/transparency"

#: What a saved report can be. Anything else in the directory — a `.DS_Store`,
#: a half-written download — is not a report and is left alone.
ACCEPTED_SUFFIXES = frozenset({".csv", ".xlsx", ".json", ".pdf", ".html", ".htm", ".png"})

_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
}


class ManualReportSource(Source, abstract=True):
    """Every saved report in this provider's drop directory, landed as received."""

    #: The subdirectory of `tmp/transparency` this provider's files go in.
    provider: str

    #: The report a researcher should open, for the landing record.
    report_url: str

    dataset: str = "saved-reports"

    def drop_dir(self, ctx: ScrapeContext) -> Path:
        """`--param dir=...`, then `tmp/transparency/<provider>`."""
        override = ctx.params.get("dir")
        if override:
            return resolve_path(str(override))
        return project_root() / DEFAULT_DROP_ROOT / self.provider

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        directory = self.drop_dir(ctx)
        if not directory.is_dir():
            log.info("transparency.no_drop_dir", source=self.meta.slug, directory=str(directory))
            return

        landed = 0
        for path in sorted(directory.iterdir()):
            if ctx.limit is not None and landed >= ctx.limit:
                return
            if not path.is_file() or path.name.startswith((".", "~$")):
                continue
            suffix = path.suffix.lower()
            if suffix not in ACCEPTED_SUFFIXES:
                log.warning("transparency.unaccepted_file", source=self.meta.slug, path=str(path))
                continue

            saved = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            yield Artifact(
                content=path.read_bytes(),
                filename=path.name,
                dataset=self.dataset,
                source_url=self.report_url,
                media_type=_MEDIA_TYPES[suffix],
                # When the researcher saved it, which is as close to a
                # retrieval time as a hand-saved file has.
                retrieved_at=saved,
                metadata={
                    "provider": self.provider,
                    "report_page": self.report_url,
                    "source_method": "manual",
                    "delivered_as": path.name,
                    "file_modified": saved.isoformat(),
                },
            )
            landed += 1


class MetaContentRestrictions(ManualReportSource):
    """Meta's content restrictions based on local law, saved by hand."""

    provider = "meta"
    report_url = "https://transparency.meta.com/reports/content-restrictions/country/ID/"

    meta = SourceMeta(
        slug="meta-content-restrictions",
        name="Meta — Content restrictions based on local law",
        organization="Meta",
        category=Category.RESEARCH,
        source_type=SourceType.MANUAL_UPLOAD,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url=report_url,
        country=None,
        license="Meta Transparency Center; automated collection prohibited by robots.txt",
        update_frequency=UpdateFrequency.IRREGULAR,
        schedule=None,
        notes=(
            "Facebook and Instagram items restricted in Indonesia under local law. "
            "The page renders client-side and robots.txt forbids automated "
            f"collection: save the report into {DEFAULT_DROP_ROOT}/meta and run the "
            "source."
        ),
    )


class XRemovalRequests(ManualReportSource):
    """X's removal requests for Indonesia, saved by hand."""

    provider = "x"
    report_url = "https://transparency.x.com/en/reports/countries/id"

    meta = SourceMeta(
        slug="x-removal-requests",
        name="X — Removal requests (legal demands) by country",
        organization="X Corp.",
        category=Category.RESEARCH,
        source_type=SourceType.MANUAL_UPLOAD,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url=report_url,
        country=None,
        license="X Transparency Center",
        update_frequency=UpdateFrequency.IRREGULAR,
        schedule=None,
        notes=(
            "Legal demands, accounts specified, and accounts and posts withheld. The "
            "figures come from an undocumented internal endpoint, which the "
            f"observatory does not build on: save the report into {DEFAULT_DROP_ROOT}/x "
            "and run the source."
        ),
    )
