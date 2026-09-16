# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: OJK Statistik Perbankan Indonesia (SPI) - Commercial/Rural Bank statistics
Source page: https://ojk.go.id/id/kanal/perbankan/data-dan-statistik/statistik-perbankan-indonesia/default.aspx
Covers ~20 banking indicators in Catalogue_Regional sourced from the monthly SPI
publication, referencing specific table numbers within the SPI Excel workbook:
  1.33.a  Bank - Demand/Savings/Time/Total DPK by Rupiah+Valas, by bank location
  1.34.a  Bank - Third Party Funds (DPK) by bank location, monthly time series
  1.43.a  Bank - Branch offices growth by location, monthly time series
  2.8     Rural Bank (BPR) - Savings/Time deposits/Total DPK/portion, by location
  2.14    Rural Bank (BPR) - growth (count of KP/KC/KPK offices) by location
  3.12.a  Bank credit by purpose (Modal Kerja/Investasi/Konsumsi) and orientation
          (Ekspor/Impor/Lainnya), by bank location
  3.13.a  Bank credit + NPL by bank location, monthly time series
  3.18.a  Rural Bank (BPR) credit by location, monthly time series
  3.22.a  SME/MKM (UMKM) credit by project location, monthly time series

IMPORTANT (network): OJK's site is behind an Akamai-style WAF. A bare
`requests.get()` / plain curl User-Agent gets a "Request Rejected" block.
A full modern-browser User-Agent + Accept + Accept-Language header set
(see HEADERS below) is required and reliably works.

