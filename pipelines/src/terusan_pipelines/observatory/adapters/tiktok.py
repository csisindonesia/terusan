"""TikTok's government removal requests, as observations.

TikTok publishes both sides of its totals — content received, and how much of
it was actioned under local law, actioned under community guidelines, or not
actioned — so every country and half-year is checked for whether the parts add
up. Where they do not, every figure involved is flagged; none is changed.

"Actioned" stays actioned. TikTok does not say whether an item was removed
worldwide or restricted in the country, and the observatory does not decide it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .. import taxonomy as tx
from ..model import Observation, Quarantined
from .base import Adapter, BronzeRow, sum_values

_MEASURES = {m.label: m for m in tx.TIKTOK_MEASURES}


class TikTokAdapter(Adapter):
    provider = tx.Provider.TIKTOK
    sources = ("tiktok-government-removals",)
    extractor = "TikTokRemovalsExtractor"
    license_notes = "TikTok Transparency Center; published for public use with attribution."
    methodology_notes = (
        "Government removal requests per country and half-year from January–June "
        "2022; 2019–2021 only as a worldwide total. Content and accounts are counted "
        "separately, and actioned items are split between local law and community "
        "guidelines without saying whether an item was removed or geo-restricted."
    )

    def normalize(self, row: BronzeRow) -> Observation | Quarantined:
        measure = _MEASURES.get(row.get("measure"))
        if measure is None:
            return self.quarantine(row, f"unmapped TikTok measure {row.get('measure')!r}")
        return self.build(
            row,
            measure,
            platform="tiktok",
            requester_type=measure.requester or tx.RequesterType.GOVERNMENT,
            geographic_basis=tx.GeographicBasis.REQUEST_ORIGIN,
            # TikTok's code for the report, which every release's file carries
            # in its path; the filename itself changes with the release.
            table_key="grfcr",
            breakdown=f"policy={row.get('policy')};task={row.get('task')}",
        )

    def validate(
        self, observations: Sequence[Observation]
    ) -> tuple[list[Observation], list[Quarantined]]:
        groups: dict[tuple, dict[str, Observation]] = defaultdict(dict)
        for observation in observations:
            key = (
                observation.document_id,
                observation.country_code or observation.country_raw,
                observation.period_start,
                observation.period_end,
            )
            groups[key][observation.source_label] = observation

        for figures in groups.values():
            for total_label, parts in tx.TIKTOK_TOTALS.items():
                total = figures.get(total_label)
                components = [figures[p] for p in parts if p in figures]
                if total is None or len(components) != len(parts):
                    continue
                summed = sum_values(components)
                if summed is None or total.metric_value is None or summed == total.metric_value:
                    continue
                flag = f"components_do_not_sum_to_{total_label.lower().replace(' ', '_')}"
                for observation in (total, *components):
                    observation.quality_flags.append(flag)
        return list(observations), []
