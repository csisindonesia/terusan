"""DJPK APBD: which months are asked for, and what a landed file becomes.

The fetch tests run against a fake client that answers the way the portal
does: a closed year serves its year-end file for every month.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from terusan_pipelines.extract import DjpkApbdExtractor, Landed
from terusan_pipelines.extract.runner import DEFAULT_EXTRACTORS
from terusan_pipelines.sources.kemenkeu.apbd import (
    GOVERNMENTS,
    Filter,
    RegionalBudgets,
    months_to_fetch,
)

TODAY = date(2026, 9, 25)


# -- which months ------------------------------------------------------------


def test_the_current_year_is_walked_forwards_to_this_month() -> None:
    assert months_to_fetch(2026, TODAY, None) == list(range(1, 10))


def test_a_closed_year_is_walked_from_december() -> None:
    """So the year-end file is the first to land with its bytes, and is filed
    under December rather than January."""
    assert months_to_fetch(2025, TODAY, None) == list(range(12, 0, -1))


def test_since_still_reaches_back_for_restatements() -> None:
    assert months_to_fetch(2026, TODAY, date(2026, 9, 18)) == [6, 7, 8, 9]


def test_the_reach_back_crosses_the_year() -> None:
    today = date(2026, 1, 10)
    since = date(2026, 1, 3)

    assert months_to_fetch(2026, today, since) == [1]
    assert months_to_fetch(2025, today, since) == [12, 11, 10]
    assert months_to_fetch(2024, today, since) == []


# -- a closed year stops after two requests ----------------------------------


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content


class _Portal:
    """Answers with the year-end bytes for any month of a closed year."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def try_get(self, url: str, params: dict) -> _Response:
        self.asked.append(params["periode"])
        if params["tahun"] < 2026:
            return _Response(b"year-end")
        return _Response(f"month {params['periode']}".encode())


def test_a_year_the_portal_has_closed_costs_two_requests() -> None:
    portal = _Portal()
    region = Filter(GOVERNMENTS, provinsi="13", pemda="01", pemda_name="Kab. Bangkalan")

    landed = list(RegionalBudgets()._year(portal, region, 2015, list(range(12, 0, -1))))

    assert portal.asked == [12, 11]
    assert [a.metadata["periode"] for a in landed] == [12]
    assert landed[0].dataset == "apbd-governments"
    assert landed[0].partition == ("year=2015", "province=13")


def test_an_open_year_lands_every_month() -> None:
    portal = _Portal()

    landed = list(RegionalBudgets()._year(portal, Filter("national"), 2026, [7, 8, 9]))

    assert [a.filename for a in landed] == [
        "apbd-2026-07.xml",
        "apbd-2026-08.xml",
        "apbd-2026-09.xml",
    ]


# -- extraction --------------------------------------------------------------


def _sheet(rows: list[tuple[str, float, float]]) -> bytes:
    cells = "".join(
        f"<Row><Cell><Data>{akun}</Data></Cell><Cell><Data>{a}</Data></Cell>"
        f"<Cell><Data>{r}</Data></Cell><Cell><Data>0</Data></Cell></Row>"
        for akun, a, r in rows
    )
    return (
        '<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">'
        "<Worksheet><Table><Row><Cell><Data>Akun</Data></Cell><Cell><Data>Anggaran</Data>"
        "</Cell><Cell><Data>Realisasi</Data></Cell><Cell><Data>Persentase</Data></Cell></Row>"
        f"{cells}</Table></Worksheet></Workbook>"
    ).encode()


CURRENT = _sheet(
    [
        ("Pendapatan Daerah", 2.0e12, 1.5e12),
        ("PAD", 5.0e11, 4.0e11),
        ("Belanja Daerah", 2.2e12, 1.2e12),
    ]
)


def _landed(tmp_path: Path, **extra: object) -> Landed:
    path = tmp_path / "apbd.xml"
    path.write_bytes(CURRENT)
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="djpk-apbd",
        dataset={"governments": "apbd-governments", "provinces": "apbd-provinces"}.get(
            str(extra.get("scope")), "apbd-national"
        ),
        extra={"tahun": 2026, "periode": 8, **extra},
    )


def _by_indicator(rows: list[dict]) -> dict[str, dict[str, str]]:
    return {row["columns"]["indicator"]: row["columns"] for row in rows}


