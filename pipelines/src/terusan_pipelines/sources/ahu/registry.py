"""The company register and the beneficial ownership register.

Who owns an Indonesian company, and who ultimately benefits from it, are two
questions with two systems behind them. AHU Online is the registry of legal
entities: incorporation, the deed, the directors, the capital. The beneficial
ownership portal is the newer one, built under the anti-money-laundering
regime, where a company must declare the natural person behind it.

Together they are what turns a list of procurement winners or mining permit
holders into a network, which is phase four of the roadmap and the reason both
are in the source sheet.

Neither is open. AHU Online serves company profiles to authenticated users and
charges for documents; the beneficial ownership portal puts everything behind a
login. Both are registered here to hold the slug, the licence position and the
access note — and to record that the data exists and the lake does not hold it,
which is a different statement from silence.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import GatedSource


class CompanyRegistry(GatedSource):
    """The register of legal entities."""

    meta = SourceMeta(
        slug="ahu-company-registry",
        name="AHU Online — Perseroan dan badan hukum",
        organization="Kementerian Hukum — Ditjen Administrasi Hukum Umum",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://ahu.go.id/",
        license="Per-document fee; redistribution restricted",
        update_frequency=UpdateFrequency.DAILY,
        active=False,
        schedule=None,
        notes=(
            "Incorporation, deeds, directors and capital for Indonesian legal "
            "entities. The identity layer under any ownership network."
        ),
    )

    access = (
        "AHU Online serves profiles to authenticated accounts and charges per "
        "document; arrange an institutional account or a bulk licence, then land "
        "the received extracts under this slug"
    )


class BeneficialOwnership(GatedSource):
    """The register of natural persons behind companies."""

    meta = SourceMeta(
        slug="ahu-beneficial-ownership",
        name="Portal Pemilik Manfaat (Beneficial Ownership)",
        organization="Kementerian Hukum — Ditjen Administrasi Hukum Umum",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://bo.ahu.go.id/",
        license="Restricted; personal data of declared owners",
        update_frequency=UpdateFrequency.DAILY,
        active=False,
        schedule=None,
        notes=(
            "Declared beneficial owners under the anti-money-laundering regime. "
            "Personal data: a holding here is subject to Indonesian data "
            "protection law as well as to AHU's terms."
        ),
    )

    access = (
        "bo.ahu.go.id serves everything behind a login; request access as a "
        "research institution and land the approved extracts under this slug"
    )
