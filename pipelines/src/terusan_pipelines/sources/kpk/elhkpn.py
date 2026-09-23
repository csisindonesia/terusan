"""e-LHKPN — what public officials declare they own.

Every Indonesian official above a certain rank files an asset declaration, and
KPK publishes an announcement extract of each: land, vehicles, securities,
cash, debts and the net figure. Joined to the procurement and company
registers, it is the backbone of public-sector network analysis, which is what
phase four of the roadmap is.

It is published as a search portal, one official at a time, behind a session
and a captcha — there is no listing, no export, and no API. Scripted collection
would mean defeating an access control put there deliberately, so this source
does not attempt it.

What would make it collectable is asking: KPK releases bulk extracts to
researchers on request. Registered here so the request has a destination.
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


class Elhkpn(GatedSource):
    """Official asset declarations."""

    meta = SourceMeta(
        slug="kpk-elhkpn",
        name="e-LHKPN — Laporan Harta Kekayaan Penyelenggara Negara",
        organization="Komisi Pemberantasan Korupsi",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://elhkpn.kpk.go.id/",
        license="Public announcement extracts; personal data restrictions apply",
        update_frequency=UpdateFrequency.ANNUAL,
        active=False,
        schedule=None,
        notes=(
            "Asset declarations by official, filed annually. Personal data: any "
            "holding here is subject to KPK's terms and to Indonesian data "
            "protection law, which is a reason to receive an extract rather than "
            "to assemble one."
        ),
    )

    access = (
        "the portal searches one official at a time behind a captcha and publishes "
        "no export; request a bulk extract from KPK and land the approved files "
        "through this source"
    )
