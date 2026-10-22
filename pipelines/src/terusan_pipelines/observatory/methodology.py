"""Changes in how a provider reports, declared with the evidence for each.

Only changes that can be shown from a landed file or the provider's own
statement are listed — each `evidence` says which. A trend that crosses one of
these dates is not comparable across it without the reader knowing, and the
coverage view marks it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import taxonomy as tx


@dataclass(frozen=True, slots=True)
class MethodologyChange:
    provider: tx.Provider
    effective_date: date
    description: str
    affected_metrics: tuple[tx.Metric, ...]
    evidence: str
    #: Which landed source the evidence is in, so the build can link the
    #: provider's newest report holding it.
    evidence_source: str | None = None


CHANGES: tuple[MethodologyChange, ...] = (
    MethodologyChange(
        provider=tx.Provider.GOOGLE,
        effective_date=date(2019, 1, 1),
        description=(
            "Google replaced its country totals by branch of government (court orders; "
            "executive, police, etc.) and its compliance percentage with a breakdown by "
            "requester category, product and reason, giving each item's outcome. "
            "Country totals of requests and the compliance percentage are not in the "
            "data download after 2018."
        ),
        affected_metrics=(
            tx.Metric.REQUESTS_RECEIVED,
            tx.Metric.ITEMS_SPECIFIED,
            tx.Metric.COMPLIANCE_RATE,
            tx.Metric.ITEMS_REMOVED,
            tx.Metric.ITEMS_NOT_ACTIONED,
        ),
        evidence=(
            "README.txt in google-government-removals.zip: the historical folder holds "
            "'the reporting format we used through 2018'; the main CSV is 'the reporting "
            "format we started using in 2019'."
        ),
        evidence_source="google-government-removals",
    ),
    MethodologyChange(
        provider=tx.Provider.TIKTOK,
        effective_date=date(2022, 1, 1),
        description=(
            "TikTok's data download breaks government requests down by country, and "
            "into content and accounts with their outcomes, from January–June 2022. "
            "For 2019–2021 it carries only a worldwide total of government requests."
        ),
        affected_metrics=(
            tx.Metric.REQUESTS_RECEIVED,
            tx.Metric.ITEMS_SPECIFIED,
            tx.Metric.ACCOUNTS_SPECIFIED,
            tx.Metric.ITEMS_ACTIONED,
            tx.Metric.ACCOUNTS_ACTIONED,
        ),
        evidence=(
            "2025H2_GRFCR_English.csv: country rows begin with period 'Jan-Jun 2022'; "
            "rows for 2019–2021 have location 'all' and the single metric 'Total "
            "Government Requests'."
        ),
        evidence_source="tiktok-government-removals",
    ),
    MethodologyChange(
        provider=tx.Provider.KOMDIGI,
        effective_date=date(2022, 1, 1),
        description=(
            "Komdigi's published content figures change table: January 2018 to October "
            "2021 is a count of websites blocked by content category; 2022 and 2024 are "
            "counts of content handled per social media platform. The two do not "
            "continue one another, and 2023 is not published on the portal."
        ),
        affected_metrics=(tx.Metric.URLS_BLOCKED, tx.Metric.CONTENT_HANDLED),
        evidence=(
            "data.komdigi.go.id, Ditjen Pengawasan Ruang Digital datasets as listed on "
            "2026-10-22: 'Penanganan Konten Negatif pada Situs Internet Periode Januari "
            "2018 – Oktober 2021', 'Jumlah Penanganan Konten Internet Negatif pada Media "
            "Sosial Tahun 2022', 'Penanganan Konten Internet Negatif Pada Sosial Media "
            "2024'."
        ),
        evidence_source="komdigi-content-handling",
    ),
)