IMPORTANT (data location): As of the publication for period July 2025 onward,
OJK moved SPI publication entirely to a new interactive portal
(https://data.ojk.go.id/SJKPublic) - see the notice embedded in the old page's
webpart HTML. The legacy SharePoint page (fetched here) still hosts an ARCHIVE
of monthly editions up to and including June 2025, each as its own sub-page
under .../statistik-perbankan-indonesia/Pages/Statistik-Perbankan-Indonesia---
<Month>-<Year>.aspx, which links to the real downloadable files:
  .../Documents/Pages/Statistik-Perbankan-Indonesia---<Month>-<Year>/
      STATISTIK PERBANKAN INDONESIA  -<Month> <Year>.xlsx
      STATISTIK PERBANKAN INDONESIA  -<Month> <Year>.pdf
Both xlsx and pdf are published; this scraper uses the xlsx (pandas/openpyxl
parseable; no PDF table extraction needed). The workbook has 50 sheets, one
(or a small cluster) per SPI table number, with sheet names like
'Komp DPK per Lok_1.33.a.' -- all 9 tables above were confirmed present.

This scraper does NOT (yet) pull from the new data.ojk.go.id/SJKPublic portal
for July-2025-onward periods -- that portal is a JS-rendered interactive
dashboard, not a static file archive, and would need separate investigation
(likely a different API). See LIMITATIONS at the bottom of this file.
"""
from __future__ import annotations

import csv
import io
import re
import tempfile
from dataclasses import dataclass
from urllib.parse import urljoin

import openpyxl
import requests

BASE = "https://ojk.go.id"
MAIN_PAGE_URL = (
    f"{BASE}/id/kanal/perbankan/data-dan-statistik/statistik-perbankan-indonesia/default.aspx"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

MONTH_ID_TO_NUM = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10,
    "november": 11, "desember": 12,
}

ANCHOR_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
NUM_CELL_RE = re.compile(r"^\d{1,3}\.$")


@dataclass
class SpiEdition:
    label: str          # e.g. "Statistik Perbankan Indonesia - Juni 2025"
    year: int
    month: int
    page_url: str


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r


def _extract_anchors(html: str):
    for m in ANCHOR_RE.finditer(html):
        href, text = m.group(1), m.group(2)
        yield href, TAG_RE.sub("", text).strip()


def list_editions() -> list[SpiEdition]:
    """Fetch the SPI landing page and return the archive of monthly editions
    (each a link to a Pages/*.aspx sub-page), newest first as listed on-page."""
    html = _get(MAIN_PAGE_URL).text
    editions = []
    for href, text in _extract_anchors(html):
        m = re.match(r"Statistik Perbankan Indonesia\s*-\s*(\w+)\s+(\d{4})$", text)
        if not m:
            continue
        month_name, year = m.group(1).lower(), int(m.group(2))
        month = MONTH_ID_TO_NUM.get(month_name)
        if not month:
            continue
        editions.append(SpiEdition(label=text, year=year, month=month, page_url=urljoin(BASE, href)))
    return editions


def pick_edition(editions: list[SpiEdition], year: int | None, month: int | None) -> SpiEdition:
    if year is None and month is None:
        if not editions:
            raise RuntimeError("No SPI editions found on landing page")
        return editions[0]  # page lists newest first
    for ed in editions:
        if (year is None or ed.year == year) and (month is None or ed.month == month):
            return ed
    raise RuntimeError(f"No SPI edition found for year={year} month={month}")


def get_xlsx_url(edition: SpiEdition) -> str:
    html = _get(edition.page_url).text
    for href, text in _extract_anchors(html):
        if href.lower().endswith(".xlsx"):
            return urljoin(BASE, href)
    raise RuntimeError(f"No .xlsx download link found on {edition.page_url}")


def download_xlsx(url: str, dest_path: str | None = None) -> str:
    r = _get(url)
    if dest_path is None:
        fd, dest_path = tempfile.mkstemp(suffix=".xlsx", prefix="ojk_spi_")
        with io.FileIO(fd, "wb") as f:
            f.write(r.content)
    else:
        with open(dest_path, "wb") as f:
            f.write(r.content)
    return dest_path


# --------------------------------------------------------------------------
# Table parsing
# --------------------------------------------------------------------------

def _clean(v):
    if isinstance(v, str):
        v = v.strip()
    return v


def _iter_rows(ws, max_row=500):
    return list(ws.iter_rows(min_row=1, max_row=max_row, values_only=True))


def extract_simple_series(ws, num_col: int, name_col: int, data_start: int):
    """Location rows with a flat run of time-series values, one row per
    location, no interleaved sub-rows. Stops at first row that doesn't carry
    a 'N.' numbering cell at num_col (e.g. hits a Total/section-break row)."""
    out = []
    for row in _iter_rows(ws):
        if num_col >= len(row) or name_col >= len(row):
            continue
        num, name = row[num_col], row[name_col]
        if not (isinstance(num, str) and NUM_CELL_RE.match(num.strip())):
            if out:
                break
            continue
        values = [_clean(v) for v in row[data_start:] if v is not None]
        out.append({"location": str(name).strip(), "latest_value": values[-1] if values else None,
                     "series": values})
    return out


def extract_series_with_npl(ws, num_col: int, name_col: int, data_start: int):
    """Like extract_simple_series, but each location row is immediately
    followed by an 'NPL/NPF' sub-row with the same column layout."""
    out = []
    rows = _iter_rows(ws)
    i = 0
    started = False
    while i < len(rows):
        row = rows[i]
        num = row[num_col] if num_col < len(row) else None
        name = row[name_col] if name_col < len(row) else None
        if isinstance(num, str) and NUM_CELL_RE.match(num.strip()):
            started = True
            values = [_clean(v) for v in row[data_start:] if v is not None]
            entry = {"location": str(name).strip(), "latest_value": values[-1] if values else None,
                      "series": values}
            # look at next row for NPL sub-row (num cell empty, name mentions NPL)
            if i + 1 < len(rows):
                nrow = rows[i + 1]
                nname = nrow[name_col] if name_col < len(nrow) else None
                if isinstance(nname, str) and "NPL" in nname.upper():
                    nvalues = [_clean(v) for v in nrow[data_start:] if v is not None]
                    entry["latest_npl"] = nvalues[-1] if nvalues else None
                    entry["npl_series"] = nvalues
                    i += 1
            out.append(entry)
        elif started:
            break
        i += 1
    return out


def extract_snapshot(ws, num_col: int, name_col: int, data_start: int, columns: list[str]):
    """Single-period 'composition' tables: one row per location with a fixed
    set of named value columns (no time series)."""
    out = []
    for row in _iter_rows(ws):
        if num_col >= len(row) or name_col >= len(row):
            continue
        num, name = row[num_col], row[name_col]
        if not (isinstance(num, str) and NUM_CELL_RE.match(num.strip())):
            if out:
                break
            continue
        vals = list(row[data_start:data_start + len(columns)])
        entry = {"location": str(name).strip()}
        entry.update({col: _clean(v) for col, v in zip(columns, vals)})
        out.append(entry)
    return out


def extract_credit_by_purpose(ws, num_col: int, name_col: int, data_start: int, columns: list[str]):
    """Table 3.12.a shape: location row with purpose/orientation columns,
    immediately followed by an NPL/NPF sub-row with the same columns."""
    out = []
    rows = _iter_rows(ws)
    i = 0
    started = False
    while i < len(rows):
        row = rows[i]
        num = row[num_col] if num_col < len(row) else None
        name = row[name_col] if name_col < len(row) else None
        if isinstance(num, str) and NUM_CELL_RE.match(num.strip()):
            started = True
            vals = list(row[data_start:data_start + len(columns)])
            entry = {"location": str(name).strip()}
            entry.update({col: _clean(v) for col, v in zip(columns, vals)})
            if i + 1 < len(rows):
                nrow = rows[i + 1]
                nname = nrow[name_col] if name_col < len(nrow) else None
                if isinstance(nname, str) and "NPL" in nname.upper():
                    nvals = list(nrow[data_start:data_start + len(columns)])
                    entry.update({f"{col}_NPL": _clean(v) for col, v in zip(columns, nvals)})
                    i += 1
            out.append(entry)
        elif started:
            break
        i += 1
    return out


# Sheet-name -> (table_no, extractor, kwargs) mapping. Sheet names are stable
# across monthly editions (only the "as of <Month> <Year>" text inside the
# sheet changes), confirmed against the June 2025 workbook.
TABLE_SPECS = {
    "1.33.a": dict(
        sheet="Komp DPK per Lok_1.33.a.",
        kind="snapshot",
        num_col=1, name_col=2, data_start=3,
        columns=["Giro_Rupiah", "Giro_Valas", "Tabungan_Rupiah", "Tabungan_Valas",
                 "Deposito_Rupiah", "Deposito_Valas", "TotalDPK_Rupiah", "TotalDPK_Valas",
                 "TotalDPK_Total", "Pangsa_Pct"],
    ),
    "1.34.a": dict(
        sheet="DPK per Lok_1.34.a.",
        kind="series",
        num_col=0, name_col=1, data_start=2,
    ),
    "1.43.a": dict(
        sheet="Juml KC per Lok_1.43.a.",
        kind="series",
        num_col=0, name_col=1, data_start=2,
    ),
    "2.8": dict(
        sheet="Komp DPK BPR Lok_2.8",
        kind="snapshot",
        num_col=1, name_col=2, data_start=3,
        columns=["Tabungan", "Deposito", "TotalDPK", "Pangsa_Pct"],
    ),
    "2.14": dict(
        sheet="Juml BPR per Lok_2.14",
        kind="snapshot",
        num_col=1, name_col=2, data_start=3,
        columns=["KP", "KC", "KPK", "Total"],
    ),
    "3.12.a": dict(
        sheet="Kredit JP-OP per Lok._3.12.a.",
        kind="purpose",
        num_col=1, name_col=2, data_start=3,
        columns=["ModalKerja", "Investasi", "Konsumsi", "Ekspor", "Impor", "Lainnya"],
    ),
    "3.13.a": dict(
        sheet="Kredit per Lok. Dati I_3.13.a",
        kind="series_npl",
        num_col=0, name_col=1, data_start=2,
    ),
    "3.18.a": dict(
        sheet="Kredit BPR per Lok_3.18.a.",
        kind="series",
        num_col=1, name_col=2, data_start=3,
    ),
    "3.22.a": dict(
        sheet="UMKM Lok. Dati I_3.22.a.",
        kind="series",
        num_col=0, name_col=1, data_start=2,
    ),
}


def parse_workbook(xlsx_path: str) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    results = {}
    for table_no, spec in TABLE_SPECS.items():
        sheet_name = spec["sheet"]
        if sheet_name not in wb.sheetnames:
            results[table_no] = {"error": f"sheet '{sheet_name}' not found"}
            continue
        ws = wb[sheet_name]
        kind = spec["kind"]
        if kind == "snapshot":
            rows = extract_snapshot(ws, spec["num_col"], spec["name_col"], spec["data_start"], spec["columns"])
        elif kind == "series":
            rows = extract_simple_series(ws, spec["num_col"], spec["name_col"], spec["data_start"])
        elif kind == "series_npl":
            rows = extract_series_with_npl(ws, spec["num_col"], spec["name_col"], spec["data_start"])
        elif kind == "purpose":
            rows = extract_credit_by_purpose(ws, spec["num_col"], spec["name_col"], spec["data_start"], spec["columns"])
        else:
            rows = []
        results[table_no] = {"sheet": sheet_name, "rows": rows}
    return results


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------

def save_table_csv(table_no: str, rows: list[dict], out_path: str):
    if not rows:
        return
    fields = sorted({k for row in rows for k in row.keys()},
                     key=lambda k: (k not in ("location",), k))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            out_row = {}
            for k in fields:
                v = row.get(k)
                if isinstance(v, list):
                    v = ";".join(str(x) for x in v)
                out_row[k] = v
            w.writerow(out_row)


def run(year: int | None = None, month: int | None = None,
        xlsx_cache_path: str | None = None) -> tuple[SpiEdition, dict]:
    editions = list_editions()
    edition = pick_edition(editions, year, month)
    xlsx_url = get_xlsx_url(edition)
    xlsx_path = download_xlsx(xlsx_url, xlsx_cache_path)
    results = parse_workbook(xlsx_path)
    return edition, results


