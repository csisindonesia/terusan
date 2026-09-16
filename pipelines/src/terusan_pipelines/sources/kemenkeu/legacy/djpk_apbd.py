# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: Regional government budget realization (APBD) - Ministry of Finance
          Directorate General of Fiscal Balance (DJPK) SIKD portal
Source page: https://djpk.kemenkeu.go.id/portal/data/apbd
Covers indicators (Catalogue_Regional, 13 rows): Fiscal balance, Regional
Revenue and its 3 sub-categories (PAD, TKDD, Other Revenue), Regional
Expenditure and its 4 sub-categories (Personnel, Goods & Services, Capital,
Other), Regional Financing and its 2 sub-categories (Receipts, Expenditures).
Frequency: monthly (cumulative realization within the fiscal year).

Endpoint (confirmed working via WebFetch on 2026-09-07 - returned real data
for periode=9, tahun=2026, national aggregate):

    GET https://djpk.kemenkeu.go.id/portal/csv_apbd

Query params (from the filter form on the source page):
    type      - fixed value "apbd"
    periode   - month number 1-12 (e.g. 9 = September; cumulative realization
                Jan-Sep)
    tahun     - fiscal year, site supports 2011-2027
    provinsi  - province filter; "--" = all provinces (national aggregate).
                Otherwise a zero-padded 2-digit code, e.g. "01" = Aceh,
                "09" = DKI Jakarta, "13" = Jawa Timur. Confirmed by fetching
                https://djpk.kemenkeu.go.id/portal/data/apbd directly with
                Python `requests` (this page IS reachable with plain
                requests.get(), unlike the earlier belief that only the CSV
                endpoint was) and reading the `<select name="provinsi"
                id="sel_provinsi">` dropdown's `<option value="..">` pairs
                verbatim out of the HTML. There are 38 options (codes 01-38):
                the 34 standard BPS provinces plus the 4 newer Papua splits
                (Papua Selatan, Papua Tengah, Papua Pegunungan, Papua Barat
                Daya) that DJPK's own scheme assigns codes 35-38 to (NOT BPS
                codes - this is DJPK's own sequential numbering, see
                PROVINCES below). Confirmed live: passing a real provinsi
                code returns province-specific totals that differ from both
                each other and the national aggregate (e.g. periode=9,
                tahun=2026 Regional Revenue realisasi: Aceh ~22.6T, DKI
                Jakarta ~39.3T, Jawa Timur ~73.0T, vs national ~679.4T).
    pemda     - sub-region (kabupaten/kota) filter; "--" = all pemda within
                the selected provinsi. Not enumerated here (much larger
                fanout, ~500+ regions) - per-province is the priority
                granularity for this catalogue.

Despite the .xls-looking response (and a filename suggesting CSV), the
endpoint actually returns Microsoft "SpreadsheetML" (Excel 2003 XML) with
media type application/vnd.ms-excel - i.e. XML wrapping a single worksheet
named "Data APBD" with a flat table: Akun (account/line item name),
Anggaran (budget), Realisasi (realized), Persentase (realization %).

The Akun column encodes a two-level hierarchy implicitly through row order
(a parent total row immediately followed by its child rows), e.g.:
    Pendapatan Daerah          <- total regional revenue
      PAD                      <- own-source revenue (also has children)
        Pajak Daerah
        Retribusi Daerah
        ...
      TKDD                     <- transfers to regions & village funds
        Pendapatan Transfer Pemerintah Pusat
      Pendapatan Lainnya       <- other revenue
        Pendapatan Hibah
        ...
    Belanja Daerah             <- total regional expenditure
      Belanja Pegawai          <- personnel (row is duplicated: header +
                                   its single child, both with the same
                                   values - harmless, first occurrence wins)
      Belanja Barang dan Jasa  <- goods & services (same duplication)
      Belanja Modal            <- capital (same duplication)
      Belanja Lainnya          <- other expenditure
        Belanja Bagi Hasil
        ...
    Pembiayaan Daerah          <- total regional financing
      Penerimaan Pembiayaan Daerah  <- financing receipts
        ...
      Pengeluaran Pembiayaan Daerah <- financing expenditures
        ...

