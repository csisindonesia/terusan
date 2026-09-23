"""Sistem Informasi Desa — the village development index and the fund.

This is the other half of the village panel PODES is the spine of. Kemendesa
publishes the Indeks Desa Membangun — a composite score per village across
social, economic and environmental indicators, recomputed yearly and used to
classify each village from `sangat tertinggal` to `mandiri` — and the village
fund allocations that follow from it.

Where PODES describes what a village has, IDM scores what it is becoming, and
the fund says what it was given to do it with. The three joined by village code
are the longitudinal dataset the roadmap's phase six is about.

The portal renders server-side, so the sections land as pages: the index, the
fund, the population statistics and the publication shelf.
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

SITE = "https://sid.kemendesa.go.id"


class VillageInformation(PageSource):
    """The index, the fund and the village statistics sections."""

    meta = SourceMeta(
        slug="kemendesa-sid",
        name="Sistem Informasi Desa — Indeks Desa Membangun",
        organization="Kementerian Desa dan Pembangunan Daerah Tertinggal",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public sector information",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=0.5,
        # IDM is recomputed once a year and published mid-year.
        schedule="0 6 14 * *",
        notes=(
            "IDM scores and village classification, the village fund, and the "
            "ministry's village statistics. Joins to PODES and to the school and "
            "facility registers on the village code."
        ),
    )

    dataset = "village-development"

    urls = (
        f"{SITE}/",
        f"{SITE}/idm",
        f"{SITE}/village-fund",
        f"{SITE}/population-statistic",
        f"{SITE}/publikasi",
    )
