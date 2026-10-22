"""Google's government removals, as observations.

Requesters are Google's categories — "Government Officials", "Police",
"Information and Communications Authority" — mapped to a requester type and
kept as written. None is an agency, so `requester_agency` stays empty: Google
never says which Indonesian body sent a request, and the observatory does not
say it for them.

Each 2019-onward row states its items' outcomes, which between them should
account for every item requested. A row where they do not is kept and flagged:
the figures are Google's, and the disagreement is worth seeing, not hiding.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from pathlib import PurePosixPath

from .. import taxonomy as tx
from ..model import Observation, Quarantined
from .base import Adapter, BronzeRow, sum_values

_MEASURES: dict[tuple[str, str], tx.Measure] = {
    **{("2019", m.label): m for m in tx.GOOGLE_DETAIL_MEASURES},
    **{("2009", m.label): m for m in tx.GOOGLE_TOTAL_MEASURES},
    **{("2009", m.label): m for m in tx.GOOGLE_HISTORICAL_DETAIL_MEASURES},
    **{("2009", m.label): m for m in tx.GOOGLE_HISTORICAL_TOTAL_MEASURES},
}


class GoogleAdapter(Adapter):
    provider = tx.Provider.GOOGLE
    sources = ("google-government-removals",)
    extractor = "GoogleRemovalsExtractor"
    license_notes = "Google Transparency Report; published for public use with attribution."
    methodology_notes = (
        "Through 2018: country totals split into court orders and executive/police "
        "requests, with a compliance percentage. From January–June 2019: a row per "
        "requester category, product and reason, with every item's outcome. "
        "Requests are counted per row, and a request naming several products or "
        "reasons appears on several rows, so rows do not sum to a country's requests."
    )

    def normalize(self, row: BronzeRow) -> Observation | Quarantined:
        measure = _MEASURES.get((row.get("format"), row.get("measure")))
        if measure is None:
            return self.quarantine(row, f"unmapped Google measure {row.get('measure')!r}")

        requester_raw = row.get("requester_raw")
        if measure.requester is not None:
            requester = measure.requester
        elif requester_raw in tx.GOOGLE_REQUESTERS:
            requester = tx.GOOGLE_REQUESTERS[requester_raw]
        else:
            return self.quarantine(row, f"unmapped Google requester {requester_raw!r}")

        product = row.get("product_raw")
        return self.build(
            row,
            measure,
            platform=tx.GOOGLE_PLATFORMS.get(product, tx.GOOGLE_DEFAULT_PLATFORM)
            if product
            else "google_all",
            product=product or None,
            requester_type=requester,
            requester_raw=requester_raw or None,
            reason_raw=row.get("reason_raw") or None,
            geographic_basis=tx.GeographicBasis.REQUEST_ORIGIN,
            table_key=PurePosixPath(row.get("source_table")).name,
        )

    def validate(
        self, observations: Sequence[Observation]
    ) -> tuple[list[Observation], list[Quarantined]]:
        by_row: dict[tuple[str | None, str], list[Observation]] = defaultdict(list)
        for observation in observations:
            if observation.source_label in tx.GOOGLE_ITEM_OUTCOMES or (
                observation.source_label == "items_requested_removal"
            ):
                by_row[(observation.document_id, observation.source_row_locator)].append(
                    observation
                )

        for row in by_row.values():
            requested = [o for o in row if o.source_label == "items_requested_removal"]
            outcomes = [o for o in row if o.source_label in tx.GOOGLE_ITEM_OUTCOMES]
            if len(requested) != 1 or not outcomes:
                continue
            total = sum_values(outcomes)
            stated = requested[0].metric_value
            if total is None or stated is None or total == stated:
                continue
            flag = f"item_outcomes_sum_{_fmt(total)}_not_requested_{_fmt(stated)}"
            for observation in row:
                observation.quality_flags.append(flag)
        return list(observations), []


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")
