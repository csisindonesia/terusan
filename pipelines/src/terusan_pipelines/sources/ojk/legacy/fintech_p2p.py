# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: OJK Fintech P2P Lending (LPBBTI) monthly statistics - national totals
Source page: https://ojk.go.id/id/kanal/iknb/data-dan-statistik/fintech/default.aspx
Covers 13 indicators in Catalogue_Regional (all monthly, national level):
  Tabel 5  -> Number of lender accounts, Lender funds
  Tabel 6  -> Number of loan recipients, Total loan disbursements
  Tabel 9  -> Number of active borrower accounts, Outstanding loan, TWP 90
  Tabel 13 -> Accumulated lender accounts
  Tabel 14 -> Accumulated borrower accounts
  Tabel 15 -> Accumulated lender lending transactions
  Tabel 16 -> Accumulated borrower credit transactions
  Tabel 17 -> Accumulated fund provided by lender
  Tabel 18 -> Accumulated loan disbursement to borrowers

Network note: OJK's WAF rejects a bare `curl`/`requests` User-Agent with
"Request Rejected". A full modern-browser UA plus Accept/Accept-Language
headers works. See HEADERS below.

Discovery process (reverse-engineered, confirmed working 2026-09-07):
1. GET the fintech statistics index page (`default.aspx`). It is a SharePoint
   (ASP.NET) publishing page. The document library itself is not enumerable
   via a plain REST call from the rendered HTML (listId/listUrl are empty --
   the web part hydrates client-side), but the page *does* server-render a
   list of monthly article links in the form:
     /id/kanal/iknb/data-dan-statistik/fintech/Pages/Statistik-LPBBTI-<Month>-<Year>.aspx
   Page 1 of that list (the default view) is already newest-first (the
   "previous page" paging control is disabled), so the first such link found
   is the latest published month.
2. GET that article page. It server-renders a direct link to the workbook:
     /id/kanal/iknb/data-dan-statistik/fintech/Documents/STATISTIK%20LPBBTI%20<Month>%20<Year>.xlsx
3. Download that .xlsx (a single workbook with numbered sheets "5", "6", "9",
   "13".."18" among others -- confirmed via openpyxl on the Desember 2025
   workbook) and parse it.

Sheet layout for tables 5/6/9 (location breakdown, wide-by-month):
  Row 1: table title. Row 2: merged month header (one date per month, spanning
  2 or 3 columns). Row 3: sub-indicator label per column (matches the
  Indonesian labels quoted in the indicator catalogue). Rows 4.. : province
  rows, grouped "a. Jawa" / "b. Luar Jawa" / "c. Luar Negeri", terminated by a
  row whose column B is exactly "JUMLAH" -- the national total row we want.

Sheet layout for tables 13-18 (accumulated, one column per month, no
sub-indicator split): same row/column shape, but row 3 just repeats a
boilerplate "Akumulasi Sejak Perusahaan Didirikan s.d Akhir Posisi Bulan"
label in each column, so the indicator is identified by table/sheet number
alone and the value is read straight from the "JUMLAH" row's last populated
month column.

