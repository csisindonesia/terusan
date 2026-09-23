"""Kemendagri — the administrative regions, and the codes that name them.

Every subnational figure in this lake is keyed by a region code, and the codes
are Kemendagri's: `31.71.03.1001` is Kemayoran, and the ministry decides when
that changes, when a regency splits, and what the new one is called. Holding
the register is what lets the warehouse join BPS to BNPB to the election
results at all — without it, three ministries' spellings of the same regency
are three different places.

The ministry publishes the definitive list as a decree, a PDF appendix running
to hundreds of pages. What it also publishes, and what this lands, is the
backend behind `kemendagri.go.id/data-profil-daerah`: the regions as its own
site reads them, with the code, the name and the profile, as JSON. That is the
same register in a form that does not need a PDF parser, and the decree remains
the authority when the two disagree — which is worth knowing before trusting
this to be current the week after a split.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import ApiSource, Endpoint

BACKEND = "https://backend.kemendagri.go.id/api/v1"

#: The whole register in one response. The endpoint takes a `kodeProv`
#: parameter and ignores it — every call answers `"kodeProv": "all"` — so
#: asking per province would be 38 requests for 38 copies of one file.
PROFILES = f"{BACKEND}/data-profil-daerah/public"


class Wilayah(ApiSource):
    """The region register, as the ministry's own site reads it."""

    meta = SourceMeta(
        slug="kemendagri-wilayah",
        name="Kemendagri — Kode dan Data Wilayah Administrasi",
        organization="Kementerian Dalam Negeri",
        category=Category.GOVERNMENT,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://www.kemendagri.go.id/",
        license="Public sector information",
        # Codes change when a region is created, split or renamed, which is a
        # decree rather than a release cycle.
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=1.0,
        # Weekly. A split is rare and the file is small; a week is soon enough
        # to notice one and cheap enough not to think about.
        schedule="0 2 * * 1",
        notes=(
            "Regional profiles and their codes, from the backend behind "
            "data-profil-daerah. The Permendagri appendix remains the authority; "
            "this is the machine-readable rendering of the same register."
        ),
    )

    endpoints = (
        Endpoint(
            dataset="region-profiles",
            url=PROFILES,
            filename="data-profil-daerah.json",
            metadata={
                "coverage": "every province and the regencies and cities under it",
                "authority": "Permendagri on the code and data of administrative regions",
            },
        ),
    )
