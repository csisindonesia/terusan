# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: OJK Statistik Perbankan Indonesia (SPI) - NEW PORTAL
Source: https://data.ojk.go.id/SJKPublic

Companion to scrapers/ojk_banking_spi.py, which only covers the legacy
SharePoint archive (capped at June 2025). This module pulls the equivalent
data straight from the new portal OJK moved to from July 2025 onward.

INVESTIGATION SUMMARY
----------------------
data.ojk.go.id/SJKPublic is NOT a static file archive. It's a server-rendered
ASP.NET Core MVC app (behind an F5/BIG-IP + Akamai-style WAF -- same
full-desktop-browser-User-Agent requirement as the old ojk.go.id page) whose
data grids/pivot tables are powered by DevExtreme widgets:
  - A DevExtreme dxPivotGrid backed by DevExpress.data.XmlaStore, which talks
    to an internal SSAS OLAP cube over XMLA/SOAP at
    POST /SJKPublic/datasource/  (catalog=PORTALSJKCUBE / PORTALSJKCUBE_BU,
    cube=<CubeName>, e.g. "BU_DanaPihakKetiga"). Not used here (SOAP/MDX is
    heavier to replicate) -- see GetGridCSVData below instead.
  - A parallel, much simpler REST endpoint the page uses to build its Excel/
    CSV export, which turns out to be a plain GET-able JSON endpoint that
    returns the *raw fact rows* behind whichever cube/table is showing:

      GET /SJKPublic/Dataset/Dataset/GetGridCSVData
          ?namaTable=<CubeTableName>&metricID=<MetricID>
          &_filter=<field>\\=\\<value>?<field>\\=\\<value>...  (URL-encoded,
              "?"-joined predicates, built from the pivot's active filters --
              in practice this is just used to select one or more `Bulan`
              (month) values, e.g. "Bulan\\=\\2026-06")
          &_field=<comma-joined field names, one per filter predicate>
          &_data=<comma-joined values, one per filter predicate>
          &take=<page size>&skip=<offset>&requireTotalCount=true

    No auth beyond the ASP.NET Core session + WAF cookies picked up from an
    initial GET to the portal home page -- no login, no API key. Confirmed
    working with plain `requests` (see _init_session()/HEADERS below).

  - A companion catalog search API,
      GET /SJKPublic/Dataset/KatalogData/GetGridData?keyword=...
    lists every published "metric" with its `RefMetricID`/`MetricID` and
    (via /SJKPublic/Dataset/Dataset/Dataset/<RefMetricID>) the corresponding
    `namaTable` + `metricID` pair, and the *default* pivot's `Bulan` filter,
    whose highest value is the latest month currently published for that
    table (there is no simple "latest period" endpoint by itself).

TABLE MAPPING (old SPI table number -> new portal source)
-----------------------------------------------------------
  1.33.a  -> BU_DanaPihakKetiga  (metricID 341) -- single latest month,
             aggregated by DATI1 (province) x MataUang x JenisDPK.
  1.34.a  -> BU_DanaPihakKetiga  (metricID 341) -- same cube, several months,
             aggregated by DATI1 per month (time series).
  1.43.a  -> BU_InformasiKantor  (metricID 330) -- PARTIAL. This cube only
             carries a NATIONAL count of head offices ("1. Kantor Pusat") by
             KBMI/KelompokBank; DATI1 is always null. The old workbook's
             *by-location* branch-office growth series is not published on
             the new portal under this metric (see LIMITATIONS).
  2.8     -> BPR_DanaPihakKetiga (metricID 10)  -- BPR Konvensional only
             (BPR Syariah is metricID 23, a separate cube, not pulled here to
             stay 1:1 with the old table, which was BPR Konvensional).
  2.14    -> NOT FOUND. The BPR office-count catalog entries ("Jumlah
             Kantor", KatalogDataID 83/104) have no RefMetricID on the new
             portal (Preview button is disabled server-side, i.e. OJK
             enumerates the metric but does not (yet) expose its dataset).
             See LIMITATIONS.
  3.12.a  -> BU_KreditdanPembiayaanEntitas (metricID 340) -- single latest
             month, aggregated by DATI1 x JenisPenggunaan (purpose) x
             OrientasiPenggunaan (orientation), with NPL = sum of
             KualitasKredit in {Kurang Lancar, Diragukan, Macet}.
  3.13.a  -> BU_KreditdanPembiayaanEntitas (metricID 340) -- same cube,
             several months, total credit + NPL aggregated by DATI1/month.
  3.18.a  -> NOT FOUND. BPR Konvensional's "Total Kredit/Pembiayaan" catalog
             entries (KatalogDataID 73/74/75) also have no RefMetricID.
             Guessed table names (BPR_Kredit, BPR_KreditPembiayaan, ...)
             against GetGridCSVData all 404'd. See LIMITATIONS.
  3.22.a  -> BU_KreditdanPembiayaanEntitas (metricID 340) -- same cube,
             several months, credit aggregated by DATI1/month restricted to
             KategoriUsaha in {Usaha Mikro, Usaha Kecil, Usaha Menengah}
             (UMKM), which is a *finer* location-of-project breakdown than
             the old table numerically (this is bank-location, like the
             other tables here, not "project location" -- see LIMITATIONS).

So: 6 of 9 tables (1.33.a, 1.34.a, 2.8, 3.12.a, 3.13.a, 3.22.a) are fully
reproduced with CURRENT data. 3 tables (1.43.a partial/national-only, 2.14,
3.18.a) are not available through this portal's public API at all (BPR
office/credit and per-location branch-office detail appear to simply not be
published there yet) -- these are real portal gaps, not scraper bugs.