Latest month is picked by taking the right-most column in row 2 that carries
a date value in each sheet (equivalent to "the JUMLAH row's last populated
month column").
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass

import openpyxl
import requests

BASE = "https://ojk.go.id"
INDEX_URL = f"{BASE}/id/kanal/iknb/data-dan-statistik/fintech/default.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

ARTICLE_LINK_RE = re.compile(
    r'href="(/id/kanal/iknb/data-dan-statistik/fintech/Pages/Statistik-LPBBTI-[^"]+\.aspx)"',
    re.IGNORECASE,
)
WORKBOOK_LINK_RE = re.compile(
    r'href="(/id/kanal/iknb/data-dan-statistik/fintech/Documents/[^"]+\.xlsx)"',
    re.IGNORECASE,
)

# The 13 indicators, in Catalogue_Regional order.
# kind:
#   "subcol"  -> table 5/6/9 style: identify the national total row ("JUMLAH")
#                then pick the sub-column (matched via row-3 label) under the
#                latest month's merged header.
#   "lastcol" -> table 13-18 style: national total row, right-most populated
#                month column.
INDICATORS = [
    {"name": "Number of lender accounts", "sheet": "5", "kind": "subcol",
     "label": "Jumlah Rekening Pemberi Pinjaman (akun)"},
    {"name": "Lender funds", "sheet": "5", "kind": "subcol",
     "label": "Jumlah Dana yang Diberikan (miliar Rp)"},
    {"name": "Number of loan recipients", "sheet": "6", "kind": "subcol",
     "label": "Jumlah Penerima Pinjaman (akun)"},
    {"name": "Total loan disbursements", "sheet": "6", "kind": "subcol",
     "label": "Jumlah Penyaluran Pinjaman (miliar Rp)"},
    {"name": "Number of active borrower accounts", "sheet": "9", "kind": "subcol",
     "label": "Jumlah Rekening Penerima Pinjaman Aktif (entitas)"},
    {"name": "Outstanding loan", "sheet": "9", "kind": "subcol",
     "label": "Outstanding Pinjaman (miliar Rp)"},
    {"name": "90-Day Default Rate (TWP 90)", "sheet": "9", "kind": "subcol",
     "label": "TWP 90"},
    {"name": "Accumulated lender accounts", "sheet": "13", "kind": "lastcol"},
    {"name": "Accumulated borrower accounts", "sheet": "14", "kind": "lastcol"},
    {"name": "Accumulated lender lending transactions", "sheet": "15", "kind": "lastcol"},
    {"name": "Accumulated borrower credit transactions", "sheet": "16", "kind": "lastcol"},
    {"name": "Accumulated fund provided by lender", "sheet": "17", "kind": "lastcol"},
    {"name": "Accumulated loan disbursement to borrowers", "sheet": "18", "kind": "lastcol"},
]


@dataclass
class Indicator:
    name: str
    sheet: str
    value: float | int | None
    period: str | None
    source_cell: str | None


def find_latest_article_url() -> str:
    """Fetch the fintech statistics index page and return the newest
    'Statistik LPBBTI <Month> <Year>' article URL (page 1 is newest-first)."""
    r = requests.get(INDEX_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    links = ARTICLE_LINK_RE.findall(r.text)
    # Drop non-monthly announcement pages (e.g. "PENGUMUMAN-...") just in case.
    links = [l for l in links if "Statistik-LPBBTI-" in l and "PENGUMUMAN" not in l]
    if not links:
        raise RuntimeError("No 'Statistik-LPBBTI-*' article links found on index page")
    return BASE + links[0]


def find_workbook_url(article_url: str) -> str:
    r = requests.get(article_url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    links = WORKBOOK_LINK_RE.findall(r.text)
    if not links:
        raise RuntimeError(f"No .xlsx link found on article page {article_url}")
    # Prefer the non-percent-encoded variant if both appear; either downloads fine.
    return BASE + links[0]


def download_workbook(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.content


def _find_jumlah_row(ws) -> int:
    for r in range(1, ws.max_row + 1):
        for c in (1, 2):
            v = ws.cell(row=r, column=c).value
            if isinstance(v, str) and v.strip().upper() == "JUMLAH":
                return r
    raise RuntimeError(f"'JUMLAH' total row not found in sheet '{ws.title}'")


def _latest_month_columns(ws) -> list[int]:
    """Return the column indices belonging to the right-most dated header in
    row 2 (a single column for tables 13-18, or a merged span of 2-3 columns
    for tables 5/6/9)."""
    dated_cols = [c for c in range(1, ws.max_column + 1)
                  if isinstance(ws.cell(row=2, column=c).value, (dt.datetime, dt.date))]
    if not dated_cols:
        raise RuntimeError(f"No month headers found in row 2 of sheet '{ws.title}'")
    last_start = max(dated_cols)
    # Collect the merged span starting at last_start, if any.
    span_end = last_start
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= 2 <= rng.max_row and rng.min_col == last_start:
            span_end = rng.max_col
            break
    else:
        # Not merged: for subcol sheets the header still spans a couple of
        # plain (unmerged) columns up to the next dated column or sheet end.
        next_dated = [c for c in dated_cols if c > last_start]
        span_end = (min(next_dated) - 1) if next_dated else ws.max_column
    return list(range(last_start, span_end + 1))


def _period_label(ws, col: int) -> str:
    v = ws.cell(row=2, column=col).value
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m")
    return str(v)


def extract_indicator(wb, spec: dict) -> Indicator:
    ws = wb[spec["sheet"]]
    jrow = _find_jumlah_row(ws)
    cols = _latest_month_columns(ws)

    if spec["kind"] == "lastcol":
        col = cols[0]
        value = ws.cell(row=jrow, column=col).value
        return Indicator(spec["name"], spec["sheet"], value, _period_label(ws, col),
                          ws.cell(row=jrow, column=col).coordinate)

    # kind == "subcol": match row-3 label within the latest month's column span.
    # The month header in row 2 is merged, so only the span's first column
    # (cols[0]) actually carries the date value -- use that for the period
    # label regardless of which sub-column within the span matches.
    target_label = spec["label"].strip().lower()
    for col in cols:
        label = ws.cell(row=3, column=col).value
        if isinstance(label, str) and label.strip().lower() == target_label:
            value = ws.cell(row=jrow, column=col).value
            return Indicator(spec["name"], spec["sheet"], value, _period_label(ws, cols[0]),
                              ws.cell(row=jrow, column=col).coordinate)
    raise RuntimeError(
        f"Label '{spec['label']}' not found among columns {cols} (row 3) of sheet '{spec['sheet']}'"
    )


def scrape() -> list[Indicator]:
    article_url = find_latest_article_url()
    workbook_url = find_workbook_url(article_url)
    content = download_workbook(workbook_url)
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    results = [extract_indicator(wb, spec) for spec in INDICATORS]
    return results, workbook_url


def save_csv(rows: list[Indicator], workbook_url: str, out_path: str):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["indicator", "sheet", "period", "value", "source_cell", "workbook_url"])
        for row in rows:
            w.writerow([row.name, row.sheet, row.period, row.value, row.source_cell, workbook_url])


