"""ADB KIDB: reading the SDMX-CSV and the indicator codelist.

Offline, against rows cut down from the real `DF_KIDB/A..INO` response — the
quoted footnote with its embedded commas, the empty trailing columns, and the
exact repeat KIDB serves for the World Bank's poverty headcount.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terusan_pipelines.extract import (
    DEFAULT_EXTRACTORS,
    ExtractionError,
    KidbCodelistExtractor,
    KidbDataExtractor,
    Landed,
)
from terusan_pipelines.extract.adb import indicator_id, unit_label
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.adb import kidb
from terusan_pipelines.sources.portals import AccessNotProvisioned

HEADER = (
    "DATAFLOW,FREQ,INDICATOR,ECONOMY_CODE,TIME_PERIOD,OBS_VALUE,UNIT,UNIT_MULT,"
    "DECIMALS,FOOTNOTE,OBS_STATUS,REF_YEAR,BASE_YEAR,DATA_SOURCE,METHODOLOGY"
)

DATA_CSV = "\n".join(
    [
        HEADER,
        'ADB:DF_KIDB(1.0),A,NGDP_XDC,INO,2000,1389.76985,IDR,12,0,"1) For 2010 onward, '
        'data are based on the 2008 SNA, base 2010.",A,Calendar Year,,BPS Statistics Indonesia,',
        "ADB:DF_KIDB(1.0),A,NGDP_XDC,INO,2010,6864.1331,IDR,12,0,,B,Calendar Year,,"
        "BPS Statistics Indonesia,System of National Accounts - 2008",
        "ADB:DF_KIDB(1.0),A,SI_POV_DDAY,INO,2000,65.67724306,PCT,0,1,,A,,,World Bank,",
        "ADB:DF_KIDB(1.0),A,SI_POV_DDAY,INO,2000,65.67724306,PCT,0,1,,A,,,World Bank,",
    ]
).encode()

CODELIST = {
    "data": {
        "codelists": [
            {
                "id": "CL_KIDB_INDICATORS",
                "codes": [
                    {"id": "NGDP_XDC", "name": "GDP at current prices", "description": "Gross."},
                    {"id": "SI_POV_DDAY", "names": {"en": "Poverty headcount at $2.15"}},
                    {"name": "a code with no id"},
                ],
            }
        ]
    }
}


def landed(tmp_path: Path, name: str, content: bytes, dataset: str) -> Landed:
    path = tmp_path / name
    path.write_bytes(content)
    return Landed(
        path=path,
        document_id=f"doc_{name}",
        content_hash="abc",
        source_slug="adb-kidb",
        dataset=dataset,
    )


def test_data_rows_carry_the_scaled_unit_and_the_place(tmp_path: Path) -> None:
    rows = list(
        KidbDataExtractor().extract(landed(tmp_path, "kidb-ino.csv", DATA_CSV, kidb.DATASET))
    )
    gdp = rows[0]["columns"]
    assert rows[0]["dataset"] == kidb.DATASET
    assert gdp["indicator"] == indicator_id("NGDP_XDC")
    assert gdp["code"] == "NGDP_XDC"
    assert gdp["country"] == "Indonesia"
    assert gdp["period"] == "2000"
    assert gdp["value"] == "1389.76985"
    assert gdp["unit"] == "IDR trillion"
    assert gdp["publisher"] == "BPS Statistics Indonesia"
    assert gdp["footnote"].startswith("1) For 2010 onward, data")
    assert rows[1]["columns"]["methodology"] == "System of National Accounts - 2008"


def test_an_exact_repeat_is_kept_once(tmp_path: Path) -> None:
    rows = list(
        KidbDataExtractor().extract(landed(tmp_path, "kidb-ino.csv", DATA_CSV, kidb.DATASET))
    )
    poverty = [r for r in rows if r["columns"]["code"] == "SI_POV_DDAY"]
    assert len(poverty) == 1


def test_a_repeat_with_another_value_is_not_hidden(tmp_path: Path) -> None:
    content = DATA_CSV + b"\nADB:DF_KIDB(1.0),A,SI_POV_DDAY,INO,2000,70.1,PCT,0,1,,A,,,World Bank,"
    rows = list(
        KidbDataExtractor().extract(landed(tmp_path, "kidb-ino.csv", content, kidb.DATASET))
    )
    assert [r["columns"]["value"] for r in rows if r["columns"]["code"] == "SI_POV_DDAY"] == [
        "65.67724306",
        "70.1",
    ]


def test_an_error_page_is_refused_rather_than_read(tmp_path: Path) -> None:
    content = b'<?xml version="1.0"?>\n<message:Error>Illegal parameter</message:Error>\n'
    with pytest.raises(ExtractionError):
        list(KidbDataExtractor().extract(landed(tmp_path, "kidb-ino.csv", content, kidb.DATASET)))


@pytest.mark.parametrize(
    ("unit", "multiplier", "expected"),
    [
        ("IDR", "12", "IDR trillion"),
        ("USD", "6", "USD million"),
        ("PCT", "0", "PCT"),
        ("MT", "15", "MT x10^15"),
        ("PCT", "", "PCT"),
    ],
)
def test_unit_label(unit: str, multiplier: str, expected: str) -> None:
    assert unit_label(unit, multiplier) == expected


def test_codelist_names_each_code_under_the_same_identifier(tmp_path: Path) -> None:
    content = json.dumps(CODELIST).encode()
    rows = list(
        KidbCodelistExtractor().extract(
            landed(tmp_path, "cl-kidb-indicators.json", content, kidb.CODELIST_DATASET)
        )
    )
    assert [r["columns"]["code"] for r in rows] == ["NGDP_XDC", "SI_POV_DDAY"]
    assert rows[0]["columns"]["indicator"] == indicator_id("NGDP_XDC")
    assert rows[0]["columns"]["title"] == "GDP at current prices"
    assert rows[0]["columns"]["notes"] == "Gross."
    assert rows[1]["columns"]["title"] == "Poverty headcount at $2.15"


def test_kidb_extractors_claim_before_the_generic_readers(tmp_path: Path) -> None:
    csv_file = landed(tmp_path, "kidb-ino.csv", DATA_CSV, kidb.DATASET)
    json_file = landed(tmp_path, "cl.json", b"{}", kidb.CODELIST_DATASET)
    assert isinstance(next(e for e in DEFAULT_EXTRACTORS if e.handles(csv_file)), KidbDataExtractor)
    assert isinstance(
        next(e for e in DEFAULT_EXTRACTORS if e.handles(json_file)), KidbCodelistExtractor
    )


def test_source_asks_for_indonesia_by_adbs_code() -> None:
    urls = [endpoint.url for endpoint in kidb.KeyIndicators.endpoints]
    assert urls[0] == "https://kidb.adb.org/api/v5/sdmx/data/ADB,DF_KIDB/A..INO?format=sdmx-csv"
    assert "CL_KIDB_INDICATORS" in urls[1]


def test_data_library_is_registered_but_gated() -> None:
    source = kidb.DataLibrary()
    assert source.meta.active is False
    with pytest.raises(AccessNotProvisioned):
        list(source.collect(ScrapeContext()))
