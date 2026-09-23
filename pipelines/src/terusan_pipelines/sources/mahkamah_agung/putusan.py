"""Direktori Putusan — several million court decisions, behind a challenge.

Every decision from the Supreme Court down to the district courts is published
here in full text: the parties, the charge or claim, the reasoning and the
ruling. For research on how Indonesian law is actually applied there is nothing
comparable, which is why the source sheet marks it critical and the roadmap
gives court cases their own table.

`putusan3.mahkamahagung.go.id` sits behind a Cloudflare interstitial that
answers an unattended client with a JavaScript challenge rather than the
directory. That is a deliberate access control on a public archive, and the
answer to it is an arrangement, not a cleverer client: the court's IT bureau
grants access for bulk research, and several published corpora exist that were
collected under one.

Registered inactive, with the note pointing at the corpus route — the same
shape `bpk-peraturan-daerah` took, where a quarter of a million regulations
arrived as a finished corpus rather than as a crawl this repository ran.
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


class Putusan(GatedSource):
    """The decision directory."""

    meta = SourceMeta(
        slug="ma-putusan",
        name="Direktori Putusan Mahkamah Agung",
        organization="Mahkamah Agung Republik Indonesia",
        category=Category.REGULATIONS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        base_url="https://putusan3.mahkamahagung.go.id/",
        license="Public domain — court decisions are not subject to copyright",
        update_frequency=UpdateFrequency.DAILY,
        active=False,
        schedule=None,
        notes=(
            "Full-text decisions from every level of the Indonesian judiciary. "
            "Access, not parsing, is what holds this back."
        ),
    )

    access = (
        "the directory answers unattended clients with a Cloudflare challenge; "
        "request bulk access from the Supreme Court's IT bureau, or import an "
        "existing research corpus and land it under this slug"
    )
