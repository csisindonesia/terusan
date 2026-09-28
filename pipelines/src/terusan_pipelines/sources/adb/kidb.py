"""ADB's Key Indicators Database — Indonesia, every indicator ADB compiles.

The link people are given is ADB's Data Library,
`data.adb.org/search/content/type/dataset/countries/indonesia-22`. It cannot be
collected: every path on `data.adb.org` — the search, the dataset pages, the
file downloads under `/media/<id>/download`, even `/data.json` — answers a
Cloudflare managed challenge (`cf-mitigated: challenge`, HTTP 403) to anything
that is not a browser running JavaScript. It is registered below as
`adb-data-library`, gated, so the catalogue records the gap rather than
omitting it.

Most of what the Data Library lists for Indonesia is the Key Indicators for
Asia and the Pacific, and that is served in full by KIDB, ADB's statistical
database, at `kidb.adb.org`. KIDB answers an SDMX REST API to any client — the
same one its own explorer reads — so the figures are collected from there:

- `/api/v5/sdmx/data/ADB,DF_KIDB/A..INO?format=sdmx-csv` is every indicator
  in the database for Indonesia (ADB's economy code is `INO`, not the ISO
  `IDN`), 2000 onward, one row per indicator and year: national accounts,
  prices, money and finance, government finance, external trade and debt,
  population, labour, poverty, energy, environment, transport and the SDG
  indicators. About five hundred and fifty series and eleven thousand
  observations in one 2 MB response. `DF_KIDB` is the umbrella flow; the
  topical flows (`DF_NA`, `DF_PRI`, ...) are subsets of it, so fetching them
  as well would land every figure twice.
- `/api/v5/sdmx/structure/codelist/ADB/CL_KIDB_INDICATORS/+` is what the codes
  mean. The data states `NGDP_XDC` and `IDR` and a unit multiplier of 12 and
  nothing else; the codelist is where `NGDP_XDC` is called "GDP at current
  prices" and where ADB says what each series counts, often in a paragraph.
  It lands as its own collection so that paragraph is not copied onto every
  observation.

KIDB is annual only: the API accepts `Q` and `M` in the frequency position and
returns the annual figures regardless.

Left out: `DF_KIDB_ITG`, exports and imports by partner. It is built on a
different structure (partner and currency dimensions, its own codelists), and
the same bilateral figures reach the lake from Comtrade and WITS at a finer
grain. It is one more endpoint the day someone wants ADB's version of them.

Nothing is parsed here. The history is one request, so there is nothing for
`ctx.since` to narrow; a monthly run that finds ADB unchanged lands nothing,
because landing is content-addressed.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import ApiSource, Endpoint, GatedSource

KIDB = "https://kidb.adb.org"
SDMX = f"{KIDB}/api/v5/sdmx"

#: ADB's code for Indonesia. Its economy codelist predates ISO 3166 in its
#: statistics and keeps its own three letters for most members.
ECONOMY = "INO"

#: The figures. This name reaches the portal as the collection's slug.
DATASET = "adb-key-indicators"

#: What the indicator codes mean, one row per code. Produces no observations.
CODELIST_DATASET = "adb-key-indicators-codelist"

LICENSE = "CC BY 3.0 IGO — Asian Development Bank"


def data_url(dataflow: str = "DF_KIDB", economy: str = ECONOMY) -> str:
    """One dataflow, every indicator, for one economy, as SDMX-CSV."""
    return f"{SDMX}/data/ADB,{dataflow}/A..{economy}?format=sdmx-csv"


def codelist_url(codelist: str = "CL_KIDB_INDICATORS") -> str:
    return f"{SDMX}/structure/codelist/ADB/{codelist}/+?format=sdmx-json"


class KeyIndicators(ApiSource):
    """Every KIDB indicator for Indonesia, and the codelist that names them."""

    meta = SourceMeta(
        slug="adb-kidb",
        name="ADB — Key Indicators Database",
        organization="Asian Development Bank",
        category=Category.STATISTICS,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.API,
        base_url=KIDB,
        license=LICENSE,
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=1.0,
        # Monthly, though the Key Indicators are an annual publication: KIDB
        # revises between editions, and two requests a month cost nothing when
        # nothing moved.
        schedule="20 4 6 * *",
        notes=(
            "SDMX REST API at kidb.adb.org. Two requests: the DF_KIDB dataflow for "
            "economy INO as SDMX-CSV, and the CL_KIDB_INDICATORS codelist as "
            "SDMX-JSON. ADB compiles most Indonesian series from BPS, Bank "
            "Indonesia and the Ministry of Finance; each row names its original "
            "source in DATA_SOURCE."
        ),
    )

    endpoints = (
        Endpoint(
            dataset=DATASET,
            url=data_url(),
            filename="kidb-ino.csv",
            media_type="text/csv",
            metadata={
                "title": "Key Indicators Database — Indonesia",
                "document_type": "data_file",
                "dataflow": "DF_KIDB",
                "economy": ECONOMY,
            },
        ),
        Endpoint(
            dataset=CODELIST_DATASET,
            url=codelist_url(),
            filename="cl-kidb-indicators.json",
            metadata={
                "title": "Key Indicators Database — indicator codelist",
                "document_type": "data_file",
                "codelist": "CL_KIDB_INDICATORS",
            },
        ),
    )


class DataLibrary(GatedSource):
    """ADB's Data Library, which sits behind a browser challenge."""

    meta = SourceMeta(
        slug="adb-data-library",
        name="ADB — Data Library",
        organization="Asian Development Bank",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url="https://data.adb.org/search/content/type/dataset/countries/indonesia-22",
        license=LICENSE,
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "ADB's dataset catalogue: the Key Indicators country workbooks, Asian "
            "Development Outlook tables, multiregional input-output tables and "
            "project-level datasets. The Key Indicators are collected through "
            "adb-kidb instead; the rest is not held."
        ),
    )

    access = (
        "every path on data.adb.org answers a Cloudflare managed challenge to "
        "non-browser clients; ask ADB's data team to exempt the API or the "
        "/media downloads, or download the needed files by hand and land them "
        "through a manual source"
    )
