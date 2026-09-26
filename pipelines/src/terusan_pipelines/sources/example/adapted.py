"""An existing standalone script, brought in without being edited.

`_ad_hoc_scraper` below stands in for a script as found: it writes relative
paths, takes no arguments, and knows nothing about RAW. `legacy_source` gives
it landing and provenance anyway.
"""

from __future__ import annotations

from pathlib import Path

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..legacy import legacy_source


def _ad_hoc_scraper() -> None:
    """Stands in for an existing script. Unchanged from how it was found."""
    out = Path("out")
    out.mkdir(exist_ok=True)
    (out / "regulations.csv").write_text("number,year,title\n12,2026,Example Regulation\n")


ExampleLegacy = legacy_source(
    _ad_hoc_scraper,
    meta=SourceMeta(
        slug="example-legacy",
        name="Example — Legacy Scraper",
        organization="Example Agency",
        category=Category.REGULATIONS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        update_frequency=UpdateFrequency.IRREGULAR,
        # A template for porting a script, not a publisher: there is nothing
        # at the other end to collect.
        active=False,
        notes="Adapted from a standalone script; convert to a native Source when touched.",
    ),
    dataset="bulk",
    # The subdirectory the script writes into.
    output_subdir="out",
)
