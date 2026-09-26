"""WITS TradeStats and the RCA seed: the calls, the SDMX, and the sums.

The seed tests build frames shaped like the two working files, cut down to the
cases that decide a figure: a code the concordance repeated, a listed product
the country does not export, and a world total that has to cover it anyway.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from terusan_pipelines.extract import Landed, RcaSeedExtractor, WitsTradeStatsExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.wits import atlas_rows, indonesia_rows, product_slug
from terusan_pipelines.sources.wits.tradestats import INDICATORS, TradeStats, query_url
from terusan_pipelines.trade import REPORTERS, environmental_goods, hs6

SDMX = """<?xml version="1.0" encoding="utf-8"?>
<message:StructureSpecificData
    xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
  <message:DataSet>
    <Series FREQ="A" REPORTER="IDN" PARTNER="WLD" PRODUCTCODE="84-85_MachElec" INDICATOR="RCA">
      <Obs TIME_PERIOD="2022" OBS_VALUE="0.36" DATASOURCE="WITS-CMT" />
      <Obs TIME_PERIOD="2023" OBS_VALUE="0.37" DATASOURCE="WITS-CMT" />
    </Series>
    <Series FREQ="A" REPORTER="IDN" PARTNER="WLD" PRODUCTCODE="Total" INDICATOR="RCA">
      <Obs TIME_PERIOD="2023" OBS_VALUE="1" DATASOURCE="WITS-CMT" />
    </Series>
  </message:DataSet>
</message:StructureSpecificData>
"""


def _landed(path: Path, source: str, dataset: str = "x") -> Landed:
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug=source,
        dataset=dataset,
    )


# -- the calls ----------------------------------------------------------------


def test_one_call_per_reporter_and_indicator() -> None:
    endpoints = list(TradeStats().endpoints_for(None, None))  # type: ignore[arg-type]

    assert len(endpoints) == len(REPORTERS) * len(INDICATORS)
    assert endpoints[0].url == query_url("IDN", "RCA")
    assert "/reporter/idn/year/all/partner/wld/product/all/indicator/RCA" in endpoints[0].url
    assert endpoints[0].partition == ("reporter=IDN",)


def test_indonesia_is_among_the_reporters_and_listed_once() -> None:
    assert REPORTERS[0] == "IDN"
    assert len(set(REPORTERS)) == len(REPORTERS)


# -- the SDMX -----------------------------------------------------------------


def test_each_observation_is_a_row_named_by_measure_and_group(tmp_path: Path) -> None:
    path = tmp_path / "wits-idn-rca.xml"
    path.write_text(SDMX)

    rows = [
        r["columns"] for r in WitsTradeStatsExtractor().extract(_landed(path, "wits-tradestats"))
    ]

    assert [(r["indicator"], r["year"], r["value"]) for r in rows] == [
        ("wits_rca_84_85_machelec", "2022", "0.36"),
        ("wits_rca_84_85_machelec", "2023", "0.37"),
    ]
    assert rows[0]["reporter"] == "IDN"
    assert (
        rows[0]["series_name"]
        == "Revealed comparative advantage: HS 84–85 Machinery and electrical"
    )


def test_rca_in_all_products_is_left_out(tmp_path: Path) -> None:
    """It is 1 for every country by definition."""
    path = tmp_path / "wits-idn-rca.xml"
    path.write_text(SDMX)

    rows = list(WitsTradeStatsExtractor().extract(_landed(path, "wits-tradestats")))

    assert all(r["columns"]["measure"] != "RCA/Total" for r in rows)


def test_an_error_answered_with_200_fails_the_document(tmp_path: Path) -> None:
    path = tmp_path / "wits-too-large.xml"
    path.write_text(
        '<string xmlns="http://schemas.microsoft.com/2003/10/Serialization/">'
        "&lt;wits:error&gt;Response too large&lt;/wits:error&gt;</string>"
    )

    with pytest.raises(ExtractionError):
        list(WitsTradeStatsExtractor().extract(_landed(path, "wits-tradestats")))


def test_two_families_of_fuels_stay_apart() -> None:
    assert product_slug("27-27_Fuels") != product_slug("Fuels")


# -- the goods lists ----------------------------------------------------------


def test_a_code_keeps_its_leading_zero() -> None:
    assert hs6(20731) == "020731"
    assert hs6("20731.0") == "020731"


def test_the_union_holds_every_list() -> None:
    lists = environmental_goods()

    assert len(lists["any"]) == 561
    for key, codes in lists.items():
        assert codes <= lists["any"], key


# -- the seed: Indonesia from WITS --------------------------------------------


def _indonesia(year: int = 2023) -> pd.DataFrame:
    lists = environmental_goods()
    tessd = sorted(lists["tessd"])
    return pd.DataFrame(
        {
            "year": [year, year, year, year],
            # The second row is the first repeated by the concordance.
            "hs92": [int(tessd[0]), int(tessd[0]), int(tessd[1]), 10111],
            "idnexport": [100.0, 100.0, 50.0, 850.0],
            "idntotalexport": [1000.0] * 4,
            "wldexport": [1.0, 1.0, 1.0, 1.0],
            "wldtotalexport": [10.0] * 4,
            "rca": [2.0, 2.0, 0.5, 1.5],
        }
    )


def test_a_code_the_concordance_repeated_is_counted_once() -> None:
    rows = {r["indicator"]: r for r in indonesia_rows(_indonesia(), "rca-indonesia-hs6")}

    assert rows["wits_hs6_eg_exports_tessd"]["value"] == "150.0"
    assert rows["wits_hs6_eg_export_share_tessd"]["value"] == "15.0"
    assert rows["wits_hs6_eg_products_rca_tessd"]["value"] == "1"
    assert rows["wits_hs6_products_rca"]["value"] == "2"


def test_indonesia_file_publishes_no_basket_rca() -> None:
    """The world's side of the basket is missing what Indonesia did not sell."""
    indicators = {r["indicator"] for r in indonesia_rows(_indonesia(), "rca-indonesia-hs6")}

    assert not any("_rca_" in i and "products" not in i for i in indicators)


