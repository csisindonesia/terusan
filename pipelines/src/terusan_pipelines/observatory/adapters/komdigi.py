"""Komdigi's content handling and website blocking, as observations.

The one provider whose figures name their agency, because the publisher is the
agency: `requester_agency` is Komdigi here and nowhere else.

A table's stated total is not an observation. It is checked against the rows it
totals: where it agrees, the rows are marked as confirmed and the total adds
nothing; where it does not, the total is quarantined with both numbers, so the
disagreement is on record without a second, conflicting figure entering the
series. The 2018–2021 website table is the case in point — its categories add
up to 1,558,736 and its total row says 2,679,352.

What "handled" means is not published. The social media figures are kept as
`content_handled`, a metric of their own, and never set beside a platform's
"requests" or "actioned" counts as if they measured the same thing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .. import taxonomy as tx
from ..model import Observation, Quarantined
from .base import Adapter, BronzeRow, sum_values

AGENCY = "Kementerian Komunikasi dan Digital (Komdigi)"


class KomdigiAdapter(Adapter):
    provider = tx.Provider.KOMDIGI
    sources = ("komdigi-content-handling",)
    extractor = "KomdigiContentExtractor"
    license_notes = "Satu Data Indonesia open data, data.komdigi.go.id."
    methodology_notes = (
        "Ditjen Pengawasan Ruang Digital's own aggregates: negative content handled "
        "per social media platform by year, and websites blocked by content category. "
        "No definition of a handled item is published, and the platforms' figures "
        "count requests and items on their own definitions; the two are shown side "
        "by side and never subtracted."
    )

    def __init__(self) -> None:
        self._totals: list[BronzeRow] = []

    def normalize(self, row: BronzeRow) -> Observation | Quarantined | None:
        websites = row.dataset.endswith("websites")
        if row.get("row_role") == "total":
            # Held for `validate`, which needs the rows it totals.
            self._totals.append(row)
            return None

        measure = tx.KOMDIGI_WEBSITE_MEASURE if websites else tx.KOMDIGI_SOCIAL_MEASURE
        return self.build(
            row,
            measure,
            platform="websites" if websites else (row.get("platform") or "unknown"),
            requester_type=tx.RequesterType.GOVERNMENT,
            requester_agency=AGENCY,
            reason_raw=row.get("reason_raw") or None,
            geographic_basis=(
                tx.GeographicBasis.RESTRICTION_LOCATION if websites else tx.GeographicBasis.UNKNOWN
            ),
            table_key="websites" if websites else "social",
            notes=(None if websites else f"Komdigi's label: {row.get('platform_raw')}."),
        )

    def validate(
        self, observations: Sequence[Observation]
    ) -> tuple[list[Observation], list[Quarantined]]:
        components: dict[str, list[Observation]] = defaultdict(list)
        for observation in observations:
            if observation.document_id:
                components[observation.document_id].append(observation)

        quarantined: list[Quarantined] = []
        for total in self._totals:
            rows = components.get(total.document_id, [])
            summed = sum_values(rows) if rows else None
            stated = total.get("value")
            if summed is not None and stated and format(summed, "f") == stated:
                for observation in rows:
                    observation.quality_flags.append("publisher_total_confirmed")
                continue
            for observation in rows:
                observation.quality_flags.append("publisher_total_disagrees")
            quarantined.append(
                self.quarantine(
                    total,
                    f"stated total {stated} does not equal the sum of its rows, "
                    f"{format(summed, 'f') if summed is not None else 'not computable'}",
                )
            )
        self._totals = []
        return list(observations), quarantined
