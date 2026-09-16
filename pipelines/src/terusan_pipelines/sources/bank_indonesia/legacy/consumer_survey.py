# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: Bank Indonesia Survei Konsumen (SK) / Consumer Survey
Source page: https://www.bi.go.id/id/publikasi/laporan/default.aspx?Kategori=survei%20konsumen&Periode=bulanan
Direct data file (verified via WebFetch on the listing page, confirmed reachable
with curl using a full browser User-Agent): https://www.bi.go.id/id/publikasi/laporan/Documents/SK.zip

IMPORTANT network note: www.bi.go.id resets the connection (curl exit 56 / "Recv
failure: Connection reset by peer") for requests with a short/default User-Agent
(e.g. "Mozilla/5.0" alone, or python-requests' default UA). It responds normally
(HTTP 200, real payload) once a full, realistic desktop-browser User-Agent string
is sent -- see HEADERS below.

The ZIP always contains a single xlsx named like "Tabel Series SK BI <Month> <Year>.xlsx"
with sheets Tabel 1..Tabel 9. Sheets are matched by scanning each sheet's title text
(rows 1-6) rather than trusting a fixed table number, because the live file's actual
layout differs from the numbering implied by the indicator catalogue:
  - catalogue says CCI/IKK, CECI/IKE, CEI/IEK per-city all live on "Tabel 6"; in the
    live file, Tabel 6 is actually "Perkiraan Tabungan, Utang, dan Konsumsi" (unrelated).
    The real per-city IKK/IKE/IEK table is **Tabel 8**: "Perkembangan Indeks Keyakinan
    Konsumen dan Indeks Ekspektasi Harga di 18 Kota (dalam indeks)" -- one block of rows
    per city, with "-" sub-rows named exactly "Indeks Keyakinan Konsumen (IKK)",
    "Indeks Kondisi Ekonomi Saat Ini (IKE)", "Indeks Ekspektasi Konsumen (IEK)" (plus
    3-month/6-month/12-month price-expectation rows we don't need here).
  - catalogue also lists "Consumer confidence index (raw)" as an infographic embedded
    inside a PDF. Rather than scrape a PDF infographic, this scraper pulls the
    equivalent **national (18-city combined)** raw IKK/IKE/IEK series straight out of
    the same Excel file, sheet **Tabel 1**: "Perkembangan Indeks Keyakinan Konsumen dan
    Indeks Ekspektasi Harga Gabungan 18 Kota (dalam indeks)". This is the authoritative
    numeric source behind that PDF infographic and is far more reliable to parse.
Matching by title keywords instead of a fixed sheet index makes this robust across
future publications even if BI re-numbers/re-orders sheets again.
"""
import io
import re
import zipfile

import openpyxl
import requests

ZIP_URL = "https://www.bi.go.id/id/publikasi/laporan/Documents/SK.zip"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# indicator name -> label as it appears in the "- <label>" sub-rows
INDICATOR_ROW_LABELS = {
    "cci_ikk": "Indeks Keyakinan Konsumen (IKK)",
    "ceci_ike": "Indeks Kondisi Ekonomi Saat Ini (IKE)",
    "cei_iek": "Indeks Ekspektasi Konsumen (IEK)",
}

CITY_ROW_RE = re.compile(r"^\d+\.\s*(.+)$")


def download_zip(url: str = ZIP_URL) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content


def extract_workbook(zip_bytes: bytes) -> openpyxl.Workbook:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xlsx_names = [n for n in zf.namelist() if n.lower().endswith(".xlsx")]
        if not xlsx_names:
            raise RuntimeError("No .xlsx file found inside SK.zip")
        data = zf.read(xlsx_names[0])
    return openpyxl.load_workbook(io.BytesIO(data), data_only=True)


def sheet_title(ws) -> str:
    parts = []
    for row in ws.iter_rows(min_row=1, max_row=6, max_col=10, values_only=True):
        for v in row:
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    return " ".join(parts)


def find_sheet(wb, must_include, must_exclude=()):
    for name in wb.sheetnames:
        title = sheet_title(wb[name])
        if all(kw in title for kw in must_include) and not any(kw in title for kw in must_exclude):
            return wb[name]
    return None


def _periods_by_column(ws, header_year_row, header_period_row, first_data_col, max_scan_col=400):
    """Forward-fill year across merged header cells; yield (col, year, period_label)."""
    year = None
    periods = []
    for c in range(first_data_col, max_scan_col):
        y = ws.cell(row=header_year_row, column=c).value
        m = ws.cell(row=header_period_row, column=c).value
        if isinstance(y, (int, float)):
            year = int(y)
        if isinstance(m, str) and year is not None:
            periods.append((c, year, m))
    return periods


def parse_percity_ikk_table(ws):
    """Tabel 8 layout: city header rows '<n>. <City>' in col B, then '-' sub-rows
    with the indicator label in col C and data starting col D."""
    periods = _periods_by_column(ws, header_year_row=4, header_period_row=5, first_data_col=4)

    records = []
    current_city = None
    for r in range(1, ws.max_row + 1):
        col_b = ws.cell(row=r, column=2).value
        if isinstance(col_b, str):
            m = CITY_ROW_RE.match(col_b.strip())
            if m:
                current_city = m.group(1).strip()
                continue
        col_c = ws.cell(row=r, column=3).value
        if current_city and isinstance(col_c, str):
            label = col_c.strip()
            for indicator, wanted_label in INDICATOR_ROW_LABELS.items():
                if label == wanted_label:
                    for c, year, period in periods:
                        val = ws.cell(row=r, column=c).value
                        if isinstance(val, (int, float)):
                            records.append({
                                "scope": current_city,
                                "indicator": indicator,
                                "year": year,
                                "period": period,
                                "value": val,
                            })
    return records


def parse_national_ikk_table(ws):
    """Tabel 1 layout: national (18-city combined) series. Indicator label in col F,
    data starting col G. Header year/period rows are 6/7 (title occupies rows 3-4)."""
    periods = _periods_by_column(ws, header_year_row=6, header_period_row=7, first_data_col=7)

    records = []
    for r in range(1, ws.max_row + 1):
        col_f = ws.cell(row=r, column=6).value
        if isinstance(col_f, str):
            label = col_f.strip()
            for indicator, wanted_label in INDICATOR_ROW_LABELS.items():
                if label == wanted_label:
                    for c, year, period in periods:
                        val = ws.cell(row=r, column=c).value
                        if isinstance(val, (int, float)):
                            records.append({
                                "scope": "NASIONAL (18 kota)",
                                "indicator": f"{indicator}_raw_national",
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

    percity_ws = find_sheet(wb, must_include=["Indeks Keyakinan Konsumen", "18 Kota"],
                             must_exclude=["Gabungan"])
    if percity_ws is not None:
        recs = parse_percity_ikk_table(percity_ws)
        print(f"  per-city IKK/IKE/IEK: sheet={percity_ws.title!r} rows_parsed={len(recs)}")
        all_records.extend(recs)
    else:
        print("  WARNING: per-city IKK/IKE/IEK sheet not found")

    national_ws = find_sheet(wb, must_include=["Indeks Keyakinan Konsumen", "Gabungan", "18 Kota"])
    if national_ws is not None:
        recs = parse_national_ikk_table(national_ws)
        print(f"  national raw CCI/CECI/CEI: sheet={national_ws.title!r} rows_parsed={len(recs)}")
        all_records.extend(recs)
    else:
        print("  WARNING: national combined IKK/IKE/IEK sheet not found")

    return all_records


def save_sample_csv(records, out_path, last_n_periods=6):
    import csv
    from collections import defaultdict

    order = defaultdict(list)
    for rec in records:
        key = (rec["year"], rec["period"])
        bucket = order[rec["indicator"]]
        if key not in bucket:
            bucket.append(key)
    keep = {ind: set(keys[-last_n_periods:]) for ind, keys in order.items()}

    sample = [
        rec for rec in records
        if (rec["year"], rec["period"]) in keep.get(rec["indicator"], set())
    ]

    fields = ["indicator", "scope", "year", "period", "value"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sample)
    return len(sample)