# -- the seed: the Atlas panel ------------------------------------------------


def _atlas() -> pd.DataFrame:
    tessd = sorted(environmental_goods()["tessd"])
    listed, other = tessd[0], "010111"
    # IDN exports the listed product; VNM does not, but imports it, so the
    # world's exports of it are on VNM's row and nowhere of IDN's.
    return pd.DataFrame(
        {
            "year": [2023, 2023, 2023],
            "country_iso3_code": ["IDN", "IDN", "VNM"],
            "product_hs92_code": [listed, other, listed],
            "export_value": [10.0, 90.0, 0.0],
            "country_total_export_value": [100.0, 100.0, 200.0],
            "wld_export_value": [50.0, 450.0, 50.0],
            "wld_total_export_value": [1000.0] * 3,
            "eci": [0.28, 0.28, 0.58],
            "coi": [None, None, None],
            "diversity": [114, 114, 150],
            "growth_proj": [4.1, 4.1, 5.0],
        }
    )


def test_basket_rca_is_the_list_against_the_world() -> None:
    rows = {
        (r["reporter"], r["indicator"]): r["value"] for r in atlas_rows(_atlas(), "rca-atlas-hs6")
    }

    # (10 / 100) / (50 / 1000) = 2
    assert float(rows[("IDN", "atlas_eg_rca_tessd")]) == pytest.approx(2.0)
    assert float(rows[("VNM", "atlas_eg_rca_tessd")]) == 0.0
    assert rows[("IDN", "atlas_eg_products_rca_tessd")] == "1"


def test_complexity_is_published_as_the_atlas_states_it() -> None:
    rows = {
        (r["reporter"], r["indicator"]): r["value"] for r in atlas_rows(_atlas(), "rca-atlas-hs6")
    }

    assert rows[("IDN", "atlas_eci")] == "0.28"
    assert ("IDN", "atlas_coi") not in rows


def test_the_extractor_tells_the_files_apart(tmp_path: Path) -> None:
    path = tmp_path / "atlas.parquet"
    _atlas().to_parquet(path)

    rows = list(RcaSeedExtractor().extract(_landed(path, "rca-seed", "rca-atlas-hs6")))

    assert rows and all(r["dataset"] == "rca-atlas-hs6" for r in rows)
    assert {r["columns"]["reporter"] for r in rows} == {"IDN", "VNM"}