DATA FRESHNESS
---------------
Confirmed at run time (see find_latest_month()): BU_DanaPihakKetiga and
BU_KreditdanPembiayaanEntitas both had data through **2026-06**;
BPR_DanaPihakKetiga through **2026-07**. All comfortably newer than the old
portal's June-2025 ceiling.

UNITS
------
Raw `Nilai`/`TotalDana`/`TotalTabungan`/`TotalDeposito` values from the API
are full Rupiah. The legacy workbook (see ojk_banking_spi.py samples) is in
Rp miliar (billions). To keep the two vintages roughly comparable, this
module divides raw values by 1e9 before writing them out. This is a scale
assumption (not confirmed against an OJK unit label) -- treat absolute
magnitudes as approximate; the up-to-date *coverage* is the point of this
scraper, not penny-for-penny reconciliation with the old workbook.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass

import pandas as pd
import requests

BASE = "https://data.ojk.go.id"
PORTAL = f"{BASE}/SJKPublic"
HOME_URL = f"{PORTAL}/"
GRID_CSV_URL = f"{PORTAL}/Dataset/Dataset/GetGridCSVData"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}
XHR_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

RUPIAH_TO_MILIAR = 1_000_000_000  # unit assumption, see module docstring

NPL_QUALITY_PREFIXES = ("003.", "004.", "005.")  # Kurang Lancar/Diragukan/Macet
UMKM_CATEGORY_PREFIXES = ("001.", "002.", "003.")  # Kecil/Menengah/Mikro (004 = non-UMKM)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get(HOME_URL, timeout=30)
    r.raise_for_status()
    return s


def _build_filter(pairs: list[tuple[str, str]]) -> tuple[str, str, str]:
    """Reproduce the page's getFilter() encoding: N (field,value) predicates,
    OR'd with '?' when repeated on the same field, into the three params the
    GetGridCSVData endpoint expects."""
    listFilter = "?".join(f"{f}\\=\\{v}" for f, v in pairs)
    listField = ",".join(f for f, _ in pairs)
    listData = ",".join(v for _, v in pairs)
    return listFilter, listField, listData


def get_grid_csv_page(session: requests.Session, nama_table: str, metric_id: int,
                       filter_pairs: list[tuple[str, str]], skip: int = 0,
                       take: int = 10000, require_total: bool = False) -> dict:
    listFilter, listField, listData = _build_filter(filter_pairs)
    params = {
        "_filter": listFilter,
        "_field": listField,
        "_data": listData,
        "namaTable": nama_table,
        "metricID": metric_id,
        "take": take,
        "skip": skip,
    }
    if require_total:
        params["requireTotalCount"] = "true"
    r = session.get(GRID_CSV_URL, params=params, headers=XHR_HEADERS, timeout=60)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "")
    if "json" not in ct:
        raise RuntimeError(
            f"GetGridCSVData returned non-JSON (namaTable={nama_table}, "
            f"metricID={metric_id}) -- likely an invalid table name/error page"
        )
    return r.json()


