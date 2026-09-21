"""BPK JDIH — perda and perkada, imported rather than scraped.

A quarter of a million regional regulations arrived already crawled, converted
from PDF and segmented into bab/pasal/ayat by a separate project. Re-running
that here would re-fetch 266,561 PDFs from a government host to arrive at
Parquet we already hold, so the corpus is landed directly into silver by
`scripts/import-regulations.sh`.

This class exists so the import is not anonymous. The registry is what the
silver `sources` dimension is built from (`terusan silver dimensions`), and the
serving layer joins every dataset to it — without a record here the regulations
would be the only data in the lake with no publisher, no licence and no link
behind them.

`collect` refuses rather than pretending. When the crawler is ported into this
repository, its logic replaces the body of `collect`, the collection method
becomes `SCRAPE`, and nothing downstream has to learn a new slug.
"""

from __future__ import annotations

from collections.abc import Iterator

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


class ImportedCorpus(RuntimeError):
    """Raised when a run is asked of a source that is imported, not fetched."""


class BPKPeraturanDaerah(Source):
    """Regional regulations from BPK's legal documentation portal."""

    meta = SourceMeta(
        slug="bpk-peraturan-daerah",
        name="BPK JDIH — Peraturan Daerah dan Peraturan Kepala Daerah",
        organization="Badan Pemeriksa Keuangan",
        category=Category.REGULATIONS,
        source_type=SourceType.OFFICIAL_PORTAL,
        # How the bytes reached this lake, which is the truth a reader needs:
        # they were handed over as a finished corpus, not fetched by us.
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://peraturan.bpk.go.id",
        country="ID",
        license="Public domain — Indonesian law is not subject to copyright",
        # BPK publishes continuously; the corpus is refreshed in batches.
        update_frequency=UpdateFrequency.IRREGULAR,
        schedule=None,
        notes=(
            "Imported corpus, not scraped here. Run scripts/import-regulations.sh "
            "to land it into silver. 266,561 records, 1952–2026; 245,804 parsed "
            "into sections, the rest catalogued with the reason their text is "
            "missing."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        raise ImportedCorpus(
            f"{self.meta.slug} is an imported corpus, not a scraper. "
            "Land it with scripts/import-regulations.sh."
        )
        yield  # pragma: no cover - unreachable, keeps the signature a generator