def test_the_national_file_keeps_its_indicator_names(tmp_path: Path) -> None:
    rows = _by_indicator(DjpkApbdExtractor().extract(_landed(tmp_path, scope="national")))

    revenue = rows["apbd_revenue_realisasi"]
    assert revenue["value"] == "1500000000000.00"
    assert revenue["period"] == "2026-08"
    assert revenue["geo"] == "Indonesia"
    assert rows["apbd_revenue_anggaran"]["value"] == "2000000000000.00"
    # Derived: revenue less expenditure, for both columns.
    assert rows["apbd_fiscal_balance_realisasi"]["value"] == "300000000000.00"


def test_a_province_total_is_filed_under_the_province(tmp_path: Path) -> None:
    landed = _landed(tmp_path, scope="provinces", provinsi="13", pemda="--")

    rows = _by_indicator(DjpkApbdExtractor().extract(landed))

    assert rows["apbd_revenue_realisasi"]["geo"] == "ID-35"


def test_one_government_is_its_own_family(tmp_path: Path) -> None:
    """At a province's geography both the province's governments summed and
    the provincial government alone exist, and they are different numbers."""
    province = _landed(tmp_path, scope="governments", provinsi="13", pemda="00")
    regency = _landed(
        tmp_path, scope="governments", provinsi="13", pemda="01", pemda_name="Kab. Bangkalan"
    )

    own = _by_indicator(DjpkApbdExtractor().extract(province))
    bangkalan = _by_indicator(DjpkApbdExtractor().extract(regency))

    assert own["apbd_government_revenue_realisasi"]["geo"] == "ID-35"
    assert bangkalan["apbd_government_revenue_realisasi"]["geo"] == "ID-35.26"
    assert "apbd_revenue_realisasi" not in own


def test_djpk_numbers_the_provinces_its_own_way(tmp_path: Path) -> None:
    """DJPK's 28 is Banten, which BPS numbers 36."""
    landed = _landed(tmp_path, scope="provinces", provinsi="28")

    rows = _by_indicator(DjpkApbdExtractor().extract(landed))

    assert rows["apbd_revenue_realisasi"]["geo"] == "ID-36"


def test_a_file_without_a_month_is_refused(tmp_path: Path) -> None:
    landed = _landed(tmp_path)
    landed.extra.pop("periode")

    with pytest.raises(Exception, match="fiscal year and month"):
        list(DjpkApbdExtractor().extract(landed))


def test_apbd_is_read_before_the_generic_spreadsheetml_reader(tmp_path: Path) -> None:
    landed = _landed(tmp_path)

    first = next(e for e in DEFAULT_EXTRACTORS if e.handles(landed))

    assert isinstance(first, DjpkApbdExtractor)


def test_the_portals_error_page_is_no_report_rather_than_a_failure(tmp_path: Path) -> None:
    """A government with no report is answered with the sheet's header row and
    then an HTML error page, in one body."""
    landed = _landed(tmp_path, scope="governments", provinsi="06", pemda="17")
    landed.path.write_bytes(
        b'<?xml version="1.0"?><Workbook><Row><Data>Akun</Data></Row>'
        b"<!DOCTYPE html><html><title>Server Error</title></html>"
    )

    assert list(DjpkApbdExtractor().extract(landed)) == []


@pytest.mark.parametrize(
    ("province", "name", "geo"),
    [
        ("ID-16", "Kab. OKU Selatan", "ID-16.09"),
        ("ID-16", "Kab. OKI", "ID-16.02"),
        ("ID-71", "Kab. Kep. Siau Tagulandang Biaro", "ID-71.09"),
    ],
)
def test_djpks_abbreviations_are_spelled_out(province: str, name: str, geo: str) -> None:
    from terusan_pipelines.extract.djpk import regency_geo

    assert regency_geo(province, name) == geo


def test_years_run_newest_first_and_narrow_on_request() -> None:
    from terusan_pipelines.sources import ScrapeContext

    assert RegionalBudgets._years(ScrapeContext(), 2011, 2026)[:2] == [2026, 2025]
    assert RegionalBudgets._years(ScrapeContext(params={"years": "2012-2014"}), 2011, 2026) == [
        2014,
        2013,
        2012,
    ]
    assert RegionalBudgets._years(ScrapeContext(params={"years": "2024"}), 2011, 2026) == [2024]
