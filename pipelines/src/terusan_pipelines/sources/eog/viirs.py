"""VIIRS nighttime lights — economic activity where no statistics reach.

The annual composites measure how brightly each square kilometre of Indonesia
is lit. It is the standard proxy for local economic activity where official
figures stop — below the regency, between census years, in places where nothing
is measured — and the roadmap's village panel is one of the things it is for.

The Colorado School of Mines serves the composites behind Keycloak: the
directory listing itself redirects to a login form. Registration is free and
immediate, and the credential is an OAuth token exchanged per download, which
is a different shape from every other credential in this package — so it is
written down rather than half-implemented, and this source stays gated until
someone registers.

The lit-area figures are also derived products rather than observations, and
what lands when this opens should be the annual composite as published, not a
zonal summary computed on the way in: the zones we would summarise by are the
ones the warehouse is still deciding on.
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


class NighttimeLights(GatedSource):
    """The annual VIIRS composites."""

    meta = SourceMeta(
        slug="eog-viirs-nighttime-lights",
        name="VIIRS Nighttime Lights — annual composites",
        organization="Earth Observation Group, Colorado School of Mines",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url="https://eogdata.mines.edu/products/vnl/",
        # Global rasters; Indonesia is cut out downstream.
        country=None,
        license="Free for non-commercial use with attribution",
        update_frequency=UpdateFrequency.ANNUAL,
        active=False,
        schedule=None,
        notes=(
            "Annual global composites, ~1 GB per year per band. The proxy for "
            "local economic activity below the level official statistics reach."
        ),
    )

    access = (
        "eogdata.mines.edu serves the composites behind a Keycloak login; register "
        "free at eogdata.mines.edu, then exchange the credential for an OAuth "
        "token per download and land the annual composites through this source"
    )
