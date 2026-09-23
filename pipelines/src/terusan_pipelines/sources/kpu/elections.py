"""KPU — candidates, parties and results, as InfoPemilu renders them.

Election data is the one holding here that has no second source: nobody else
publishes who stood, who won, and how many votes were cast in each of 800,000
polling stations. The roadmap builds political geography on it.

KPU publishes it across three systems and only one answers a script. The
results system for a given election — `pemilu2024.kpu.go.id` and its
predecessors — is stood up per election and taken down afterwards, and did not
answer at all when this was written. InfoPemilu, the standing portal, renders
the candidate lists, the party lists and the elected members server-side, and
those pages are what land here. JDIH KPU holds the decisions that make each of
those official, including the one that declares the result.

The pages are landed rather than parsed for the usual reason: InfoPemilu is
rebuilt for every election cycle, and a parser written against this cycle's
markup would be the thing that breaks when the next one starts.
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

INFO = "https://infopemilu.kpu.go.id"


class Elections(PageSource):
    """Candidate and party pages, plus the legal decisions behind them."""

    meta = SourceMeta(
        slug="kpu-elections",
        name="KPU — InfoPemilu: calon, partai dan hasil",
        organization="Komisi Pemilihan Umum",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=INFO,
        license="Public sector information",
        # Continuous within a cycle, silent between them.
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        # Weekly. Between elections these pages are static, and landing is
        # content-addressed, so a quiet week writes nothing; during a cycle a
        # week is soon enough for lists that change by decree.
        schedule="0 5 * * 3",
        notes=(
            "InfoPemilu candidate and party pages, and the KPU decisions that "
            "make them official. Per-polling-station results live in the "
            "election-specific system, which is stood up per cycle."
        ),
    )

    dataset = "kpu-elections"

    urls = (
        f"{INFO}/",
        # The legislative candidate lists, national and regional.
        f"{INFO}/Pemilu/Dct_dpr",
        f"{INFO}/Pemilu/Dpd/",
        # Presidential and head-of-region tickets.
        f"{INFO}/Pemilihan/Pasangan_calon",
        # The party numbering, which is how a ballot is read.
        f"{INFO}/Pemilu/Pengundian_parpol",
        # The decisions: regulations, and the decrees declaring results.
        "https://jdih.kpu.go.id/peraturan-kpu",
        "https://jdih.kpu.go.id/keputusan-kpu",
    )