def count_for_month(session: requests.Session, nama_table: str, metric_id: int, month: str) -> int:
    data = get_grid_csv_page(session, nama_table, metric_id, [("Bulan", month)],
                              skip=0, take=1, require_total=True)
    return data.get("totalCount", 0)


def find_latest_month(session: requests.Session, nama_table: str, metric_id: int,
                       lookback_months: int = 14) -> str | None:
    """Probe months back from today until one returns data. The portal's own
    pivot defaults show a similar sliding window, but there's no direct
    'latest period' endpoint, so we probe."""
    today = dt.date.today().replace(day=1)
    for i in range(lookback_months):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        month = f"{y:04d}-{m:02d}"
        if count_for_month(session, nama_table, metric_id, month) > 0:
            return month
    return None


def recent_months(latest: str, n: int) -> list[str]:
    y, m = (int(x) for x in latest.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def fetch_all_rows(session: requests.Session, nama_table: str, metric_id: int,
                    months: list[str], page_size: int = 20000) -> list[dict]:
    filter_pairs = [("Bulan", m) for m in months]
    first = get_grid_csv_page(session, nama_table, metric_id, filter_pairs,
                               skip=0, take=page_size, require_total=True)
    rows = list(first.get("data", []))
    total = first.get("totalCount", len(rows))
    print(f"    fetching {nama_table} {months}: {len(rows)}/{total} rows", flush=True)
    skip = len(rows)
    while skip < total:
        page = get_grid_csv_page(session, nama_table, metric_id, filter_pairs,
                                  skip=skip, take=page_size)
        page_rows = page.get("data", [])
        if not page_rows:
            break
        rows.extend(page_rows)
        skip += len(page_rows)
        print(f"    fetching {nama_table} {months}: {skip}/{total} rows", flush=True)
    return rows


# --------------------------------------------------------------------------
# Per-table extraction (returns list[dict], same "location" + value column
# shape as the legacy scraper's rows, values scaled to Rp miliar)
# --------------------------------------------------------------------------

def extract_1_33_a_from_df(df: pd.DataFrame, latest_month: str) -> list[dict]:
    df = df[df["Bulan"] == latest_month]
    df = df[df["DATI1"].notna()].copy()
    df["DATI1"] = df["DATI1"].str.strip()
    df["Nilai"] = df["Nilai"].astype(float) / RUPIAH_TO_MILIAR
    jenis_map = {"1. Giro": "Giro", "2. Tabungan": "Tabungan", "3. Deposito": "Deposito"}
    mata_map = {"001. Rupiah": "Rupiah", "002. Valas": "Valas"}
    df["Jenis"] = df["JenisDPK"].map(jenis_map)
    df["Mata"] = df["MataUang"].map(mata_map)
    piv = df.groupby(["DATI1", "Jenis", "Mata"])["Nilai"].sum().unstack(["Jenis", "Mata"], fill_value=0.0)
    out = []
    grand_total = 0.0
    per_loc_total = {}
    for loc in piv.index:
        row = {"location": loc}
        total_rp = total_va = 0.0
        for jenis in ("Giro", "Tabungan", "Deposito"):
            for mata, suffix in (("Rupiah", "Rupiah"), ("Valas", "Valas")):
                v = float(piv.loc[loc].get((jenis, mata), 0.0))
                row[f"{jenis}_{suffix}"] = v
                if mata == "Rupiah":
                    total_rp += v
                else:
                    total_va += v
        row["TotalDPK_Rupiah"] = total_rp
        row["TotalDPK_Valas"] = total_va
        row["TotalDPK_Total"] = total_rp + total_va
        per_loc_total[loc] = total_rp + total_va
        grand_total += total_rp + total_va
        out.append(row)
    for row in out:
        row["Pangsa_Pct"] = (per_loc_total[row["location"]] / grand_total * 100.0) if grand_total else None
    return out


def extract_1_34_a_from_df(df: pd.DataFrame, months: list[str]) -> list[dict]:
    df = df[df["DATI1"].notna()].copy()
    df["DATI1"] = df["DATI1"].str.strip()
    df["Nilai"] = df["Nilai"].astype(float) / RUPIAH_TO_MILIAR
    piv = df.groupby(["DATI1", "Bulan"])["Nilai"].sum().unstack("Bulan", fill_value=0.0)
    piv = piv.reindex(columns=months, fill_value=0.0)
    out = []
    for loc in piv.index:
        series = [float(v) for v in piv.loc[loc].tolist()]
        out.append({"location": loc, "latest_value": series[-1] if series else None, "series": series})
    return out


def extract_2_8(session: requests.Session, latest_month: str) -> list[dict]:
    rows = fetch_all_rows(session, "BPR_DanaPihakKetiga", 10, [latest_month])
    df = pd.DataFrame(rows)
    df = df[df["DATI1"].notna()].copy()
    df["DATI1"] = df["DATI1"].str.strip()
    for col in ("TotalTabungan", "TotalDeposito"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0) / RUPIAH_TO_MILIAR
    g = df.groupby("DATI1")[["TotalTabungan", "TotalDeposito"]].sum()
    g["TotalDPK"] = g["TotalTabungan"] + g["TotalDeposito"]
    grand_total = g["TotalDPK"].sum()
    out = []
    for loc, r in g.iterrows():
        out.append({
            "location": loc,
            "Tabungan": float(r["TotalTabungan"]),
            "Deposito": float(r["TotalDeposito"]),
            "TotalDPK": float(r["TotalDPK"]),
            "Pangsa_Pct": (float(r["TotalDPK"]) / grand_total * 100.0) if grand_total else None,
        })
    return out


def extract_3_12_a_from_df(df: pd.DataFrame, latest_month: str) -> list[dict]:
    df = df[df["Bulan"] == latest_month]
    df = df[df["DATI1"].notna()].copy()
    df["DATI1"] = df["DATI1"].str.strip()
    df["Nilai"] = df["Nilai"].astype(float) / RUPIAH_TO_MILIAR
    df["is_npl"] = df["KualitasKredit"].astype(str).str.startswith(NPL_QUALITY_PREFIXES)
    purpose_map = {"001.Modal Kerja": "ModalKerja", "002.Investasi": "Investasi", "003.Konsumsi": "Konsumsi"}
    orient_map = {"001. Ekspor": "Ekspor", "002. Impor": "Impor", "003. Lainnya": "Lainnya"}
    df["Purpose"] = df["JenisPenggunaan"].map(purpose_map)
    df["Orient"] = df["OrientasiPenggunaan"].map(orient_map)

    by_loc = {}
    for loc, sub in df.groupby("DATI1"):
        row = {"location": loc}
        for col_name, mask in (
            ("ModalKerja", sub["Purpose"] == "ModalKerja"),
            ("Investasi", sub["Purpose"] == "Investasi"),
            ("Konsumsi", sub["Purpose"] == "Konsumsi"),
            ("Ekspor", sub["Orient"] == "Ekspor"),
            ("Impor", sub["Orient"] == "Impor"),
            ("Lainnya", sub["Orient"] == "Lainnya"),
        ):
            row[col_name] = float(sub.loc[mask, "Nilai"].sum())
            row[f"{col_name}_NPL"] = float(sub.loc[mask & sub["is_npl"], "Nilai"].sum())
        by_loc[loc] = row
    return list(by_loc.values())


def extract_3_13_a_from_df(df: pd.DataFrame, months: list[str]) -> list[dict]:
    df = df[df["DATI1"].notna()].copy()
    df["DATI1"] = df["DATI1"].str.strip()
    df["Nilai"] = df["Nilai"].astype(float) / RUPIAH_TO_MILIAR
    df["is_npl"] = df["KualitasKredit"].astype(str).str.startswith(NPL_QUALITY_PREFIXES)

    total_piv = df.groupby(["DATI1", "Bulan"])["Nilai"].sum().unstack("Bulan", fill_value=0.0).reindex(columns=months, fill_value=0.0)
    npl_piv = df[df["is_npl"]].groupby(["DATI1", "Bulan"])["Nilai"].sum().unstack("Bulan", fill_value=0.0).reindex(columns=months, fill_value=0.0)
    npl_piv = npl_piv.reindex(index=total_piv.index, fill_value=0.0)

    out = []
    for loc in total_piv.index:
        series = [float(v) for v in total_piv.loc[loc].tolist()]
        npl_series = [float(v) for v in npl_piv.loc[loc].tolist()]
        out.append({
            "location": loc,
            "latest_value": series[-1] if series else None,
            "series": series,
            "latest_npl": npl_series[-1] if npl_series else None,
            "npl_series": npl_series,
        })
    return out


def extract_3_22_a_from_df(df: pd.DataFrame, months: list[str]) -> list[dict]:
    df = df[df["DATI1"].notna()].copy()
    df["DATI1"] = df["DATI1"].str.strip()
    df["Nilai"] = df["Nilai"].astype(float) / RUPIAH_TO_MILIAR
    df["is_umkm"] = df["KategoriUsaha"].astype(str).str.startswith(UMKM_CATEGORY_PREFIXES)
    df = df[df["is_umkm"]]

    piv = df.groupby(["DATI1", "Bulan"])["Nilai"].sum().unstack("Bulan", fill_value=0.0).reindex(columns=months, fill_value=0.0)
    out = []
    for loc in piv.index:
        series = [float(v) for v in piv.loc[loc].tolist()]
        out.append({"location": loc, "latest_value": series[-1] if series else None, "series": series})
    return out


def extract_1_43_a_national_from_df(df: pd.DataFrame, months: list[str]) -> list[dict]:
    """PARTIAL / adapted: BU_InformasiKantor on the new portal only carries a
    NATIONAL head-office count by KelompokBank, not a by-location branch
    series like the old table. Returned with location='NASIONAL - <group>' so
    callers/consumers can immediately see this is not the same granularity."""
    df = df.copy()
    df["Nilai"] = pd.to_numeric(df["Nilai"], errors="coerce").fillna(0)
    piv = df.groupby(["KelompokBank", "Bulan"])["Nilai"].sum().unstack("Bulan", fill_value=0).reindex(columns=months, fill_value=0)
    out = []
    for grp in piv.index:
        series = [float(v) for v in piv.loc[grp].tolist()]
        out.append({"location": f"NASIONAL - {grp}", "latest_value": series[-1] if series else None, "series": series})
    return out


# How many months of history to pull per source cube. DPK is cheap (~7.7k
# rows/month) so we can afford a longer series; the credit cube is very
# heavy (~59k rows/month, one row per dimension combo) so it's kept short to
# stay within a reasonable runtime against OJK's rate-limited WAF.
DPK_SERIES_MONTHS = 6
KREDIT_SERIES_MONTHS = 2
KANTOR_SERIES_MONTHS = 6

UNAVAILABLE_TABLES = {
    "2.14": "BPR office count (KP/KC/KPK) by location -- no RefMetricID/dataset "
            "exposed on the new portal (KatalogDataID 83/104 have Preview disabled).",
    "3.18.a": "BPR credit + NPL time series by location -- no RefMetricID/dataset "
              "exposed on the new portal (KatalogDataID 73/74/75 have Preview "
              "disabled); guessed GetGridCSVData table names all failed.",
}

def save_table_csv(rows: list[dict], out_path: str):
    if not rows:
        return
    fields = sorted({k for row in rows for k in row.keys()}, key=lambda k: (k not in ("location",), k))
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


@dataclass
class RunResult:
    latest_month_by_cube: dict
    results: dict


def run() -> RunResult:
    session = _session()
    print("Session established, probing latest available month per source cube...", flush=True)

    dpk_latest = find_latest_month(session, "BU_DanaPihakKetiga", 341)
    kredit_latest = find_latest_month(session, "BU_KreditdanPembiayaanEntitas", 340)
    kantor_latest = find_latest_month(session, "BU_InformasiKantor", 330)
    bpr_dpk_latest = find_latest_month(session, "BPR_DanaPihakKetiga", 10)
    latest_month_by_cube = {
        ("BU_DanaPihakKetiga", 341): dpk_latest,
        ("BU_KreditdanPembiayaanEntitas", 340): kredit_latest,
        ("BU_InformasiKantor", 330): kantor_latest,
        ("BPR_DanaPihakKetiga", 10): bpr_dpk_latest,
    }
    for (nama_table, metric_id), month in latest_month_by_cube.items():
        print(f"  {nama_table} (metricID={metric_id}): latest month = {month}", flush=True)

    results = {}

    # --- DPK cube (BU_DanaPihakKetiga) -- feeds 1.33.a (latest month) and
    # 1.34.a (short time series); fetched ONCE and shared.
    if dpk_latest:
        dpk_months = recent_months(dpk_latest, DPK_SERIES_MONTHS)
        print(f"Fetching DPK cube for months {dpk_months} ...", flush=True)
        dpk_rows = fetch_all_rows(session, "BU_DanaPihakKetiga", 341, dpk_months)
        dpk_df = pd.DataFrame(dpk_rows)
        results["1.33.a"] = {
            "rows": extract_1_33_a_from_df(dpk_df, dpk_latest),
            "latest_month": dpk_latest, "source": "BU_DanaPihakKetiga (metricID=341)", "partial": False,
        }
        results["1.34.a"] = {
            "rows": extract_1_34_a_from_df(dpk_df, dpk_months),
            "latest_month": dpk_latest, "source": "BU_DanaPihakKetiga (metricID=341)", "partial": False,
        }
    else:
        results["1.33.a"] = {"error": "no data found for BU_DanaPihakKetiga/341"}
        results["1.34.a"] = {"error": "no data found for BU_DanaPihakKetiga/341"}

    # --- BPR DPK cube -- feeds 2.8 (latest month only, cheap: ~74 rows).
    if bpr_dpk_latest:
        results["2.8"] = {
            "rows": extract_2_8(session, bpr_dpk_latest),
            "latest_month": bpr_dpk_latest, "source": "BPR_DanaPihakKetiga (metricID=10)", "partial": False,
        }
    else:
        results["2.8"] = {"error": "no data found for BPR_DanaPihakKetiga/10"}

    # --- BU_InformasiKantor -- feeds 1.43.a (PARTIAL: national only, no location).
    if kantor_latest:
        kantor_months = recent_months(kantor_latest, KANTOR_SERIES_MONTHS)
        print(f"Fetching BU_InformasiKantor cube for months {kantor_months} ...", flush=True)
        kantor_rows = fetch_all_rows(session, "BU_InformasiKantor", 330, kantor_months)
        kantor_df = pd.DataFrame(kantor_rows)
        results["1.43.a"] = {
            "rows": extract_1_43_a_national_from_df(kantor_df, kantor_months),
            "latest_month": kantor_latest, "source": "BU_InformasiKantor (metricID=330)", "partial": True,
        }
    else:
        results["1.43.a"] = {"error": "no data found for BU_InformasiKantor/330"}

    # --- Kredit cube (BU_KreditdanPembiayaanEntitas) -- feeds 3.12.a (latest
    # month), 3.13.a and 3.22.a (short time series); fetched ONCE and shared.
    # This is the heaviest cube (~59k rows/month across every dimension
    # combo), so the series is kept short (KREDIT_SERIES_MONTHS).
    if kredit_latest:
        kredit_months = recent_months(kredit_latest, KREDIT_SERIES_MONTHS)
        print(f"Fetching Kredit cube for months {kredit_months} (heaviest cube, ~59k rows/month) ...", flush=True)
        kredit_rows = fetch_all_rows(session, "BU_KreditdanPembiayaanEntitas", 340, kredit_months)
        kredit_df = pd.DataFrame(kredit_rows)
        results["3.12.a"] = {
            "rows": extract_3_12_a_from_df(kredit_df, kredit_latest),
            "latest_month": kredit_latest, "source": "BU_KreditdanPembiayaanEntitas (metricID=340)", "partial": False,
        }
        results["3.13.a"] = {
            "rows": extract_3_13_a_from_df(kredit_df, kredit_months),
            "latest_month": kredit_latest, "source": "BU_KreditdanPembiayaanEntitas (metricID=340)", "partial": False,
        }
        results["3.22.a"] = {
            "rows": extract_3_22_a_from_df(kredit_df, kredit_months),
            "latest_month": kredit_latest, "source": "BU_KreditdanPembiayaanEntitas (metricID=340)", "partial": False,
        }
    else:
        for t in ("3.12.a", "3.13.a", "3.22.a"):
            results[t] = {"error": "no data found for BU_KreditdanPembiayaanEntitas/340"}

    for table_no, reason in UNAVAILABLE_TABLES.items():
        results[table_no] = {"error": reason}
    return RunResult(latest_month_by_cube=latest_month_by_cube, results=results)


