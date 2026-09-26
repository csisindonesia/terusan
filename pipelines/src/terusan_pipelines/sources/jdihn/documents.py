"""JDIHN — the federated index of every JDIH in the country.

BPK's portal, already collected as `bpk-peraturan-daerah` and
`bpk-peraturan-pusat`, is one legal documentation centre among several hundred:
each ministry, each province, each regency runs its own. JDIHN is the index
across all of them, which makes it the place to find a regulation that BPK
never picked up — and the place to see which institutions publish at all.

The portal is a React application whose document downloads go through
`/api/doc/{id}/file`, keyed by an id that only the rendered listing carries. So
the listings are what land, and the download path is recorded here for the day
an extractor reads ids out of them and a second source fetches the documents.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import PageSource

SITE = "https://jdihn.go.id"

#: How a document is fetched once its id is known. Not used yet; written down
#: because finding it again means reading a bundled React chunk.
DOCUMENT_FILE = f"{SITE}/api/doc/{{document_id}}/file?action=download"


class Documents(PageSource):
    """The network's landing and directory pages."""

    meta = SourceMeta(
        slug="jdihn-documents",
        name="JDIHN — Jaringan Dokumentasi dan Informasi Hukum Nasional",
        organization="Badan Pembinaan Hukum Nasional (BPHN)",
        category=Category.REGULATIONS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public domain — Indonesian law is not subject to copyright",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=0.5,
        # Daily, 05:00, as its frequency says. The network indexes new
        # regulations every working day; a weekly run left `make daily` and
        # the hourly scheduler disagreeing about how often this is collected.
        schedule="0 5 * * *",
        notes=(
            "Federated index across several hundred member JDIHs. Documents "
            "download through /api/doc/{id}/file, which needs ids read out of "
            "these listings first."
        ),
    )

    dataset = "jdihn-listings"

    #: The homepage carries the newest documents as `/doc/{id}` links, the
    #: search page carries the rest, and `/status` is the network's own account
    #: of which member JDIHs are reachable — which is the part that says why a
    #: province's regulations stopped arriving.
    urls = (f"{SITE}/", f"{SITE}/search?c=all", f"{SITE}/status")
