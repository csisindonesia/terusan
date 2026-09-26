"""The newspapers that get read.

`reference/news/outlets.csv` is the list, and it is reference data rather than
configuration: which papers are monitored decides what the dataset can possibly
contain, so a change to it is a change to the figures and belongs in a commit
with a reason attached.

Two columns exist only because the sheet the list came from pointed at crime
desks. `section_path` is that section, kept as a listing to read when an
outlet's search box ignores its query, and `adapter` names the shape of that
search box. Neither is a filter: an article found down either route is coded
the same way, and an outlet whose section is a province rather than a crime
desk is no different to the crawl.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ..storage.root import project_root

#: Where the list lives, relative to the repository root.
OUTLETS_CSV = Path("reference") / "news" / "outlets.csv"


@dataclass(frozen=True, slots=True)
class Outlet:
    """One newspaper, as the reference list records it."""

    outlet: str
    province: str

    #: BPS-keyed province from `reference/geography`, or `IDN` for a national
    #: paper. Reports from a national paper are placed by what the article
    #: says, not by where the paper sits.
    geo_id: str
    bps_code: str

    host: str
    base_url: str

    #: The crime or law section the source list pointed at. Empty for outlets
    #: listed by their front page.
    section_path: str

    #: Which search-page shape the host runs; see `news.search`.
    adapter: str

    active: bool

    #: Why a row was corrected or retired. Carried into Bronze so a reader can
    #: tell a paper that published nothing from one that no longer exists.
    note: str

    @property
    def section_url(self) -> str | None:
        """The listing page to read when search returns nothing."""
        return f"{self.base_url}{self.section_path}" if self.section_path else None

    @property
    def national(self) -> bool:
        return self.geo_id == "IDN"


def _rows(path: Path) -> list[dict[str, str]]:
    """Read the CSV, dropping the comment header.

    The reference files carry their explanation in `#` lines above the header,
    which `csv` has no notion of — it would read the first comment as the
    column names.
    """
    with path.open(encoding="utf-8") as handle:
        body = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(body))


@cache
def load_outlets(path: Path | None = None) -> tuple[Outlet, ...]:
    """Every outlet in the reference list, retired ones included.

    Cached because the crawl asks for this once per outlet per shard and the
    file does not change under a run. Pass an explicit path to bypass it.
    """
    source = path or (project_root() / OUTLETS_CSV)
    return tuple(
        Outlet(
            outlet=row["outlet"],
            province=row["province"],
            geo_id=row["geo_id"],
            bps_code=row["bps_code"],
            host=row["host"],
            base_url=row["base_url"].rstrip("/"),
            section_path=row["section_path"],
            adapter=row["adapter"] or "wp_query",
            active=row["active"].strip().lower() == "true",
            note=row["note"],
        )
        for row in _rows(source)
    )


def active_outlets(path: Path | None = None) -> tuple[Outlet, ...]:
    """The outlets a crawl should actually visit."""
    return tuple(outlet for outlet in load_outlets(path) if outlet.active)


def outlet_by_host(host: str, path: Path | None = None) -> Outlet | None:
    """Find an outlet by its host, for attributing an article back to a paper."""
    host = host.lower().removeprefix("www.")
    for outlet in load_outlets(path):
        if outlet.host == host:
            return outlet
    return None