This scraper only needs the 12 named top-level/second-level totals above; it
does not need the deeper sub-sub-item breakdown. "Fiscal balance" is derived
as Pendapatan Daerah - Belanja Daerah (computed separately for the budgeted
and realized columns).

No authentication required. Numbers in the XML are given in IDR (rupiah),
some in scientific notation for large values (e.g. "1.1925366672203E+15").

NETWORK NOTE: direct HTTP(S) connections from this development sandbox to
djpk.kemenkeu.go.id fail at the TCP/TLS level (connection times out - looks
like a geo/IP block on this specific host). The endpoint above was only
reachable and verified via an out-of-band fetch tool from a different
network path; a plain `requests.get()` run from this sandbox will time out.
This script is written to run correctly from a network that CAN reach the
host (e.g. from Indonesia, or the user's own machine/CI runner).
"""

import csv
import re
import time
from xml.etree import ElementTree as ET

import requests

BASE_URL = "https://djpk.kemenkeu.go.id/portal/csv_apbd"
PORTAL_PAGE_URL = "https://djpk.kemenkeu.go.id/portal/data/apbd"
HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_TIMEOUT = 30

SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"

# Province code -> name, scraped verbatim from the `<select name="provinsi"
# id="sel_provinsi">` dropdown on https://djpk.kemenkeu.go.id/portal/data/apbd
# (fetched directly with `requests.get()` - this HTML page is reachable from
# this sandbox even though a plain `curl` to the host is not; see module
# docstring). Codes are DJPK's own zero-padded 2-digit scheme, NOT BPS codes -
# they happen to match BPS ordering/values for codes 01-33 (Aceh..Sulawesi
# Barat) and continue past the classic 34th province (34 = Kalimantan Utara)
# with the 4 newer Papua splits at 35-38, which BPS numbers differently.
PROVINCES = {
    "01": "Aceh",
    "02": "Sumatera Utara",
    "03": "Sumatera Barat",
    "04": "Riau",
    "05": "Jambi",
    "06": "Sumatera Selatan",
    "07": "Bengkulu",
    "08": "Lampung",
    "09": "DKI Jakarta",
    "10": "Jawa Barat",
    "11": "Jawa Tengah",
    "12": "DI Yogyakarta",
    "13": "Jawa Timur",
    "14": "Kalimantan Barat",
    "15": "Kalimantan Tengah",
    "16": "Kalimantan Selatan",
    "17": "Kalimantan Timur",
    "18": "Sulawesi Utara",
    "19": "Sulawesi Tengah",
    "20": "Sulawesi Selatan",
    "21": "Sulawesi Tenggara",
    "22": "Bali",
    "23": "Nusa Tenggara Barat",
    "24": "Nusa Tenggara Timur",
    "25": "Maluku",
    "26": "Papua",
    "27": "Maluku Utara",
    "28": "Banten",
    "29": "Bangka Belitung",
    "30": "Gorontalo",
    "31": "Kepulauan Riau",
    "32": "Papua Barat",
    "33": "Sulawesi Barat",
    "34": "Kalimantan Utara",
    "35": "Papua Selatan",
    "36": "Papua Tengah",
    "37": "Papua Pegunungan",
    "38": "Papua Barat Daya",
}

# Akun (account) name -> Catalogue_Regional indicator label.
# This is the CURRENT budget classification scheme (Permendagri 13/2016
# "new APBD structure"), in effect for fiscal year 2016 onward.
INDICATOR_MAP = {
    "Pendapatan Daerah": "Regional Revenue",
    "PAD": "Regional Revenue - Own-Source Revenue (PAD)",
    "TKDD": "Regional Revenue - Transfers to Regions and Village Funds (TKDD)",
    "Pendapatan Lainnya": "Regional Revenue - Other Revenue",
    "Belanja Daerah": "Regional Expenditure",
    "Belanja Pegawai": "Regional Expenditure - Personnel Expenditure",
    "Belanja Barang dan Jasa": "Regional Expenditure - Goods and Services Expenditure",
    "Belanja Modal": "Regional Expenditure - Capital Expenditure",
    "Belanja Lainnya": "Regional Expenditure - Other Expenditure",
    "Pembiayaan Daerah": "Regional Financing",
    "Penerimaan Pembiayaan Daerah": "Regional Financing - Financing Receipts",
    "Pengeluaran Pembiayaan Daerah": "Regional Financing - Financing Expenditures",
}

# LEGACY budget classification scheme (pre-Permendagri 13/2016), in effect for
# fiscal years 2011-2015 in this endpoint's data (confirmed by direct probing:
# tahun=2013/2014/2015 all use these Akun names; tahun=2016 onward switches to
# INDICATOR_MAP above). Top-level Akun names differ ("Pendapatan" not
# "Pendapatan Daerah", etc.), and the old scheme classifies expenditure by
# Belanja Tidak Langsung / Belanja Langsung (indirect/direct) rather than by
# Personnel/Goods&Services/Capital/Other directly, so those 2 catalogue
# indicators require summing multiple legacy line items rather than a plain
# rename - see extract_indicators_legacy().
#
# Direct 1:1 renames:
LEGACY_INDICATOR_MAP = {
    "Pendapatan": "Regional Revenue",
    "PAD": "Regional Revenue - Own-Source Revenue (PAD)",
    "Pendapatan Lainnya": "Regional Revenue - Other Revenue",
    "Belanja": "Regional Expenditure",
    "Belanja Barang Jasa": "Regional Expenditure - Goods and Services Expenditure",
    "Belanja Modal": "Regional Expenditure - Capital Expenditure",
    "Pembiayaan": "Regional Financing",
    "Penerimaan Pembiayaan": "Regional Financing - Financing Receipts",
    "Pengeluaran Pembiayaan": "Regional Financing - Financing Expenditures",
}
# APPROXIMATION, not a rename: the legacy scheme has no line item equivalent
# to "TKDD" (Transfer ke Daerah dan Dana Desa) - that concept, including the
# village fund (Dana Desa), was only formalized in DJPK's reporting from 2016
# onward. "Dana Perimbangan" (DBH+DAU+DAK, the pre-2016 fiscal balancing
# transfer) is the closest available predecessor and is used as a proxy for
# years using the legacy scheme, but it under-counts TKDD for any year where
# Dana Desa was material (Dana Desa existed nationally from 2015 but DJPK's
# regional reports here don't break it out as its own line pre-2016 - it's
# folded into other legacy categories in a way that can't be cleanly
# isolated). Rows using this proxy are flagged with note="approximated" in
# extract_indicators_legacy() output.
LEGACY_TKDD_PROXY_AKUN = "Dana Perimbangan"
# Legacy Akun lines that sum into "Regional Expenditure - Personnel
# Expenditure" (personnel costs were split across the indirect and direct
# expenditure blocks in the old scheme).
LEGACY_PERSONNEL_AKUN = ["Belanja Pegawai Tidak Langsung", "Belanja Pegawai Langsung"]
# Legacy Akun lines that sum into "Regional Expenditure - Other Expenditure"
# (everything else in the indirect-expenditure block once personnel and
# interest/subsidy/grant/etc. are accounted for individually - the legacy
# scheme has no single "Belanja Lainnya" catch-all line like the new scheme).
LEGACY_OTHER_EXPENDITURE_AKUN = [
    "Belanja Bunga",
    "Belanja Subsidi",
    "Belanja Hibah",
    "Belanja Bantuan Sosial",
    "Belanja Bagi Hasil",
    "Belanja Bantuan Keuangan",
    "Belanja Tidak Terduga",
]


def _to_number(raw: str):
    """Convert a numeric-looking string (possibly scientific notation, e.g.
    '1.1925366672203E+15') to a float. Returns None if not parseable."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def fetch_raw(periode: int, tahun: int, provinsi: str = "--", pemda: str = "--") -> bytes:
    """Fetch the raw SpreadsheetML (Excel 2003 XML) APBD export for a given
    month (periode, 1-12) and fiscal year (tahun). provinsi/pemda default to
    "--" which returns the national aggregate across all regions."""
    params = {
        "type": "apbd",
        "periode": periode,
        "tahun": tahun,
        "provinsi": provinsi,
        "pemda": pemda,
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def parse_rows(xml_bytes: bytes):
    """Parse the SpreadsheetML content into a list of dicts, one per Akun
    line item, with keys: Akun, Anggaran, Realisasi, Persentase."""
    text = xml_bytes.decode("utf-8", errors="replace")
    root = ET.fromstring(text)

    header = None
    records = []
    for row in root.iter(f"{{{SS_NS}}}Row"):
        cells = [d.text for d in row.iter(f"{{{SS_NS}}}Data")]
        if header is None:
            header = cells
            continue
        if not cells:
            continue
        record = dict(zip(header, cells))
        if "Akun" in record and record["Akun"] is not None:
            record["Akun"] = re.sub(r"\s+", " ", record["Akun"]).strip()
        records.append(record)
    return records


def _collect_by_akun(records, akun_names):
    """First-occurrence-wins lookup of Anggaran/Realisasi for each Akun name
    in `akun_names`, scanning `records` in document order. Used because both
    schemes duplicate some Akun names (a parent total row immediately
    followed by a same-named child row with identical values)."""
    by_akun = {}
    for rec in records:
        akun = rec.get("Akun")
        if akun in akun_names and akun not in by_akun:
            by_akun[akun] = {
                "anggaran": _to_number(rec.get("Anggaran")),
                "realisasi": _to_number(rec.get("Realisasi")),
            }
    return by_akun


def _sum_vals(*vals_list):
    """Sum a list of {"anggaran", "realisasi"} dicts (skipping missing ones).
    Returns None for a field if every input for that field is None."""
    present = [v for v in vals_list if v is not None]
    if not present:
        return None
    out = {}
    for field in ("anggaran", "realisasi"):
        nums = [v[field] for v in present if v.get(field) is not None]
        out[field] = sum(nums) if nums else None
    return out


def is_legacy_scheme(records) -> bool:
    """True if `records` uses the pre-2016 Akun naming (fiscal years
    2011-2015 on this endpoint): top-level revenue/expenditure/financing rows
    are named "Pendapatan"/"Belanja"/"Pembiayaan" instead of "...Daerah"."""
    akuns = {rec.get("Akun") for rec in records}
    return "Pendapatan Daerah" not in akuns and "Pendapatan" in akuns


def extract_indicators_current(records):
    """Extraction for the current (2016+) scheme - plain renames per
    INDICATOR_MAP plus a derived Fiscal balance."""
    by_akun = _collect_by_akun(records, set(INDICATOR_MAP))

    indicators = {}
    for akun, label in INDICATOR_MAP.items():
        if akun in by_akun:
            indicators[label] = by_akun[akun]

    pendapatan = by_akun.get("Pendapatan Daerah")
    belanja = by_akun.get("Belanja Daerah")
    if pendapatan and belanja:
        indicators["Fiscal balance"] = {
            "anggaran": pendapatan["anggaran"] - belanja["anggaran"]
            if pendapatan["anggaran"] is not None and belanja["anggaran"] is not None
            else None,
            "realisasi": pendapatan["realisasi"] - belanja["realisasi"]
            if pendapatan["realisasi"] is not None and belanja["realisasi"] is not None
            else None,
        }
    return indicators


def extract_indicators_legacy(records):
    """Extraction for the legacy (pre-2016) scheme. Most indicators are plain
    renames (LEGACY_INDICATOR_MAP); TKDD is approximated from "Dana
    Perimbangan" (flagged with a "note" key); Personnel and Other Expenditure
    are summed from multiple legacy line items since the old scheme doesn't
    report them as single lines. Returns the same shape as
    extract_indicators_current() (label -> {"anggaran", "realisasi"}), plus
    an optional "note" key on approximated entries."""
    wanted = set(LEGACY_INDICATOR_MAP) | {LEGACY_TKDD_PROXY_AKUN} | set(LEGACY_PERSONNEL_AKUN) | set(
        LEGACY_OTHER_EXPENDITURE_AKUN
    )
    by_akun = _collect_by_akun(records, wanted)

    indicators = {}
    for akun, label in LEGACY_INDICATOR_MAP.items():
        if akun in by_akun:
            indicators[label] = by_akun[akun]

    if LEGACY_TKDD_PROXY_AKUN in by_akun:
        vals = dict(by_akun[LEGACY_TKDD_PROXY_AKUN])
        vals["note"] = "approximated from 'Dana Perimbangan' (legacy scheme has no TKDD/Dana Desa line)"
        indicators["Regional Revenue - Transfers to Regions and Village Funds (TKDD)"] = vals

    personnel = _sum_vals(*(by_akun.get(a) for a in LEGACY_PERSONNEL_AKUN))
    if personnel is not None:
        indicators["Regional Expenditure - Personnel Expenditure"] = personnel

    other_exp = _sum_vals(*(by_akun.get(a) for a in LEGACY_OTHER_EXPENDITURE_AKUN))
    if other_exp is not None:
        indicators["Regional Expenditure - Other Expenditure"] = other_exp

    pendapatan = by_akun.get("Pendapatan")
    belanja = by_akun.get("Belanja")
    if pendapatan and belanja:
        indicators["Fiscal balance"] = {
            "anggaran": pendapatan["anggaran"] - belanja["anggaran"]
            if pendapatan["anggaran"] is not None and belanja["anggaran"] is not None
            else None,
            "realisasi": pendapatan["realisasi"] - belanja["realisasi"]
            if pendapatan["realisasi"] is not None and belanja["realisasi"] is not None
            else None,
        }
    return indicators


def extract_indicators(records):
    """Reduce the full Akun list down to the 13 catalogue indicators
    (12 named + derived Fiscal balance), auto-detecting whether `records`
    uses the legacy (pre-2016) or current (2016+) Akun naming scheme."""
    if is_legacy_scheme(records):
        return extract_indicators_legacy(records)
    return extract_indicators_current(records)


def fetch_indicators(periode: int, tahun: int, provinsi: str = "--", pemda: str = "--"):
    """Convenience wrapper: fetch + parse + extract in one call."""
    raw = fetch_raw(periode, tahun, provinsi, pemda)
    records = parse_rows(raw)
    return extract_indicators(records)


def fetch_all_provinces(periode: int, tahun: int, provinces: dict = None, delay: float = 0.4,
                         on_progress=None):
    """Loop fetch_indicators() over every province in `provinces` (defaults to
    the full PROVINCES dict, 38 entries) for a given month/year. Sleeps
    `delay` seconds between requests to avoid hammering the government
    server. Returns a dict keyed by province code ->
    {"provinsi_name": str, "indicators": {label: {"anggaran", "realisasi"}}}.

    `on_progress(code, name, ok, error)` is called after each province if
    given, useful for CLI progress printing.
    """
    if provinces is None:
        provinces = PROVINCES

    results = {}
    codes = list(provinces.items())
    for i, (code, name) in enumerate(codes):
        error = None
        try:
            indicators = fetch_indicators(periode, tahun, provinsi=code)
            results[code] = {"provinsi_name": name, "indicators": indicators}
        except requests.exceptions.RequestException as exc:
            error = exc
        if on_progress:
            on_progress(code, name, error is None, error)
        # Don't sleep after the last request.
        if delay and i < len(codes) - 1:
            time.sleep(delay)
    return results


def save_csv_by_province(by_province: dict, periode: int, tahun: int, out_path: str):
    """Write a flat CSV of per-province indicator values, as produced by
    fetch_all_provinces(). Columns: provinsi_code, provinsi_name, indicator,
    periode, tahun, anggaran, realisasi."""
    fields = ["provinsi_code", "provinsi_name", "indicator", "periode", "tahun", "anggaran", "realisasi"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for code, data in by_province.items():
            for label, vals in data["indicators"].items():
                w.writerow(
                    {
                        "provinsi_code": code,
                        "provinsi_name": data["provinsi_name"],
                        "indicator": label,
                        "periode": periode,
                        "tahun": tahun,
                        "anggaran": vals["anggaran"],
                        "realisasi": vals["realisasi"],
                    }
                )


def save_csv(indicators: dict, periode: int, tahun: int, out_path: str):
    fields = ["indicator", "periode", "tahun", "anggaran", "realisasi"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for label, vals in indicators.items():
            w.writerow(
                {
                    "indicator": label,
                    "periode": periode,
                    "tahun": tahun,
                    "anggaran": vals["anggaran"],
                    "realisasi": vals["realisasi"],
                }
            )


