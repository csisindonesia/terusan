"""DJPK's APBD exports into Bronze records, one per figure.

Each landed file is one filter and one fiscal month: a SpreadsheetML sheet of
`Akun`, `Anggaran`, `Realisasi`, `Persentase`, where the account hierarchy is
carried by row order alone. Read as generic cells, the rows say nothing about
which government, which month, or which of the two account schemes the portal
has used — the first two are query parameters, on the landing record rather
than in the bytes, and the third changed in 2016 (Permendagri 13/2016).

So this reader carries all three. The account names are reduced to the
thirteen headline lines by the vendored module's own mapping, which knows both
schemes, and every line becomes two records — the budget and the realisation —
each naming the indicator it belongs to.

**Two families of indicator.** The national total and a province's total are
sums over governments, and share the `apbd_` indicators: the place is what
tells them apart. One government's own budget is `apbd_government_`, because
at a province's geography the two are both present and mean different things —
Jawa Timur's governments together, and Jawa Timur's provincial government.

**Place.** A province resolves by DJPK's code, which is its own numbering and
not BPS's past the thirty-third. A regency or city resolves by name within its
province, then by name alone where the name is unique — the geography
reference files every regency of the Papua provinces under Papua Barat, so a
match within the parent would miss all of them. A name that matches neither is
carried as DJPK printed it, and stays visibly unresolved in Silver.

**Period.** `YYYY-MM`, the month the cumulative figure runs to.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cache
from typing import Any

from ..sources.kemenkeu.legacy import djpk_apbd
from .base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "djpk-apbd"

#: DJPK's province code to the geography reference's id.
PROVINCE_GEO: dict[str, str] = {
    "01": "ID-11",
    "02": "ID-12",
    "03": "ID-13",
    "04": "ID-14",
    "05": "ID-15",
    "06": "ID-16",
    "07": "ID-17",
    "08": "ID-18",
    "09": "ID-31",
    "10": "ID-32",
    "11": "ID-33",
    "12": "ID-34",
    "13": "ID-35",
    "14": "ID-61",
    "15": "ID-62",
    "16": "ID-63",
    "17": "ID-64",
    "18": "ID-71",
    "19": "ID-72",
    "20": "ID-73",
    "21": "ID-74",
    "22": "ID-51",
    "23": "ID-52",
    "24": "ID-53",
    "25": "ID-81",
    "26": "ID-94",
    "27": "ID-82",
    "28": "ID-36",
    "29": "ID-19",
    "30": "ID-75",
    "31": "ID-21",
    "32": "ID-91",
    "33": "ID-76",
    "34": "ID-65",
    "35": "ID-95",
    "36": "ID-96",
    "37": "ID-97",
    "38": "ID-92",
}

#: The vendored module's labels to indicator stems. The first four stems are
#: the ones the national series have always had.
LINES: dict[str, tuple[str, str]] = {
    "Regional Revenue": ("revenue", "revenue"),
    "Regional Revenue - Own-Source Revenue (PAD)": ("own_revenue", "own-source revenue (PAD)"),
    "Regional Revenue - Transfers to Regions and Village Funds (TKDD)": (
        "transfer",
        "transfer revenue (TKDD)",
    ),
    "Regional Revenue - Other Revenue": ("other_revenue", "other revenue"),
    "Regional Expenditure": ("expenditure", "expenditure"),
    "Regional Expenditure - Personnel Expenditure": ("personnel_expenditure", "personnel"),
    "Regional Expenditure - Goods and Services Expenditure": (
        "goods_services_expenditure",
        "goods and services",
    ),
    "Regional Expenditure - Capital Expenditure": ("capital_expenditure", "capital expenditure"),
    "Regional Expenditure - Other Expenditure": ("other_expenditure", "other expenditure"),
    "Regional Financing": ("financing", "net financing"),
    "Regional Financing - Financing Receipts": ("financing_receipts", "financing receipts"),
    "Regional Financing - Financing Expenditures": (
        "financing_expenditures",
        "financing expenditures",
    ),
    "Fiscal balance": ("fiscal_balance", "fiscal balance (revenue less expenditure)"),
}

#: `anggaran` is the budget as it stood that month, revisions included;
#: `realisasi` is what was realised from January to that month.
MEASURES = (("realisasi", "realised, year to date"), ("anggaran", "budgeted"))


#: DJPK's abbreviations, spelled out as the geography reference spells them.
ABBREVIATIONS = (
    (re.compile(r"\bOKU\b"), "Ogan Komering Ulu"),
    (re.compile(r"\bOKI\b"), "Ogan Komering Ilir"),
    (re.compile(r"\bKep\.\s*"), "Kepulauan "),
)

#: What the portal answers with when a government has no report for the
#: period: the sheet's header row, then Laravel's error page, in one body.
SERVER_ERROR = b"<!DOCTYPE html"


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@cache
def _regencies() -> tuple[dict[tuple[str, str], str], dict[str, list[str]]]:
    """Regency ids by (province, name) and by name alone."""
    # Imported here: `normalize` imports the extraction runner, which imports
    # this module.
    from ..normalize.reference import load_regencies

    within: dict[tuple[str, str], str] = {}
    anywhere: dict[str, list[str]] = {}
    for area in load_regencies():
        for name in (area.name, *area.aliases):
            key = _key(name)
            within.setdefault((area.parent_geo_id or "", key), area.geo_id)
            ids = anywhere.setdefault(key, [])
            if area.geo_id not in ids:
                ids.append(area.geo_id)
    return within, anywhere


def regency_geo(province_geo: str | None, pemda_name: str) -> str | None:
    """The reference id of a regency or city DJPK names, or None."""
    name = pemda_name.strip()
    if name.startswith("Kab."):
        name = name.removeprefix("Kab.").strip()
    elif name.startswith("Kabupaten "):
        name = name.removeprefix("Kabupaten ").strip()
    elif name.startswith("Kota "):
        # The reference names cities `Kota X`, which is what tells Kota Banjar
        # from Kabupaten Banjar.
        pass
    for pattern, spelled in ABBREVIATIONS:
        name = pattern.sub(spelled, name)
    key = _key(name)
    within, anywhere = _regencies()
    if province_geo and (province_geo, key) in within:
        return within[(province_geo, key)]
    matches = anywhere.get(key, [])
    return matches[0] if len(matches) == 1 else None


def _place(extra: dict[str, Any]) -> tuple[str, str]:
    """The geography a file is about: `(geo, printed name)`."""
    scope = str(extra.get("scope") or "national")
    provinsi = str(extra.get("provinsi") or "--")
    pemda = str(extra.get("pemda") or "--")
    province_geo = PROVINCE_GEO.get(provinsi)
    province_name = str(extra.get("provinsi_name") or djpk_apbd.PROVINCES.get(provinsi, ""))

    if scope == "national" or provinsi == "--":
        return "Indonesia", "Indonesia"
    if pemda in ("--", "00"):
        return province_geo or province_name, province_name
    pemda_name = str(extra.get("pemda_name") or "")
    return regency_geo(province_geo, pemda_name) or pemda_name, pemda_name


class DjpkApbdExtractor(Extractor):
    """Two Bronze records — budget and realisation — per headline APBD line."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".xml"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        extra = dict(landed.extra or {})
        try:
            year = int(extra["tahun"])
            month = int(extra["periode"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractionError(
                str(landed.path), "landing record names no fiscal year and month"
            ) from exc

        content = landed.path.read_bytes()
        if SERVER_ERROR in content:
            # No report for this government and period. Landed before the
            # source learned to refuse these, and not a failure to read.
            return
        try:
            records = djpk_apbd.parse_rows(content)
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable SpreadsheetML: {exc}") from exc

        legacy = djpk_apbd.is_legacy_scheme(records)
        lines = djpk_apbd.extract_indicators(records)
        if not lines:
            # A government with no report for the year yet answers with an
            # empty sheet, which is a gap, not a failure.
            return

        scope = str(extra.get("scope") or "national")
        family = "apbd_government_" if scope == "governments" else "apbd_"
        who = (
            "one regional government" if scope == "governments" else "regional governments, summed"
        )
        geo, geo_name = _place(extra)

        number = 0
        for label, figures in lines.items():
            if label not in LINES:
                continue
            stem, title = LINES[label]
            for measure, measure_title in MEASURES:
                value = figures.get(measure)
                if value is None:
                    continue
                number += 1
                yield {
                    "dataset": landed.dataset or "apbd-national",
                    "row_number": number,
                    "columns": {
                        "indicator": f"{family}{stem}_{measure}",
                        "series_name": f"APBD {title}, {measure_title} — {who}",
                        "period": f"{year}-{month:02d}",
                        "geo": geo,
                        "geo_name": geo_name,
                        "value": f"{float(value):.2f}",
                        "unit": "IDR",
                        "measure": measure,
                        "line": label,
                        "scope": scope,
                        "provinsi": str(extra.get("provinsi") or "--"),
                        "pemda": str(extra.get("pemda") or "--"),
                        "account_scheme": "pre-2016" if legacy else "Permendagri 13/2016",
                        "note": str(figures.get("note") or ""),
                        "publisher": "DJPK Kementerian Keuangan",
                    },
                }
