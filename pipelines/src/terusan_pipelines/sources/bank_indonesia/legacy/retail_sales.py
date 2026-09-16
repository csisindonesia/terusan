# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: Bank Indonesia Survei Penjualan Eceran (SPE) / Retail Sales Survey
Source page: https://www.bi.go.id/id/publikasi/laporan/default.aspx?Kategori=survei%20penjualan%20eceran&Periode=bulanan
Direct data file (verified via WebFetch on the listing page, confirmed reachable
with curl using a full browser User-Agent): https://www.bi.go.id/id/publikasi/laporan/Documents/spe.zip

IMPORTANT network note: www.bi.go.id resets the connection (curl exit 56 / "Recv
failure: Connection reset by peer") for requests with a short/default User-Agent
(e.g. "Mozilla/5.0" alone, or python-requests' default UA). It responds normally
(HTTP 200, real payload) once a full, realistic desktop-browser User-Agent string
is sent. Both the listing page and the file host use this filter, so HEADERS below
is required for `requests` to work.

The ZIP always contains a single xlsx named like "Tabel Series SPE BI <Month> <Year>.xlsx"
with sheets Tabel 1..Tabel 9. The sheets relevant to Catalogue_Regional are matched by
scanning each sheet's title text (row 2, col A) rather than trusting a fixed table
number, because the live file's table numbering does not match the numbering implied
by the indicator catalogue:
  - catalogue "Tabel 5" (Indeks Penjualan Riil Per Kota)                  -> file's Tabel 5 (matches)
  - catalogue "Tabel 6" (Pertumbuhan Tahunan ..., %, yoy)                 -> file's Tabel 7
  - catalogue "Tabel 7" (Pertumbuhan Bulanan ..., %, mtm)                 -> file's Tabel 6
  - catalogue "Tabel 8" (Pertumbuhan Triwulanan ..., %, yoy)              -> file's Tabel 8 (matches)
Matching by title keywords instead of sheet name makes this robust across future
publications even if BI re-numbers/re-orders sheets again.

Each matched sheet is a "wide" table: column A holds city names (rows 6..~14, ending
at "INDEKS TOTAL", followed by footnote rows starting with "Keterangan:"/"*"/"**"/"R").
Row 4 holds year labels (merged across each Jan-Dec, or Q1-Q4, block; only the first
cell of each block is populated), row 5 holds the period label (month/quarter) for
every column. After the main yearly series there is a small side "Perubahan" (change)
table appended a few columns later, separated by a "DESCRIPTION"/text column -- this
is skipped automatically because its label/value cells are not numeric.
"""
import io
import zipfile

import openpyxl
import requests

ZIP_URL = "https://www.bi.go.id/id/publikasi/laporan/Documents/spe.zip"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# indicator name -> keywords that must all appear in the sheet's title text
INDICATOR_TITLE_KEYWORDS = {
    "retail_sales_index": ["Indeks Penjualan Riil"],
    "retail_sales_index_growth_yoy": ["Pertumbuhan Tahunan"],
    "retail_sales_index_growth_mtm": ["Pertumbuhan Bulanan"],
    "retail_sales_index_growth_qoq_yoy": ["Pertumbuhan Triwulanan"],
}

FOOTNOTE_PREFIXES = ("keterangan", "*", "r ", "r  ")


def download_zip(url: str = ZIP_URL) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content


def extract_workbook(zip_bytes: bytes) -> openpyxl.Workbook:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xlsx_names = [n for n in zf.namelist() if n.lower().endswith(".xlsx")]
        if not xlsx_names:
            raise RuntimeError("No .xlsx file found inside spe.zip")
        data = zf.read(xlsx_names[0])
    return openpyxl.load_workbook(io.BytesIO(data), data_only=True)


def sheet_title(ws) -> str:
    parts = []
    for row in ws.iter_rows(min_row=1, max_row=3, max_col=6, values_only=True):
        for v in row:
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    return " ".join(parts)


def find_sheet(wb, keywords, require_percity=True):
    """Match a sheet by title keywords. BI also publishes near-identical
    category-level tables (Tabel 1-4: 'Menurut Kategori' / national breakdown) whose
    titles can share the same growth-type wording as the per-city tables (Tabel 5-8),
    e.g. both "Tabel 4" and "Tabel 8" are literally titled
    "Pertumbuhan Triwulanan Penjualan Riil (%, yoy)*". Title text alone is therefore
    NOT sufficient to disambiguate. We additionally verify structurally that the first
    data row (row 6, col A) is a city name ("Jakarta" is always the first city in every
    BI SPE per-city table) rather than a product/spending category.
    """
    for name in wb.sheetnames:
        ws = wb[name]
        title = sheet_title(ws)
        if not all(kw in title for kw in keywords):
            continue
        if require_percity and ws.cell(row=6, column=1).value != "Jakarta":
            continue
        return ws
    return None


def parse_wide_city_table(ws, header_year_row=4, header_period_row=5, name_col=1,
                           data_start_row=6, max_scan_col=250):
    """Parse a BI SPE 'wide' sheet: city rows x chronological period columns."""
    # 1. collect (col, year, period_label) for the main series, forward-filling year
    year = None
    periods = []  # list of (col, year:int, period:str)
    for c in range(2, max_scan_col):
        y = ws.cell(row=header_year_row, column=c).value
        m = ws.cell(row=header_period_row, column=c).value
        # year is stored as int in some sheets (e.g. Tabel 8) and as a numeric
        # string in others (e.g. Tabel 5-7), so accept both.
        if isinstance(y, (int, float)):
            year = int(y)
        elif isinstance(y, str) and y.strip().isdigit():
            year = int(y.strip())
        elif isinstance(y, str):
            # hit the side "Perubahan"/"DESCRIPTION" mini-table -> stop main series scan
            break
        if isinstance(m, str) and year is not None:
            periods.append((c, year, m))

    # 2. collect city rows until footnotes
    rows = []
    r = data_start_row
    while True:
        label = ws.cell(row=r, column=name_col).value
        if label is None:
            break
        low = str(label).strip().lower()
        if low.startswith(FOOTNOTE_PREFIXES):
            break
        rows.append((r, str(label).strip()))
        r += 1

    records = []
    for r, city in rows:
        for c, year, period in periods:
            val = ws.cell(row=r, column=c).value
            if isinstance(val, (int, float)):
                records.append({
                    "city": city,
                    "year": year,
                    "period": period,
                    "value": val,
                })
    return records


def scrape():
    print(f"Downloading {ZIP_URL} ...")
    zip_bytes = download_zip()
    print(f"  got {len(zip_bytes):,} bytes")
    wb = extract_workbook(zip_bytes)
    print(f"  workbook sheets: {wb.sheetnames}")

    all_records = []
    for indicator, keywords in INDICATOR_TITLE_KEYWORDS.items():
        ws = find_sheet(wb, keywords)
        if ws is None:
            print(f"  WARNING: no sheet found for indicator={indicator} keywords={keywords}")
            continue
        title = sheet_title(ws)
        recs = parse_wide_city_table(ws)
        print(f"  {indicator}: sheet={ws.title!r} title={title!r} rows_parsed={len(recs)}")
        for rec in recs:
            rec["indicator"] = indicator
            all_records.append(rec)
    return all_records


def save_sample_csv(records, out_path, last_n_periods=6):
    import csv
    from collections import defaultdict

    # keep only the most recent `last_n_periods` (year, period) combos per indicator,
    # based on column order already preserved in `records`.
    order = defaultdict(list)
    for rec in records:
        key = (rec["year"], rec["period"])
        if key not in order[rec["indicator"]]:
            order[rec["indicator"]].append(key)
    keep = {ind: set(keys[-last_n_periods:]) for ind, keys in order.items()}

    sample = [
        rec for rec in records
        if (rec["year"], rec["period"]) in keep.get(rec["indicator"], set())
    ]

    fields = ["indicator", "city", "year", "period", "value"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sample)
    return len(sample)


