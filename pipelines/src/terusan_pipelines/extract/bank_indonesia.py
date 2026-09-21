"""Bank Indonesia's survey workbooks, read by the parsers that know them.

The generic workbook reader turns a sheet into rows of cells, which is the right
shape for a table nobody has studied. These workbooks have been studied: the
vendored scrapers know that the per-city confidence table is numbered 8 rather
than the 6 the catalogue implies, and that the sheets must be found by their
title text because the numbering moves between releases.

So these extractors call those parsers on the landed bytes. The agency is
visited once, by the source; what the parsers get is the copy in RAW, which is
the point of keeping it (program.md §2.1).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..sources.bank_indonesia.legacy import consumer_survey, retail_sales
from .base import ExtractionError, Extractor, Landed


class ConsumerSurveyExtractor(Extractor):
    """Consumer confidence, national and per city."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == "bi-consumer-survey"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            workbook = consumer_survey.extract_workbook(landed.path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable survey: {exc}") from exc

        records: list[dict[str, Any]] = []

        percity = consumer_survey.find_sheet(
            workbook,
            must_include=["Indeks Keyakinan Konsumen", "18 Kota"],
            must_exclude=["Gabungan"],
        )
        if percity is not None:
            records.extend(consumer_survey.parse_percity_ikk_table(percity))

        national = consumer_survey.find_sheet(
            workbook, must_include=["Indeks Keyakinan Konsumen", "Gabungan", "18 Kota"]
        )
        if national is not None:
            records.extend(consumer_survey.parse_national_ikk_table(national))

        if not records:
            # The sheets are found by title, so an empty result means the
            # workbook was restructured — worth failing over, because silently
            # extracting nothing looks the same as a month with no release.
            raise ExtractionError(
                str(landed.path),
                "no confidence tables found; the workbook's sheet titles have changed",
            )

        yield from _as_bronze(records, landed.dataset or "consumer-survey")


class RetailSalesExtractor(Extractor):
    """Retail sales index, per city."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == "bi-retail-sales-survey"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            workbook = retail_sales.extract_workbook(landed.path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable survey: {exc}") from exc

        records = retail_sales.scrape_workbook(workbook)
        if not records:
            raise ExtractionError(
                str(landed.path),
                "no sales tables found; the workbook's sheet titles have changed",
            )
        yield from _as_bronze(records, landed.dataset or "retail-sales")


def _as_bronze(records: list[dict[str, Any]], dataset: str) -> Iterator[dict[str, Any]]:
    """Wrap parsed records as Bronze rows.

    Values stay as text, as everywhere in Bronze: deciding a type is Silver's
    job, once, with the whole column in view (program.md §6).
    """
    for number, record in enumerate(records, start=1):
        yield {
            "dataset": dataset,
            "row_number": number,
            "columns": {key: "" if value is None else str(value) for key, value in record.items()},
        }
