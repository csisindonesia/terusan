# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

#!/usr/bin/env python3
"""
sp2kp_food_prices.py
=====================

Scraper for Indonesia's Ministry of Trade (Kemendag) SP2KP daily food /
commodity price dashboard (https://sp2kp.kemendag.go.id/), which feeds the
"Catalogue_Regional" indicator group (17 daily food price indicators: rice
medium/premium/SPHP Bulog, granulated sugar bulk, cooking oil premium/bulk/
minyakita, beef hind quarter, broiler chicken meat/eggs, wheat flour,
imported soybeans, various chilis, shallots, honan garlic — price per
kabupaten/kota, nationwide).

WHAT THIS SCRIPT DOES AND WHY IT NEEDS A HEADLESS BROWSER
-----------------------------------------------------------
The public sp2kp.kemendag.go.id site is a Nuxt/Vue single-page app. The
price table itself is NOT server-rendered HTML and is NOT a Tableau Public
viz (despite the page loading Tableau's *Public* embedding JS). Reverse
engineering the compiled Nuxt bundle (see scrapers/*.js chunks fetched
during investigation) revealed the real chain:

  1. The frontend page /statistik/tabulasi-harga (chunk CyGb7x1L.js) calls
     GET https://api-sp2kp.kemendag.go.id/report/api/tableau/token-harga-public
     which returns a Tableau **trusted-ticket** token (no auth required —
     it's the public/anonymous ticket endpoint Kemendag exposes on purpose).

  2. That ticket is consumed at:
     https://analitik.kemendag.go.id/trusted/<ticket>/views/
         TabulasiHargaSP2KP_17708080966730/TabulasiHarga?:embed=yes...
     This is a **self-hosted Tableau Server** (analitik.kemendag.go.id),
     NOT Tableau Public. The `tableauscraper` PyPI package (built for the
     legacy/Tableau-Public bootstrap format, where session config is
     embedded directly in the HTML's `#tsConfigContainer`) does NOT work
     here: this Tableau Server version bootstraps entirely via JS
     (PreBootstrap.min.js -> startSession -> bootstrapSession), and the
     worksheet's `dataDictionary` in that bootstrap payload comes back
     EMPTY. The worksheet is rendered as raster PNG tiles (confirmed: 0
     <canvas>, 0 accessible table text in the DOM — only <img> tiles), so
     there is no client-side JSON/DOM data to scrape directly.

  3. The workbook's "Download > Data" (view underlying data / summary-data
     REST command) is explicitly DISABLED by the server admin — calling
     `POST .../commands/tabdoc/get-summary-data` returns HTTP 500
     ("LogicException ... Internal Error") regardless of parameters, and
     the toolbar's "Data" menu item is rendered `aria-disabled="true"`.

  4. HOWEVER, "Download > Crosstab" (Download Crosstab dialog, CSV format)
     IS enabled for this workbook. That is the path this script uses:
     it drives a real headless Chromium (via Playwright) to load the
     trusted-ticket view, click the toolbar Download button, choose
     "Crosstab", select the "CSV" radio option, and click the dialog's
     Download button — then intercepts the resulting browser download
     (a UTF-16 tab-separated .csv file containing the FULL underlying
     table: No, Kode Wilayah, Provinsi, Kabupaten Kota, Komoditas, HET/HA,
     and one column per selected date).

STATUS: WORKING. Confirmed against a live run (2026-09-07): produced 7,752
rows covering 514 kabupaten/kota x 17 commodities for the current date.
See the landed artifacts for a
saved sample.

REQUIREMENTS
------------
- `pip3 install playwright requests pandas` then `python3 -m playwright
  install chromium` (headless Chromium must be downloaded once).
- Requires actual JS execution (Playwright/Chromium). There is no known
  way to get this data with plain `requests` — the trusted-ticket ->
  bootstrapSession -> summary-data command chain that `tableauscraper`
  and manual `requests`-based replication rely on is blocked (step 3
  above); only the real browser UI flow (step 4) is left open, and even
  that requires JS to render the Download dialog and drive the file
  download.

LIMITATIONS / CAVEATS
----------------------
- Filters (Tanggal Awal/Akhir = start/end date, Provinsi, Kabupaten/Kota,
  Komoditas) default to "today" and "(All)" when the view first loads.
  This script does not currently manipulate those filter controls, so it
  always pulls the CURRENT default snapshot (typically "today"'s prices
  for all regions/commodities). Extending it to set a historical date
  range means interacting with the "Tanggal Awal"/"Tanggal Akhir" text
  inputs before opening the Download dialog — the DOM hooks for that are
  visible in `page.inner_text("body")` output (see investigation notes)
  but were not implemented/tested here.
- The set of commodities returned by SP2KP's own table differs slightly
  from the literal 17-name list in the Catalogue_Regional spec (e.g. it
  includes "Garam Halus"/salt and "Ikan Kembung"/mackerel, and on the date
  tested did not show a distinct "Beras SPHP Bulog" or "Kedelai Impor"
  row) — downstream mapping/normalization to the Catalogue_Regional
  indicator names will be needed.
- This is a real browser automation against a live government dashboard.
  Be a good citizen: keep request/run frequency low (e.g. once daily),
  and expect the site's internal Tableau workbook name / trusted-ticket
  endpoint / DOM selectors to change without notice, which would break
  the CSS/test-id selectors used below.

USAGE
-----
    python3 sp2kp_food_prices.py [--output OUTPUT.csv] [--headful]

"""

import io
import sys
from pathlib import Path

import pandas as pd
import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


TOKEN_URL = "https://api-sp2kp.kemendag.go.id/report/api/tableau/token-harga-public"
TABLEAU_SERVER = "https://analitik.kemendag.go.id"
WORKBOOK = "TabulasiHargaSP2KP_17708080966730"
VIEW = "TabulasiHarga"
SHEET_NAME = "Tabulasi SP2KP"  # worksheet name inside the dashboard


# ---------------------------------------------------------------------------
# Catalogue_Regional name mapping
# ---------------------------------------------------------------------------
# The Catalogue_Regional spec ("5. Real sector - Food prices, SP2KP", checklist
# "Real sector: Food prices (SP2KP)") lists 17 target indicator names, given
# alongside their expected Indonesian source terms ("Account name / Formula in
# respective webpage" column of the original catalogue spreadsheet). Those
# expected terms match SP2KP's actual `komoditas` values in the scraped data
# (confirmed against a live run, 2026-09-07) for 15 of the 17 -- verbatim,
# same spelling/capitalization once case-normalized. Two are NOT present as a
# distinct row in the scraped data on that day:
#   - "Beras SPHP Bulog" (catalogue: "Rice (SPHP Bulog)") -- no such komoditas
#     value was returned; SP2KP only had "Beras Medium" and "Beras Premium".
#   - "Kedelai Impor" (catalogue: "Imported Soybeans") -- no such komoditas
#     value was returned.
# Both are left commented out below (not force-mapped to a wrong komoditas)
# so `filter_to_catalogue()` naturally excludes them; if SP2KP starts
# returning these rows on some future date the mapping already anticipates
# the exact expected string.
#
# Note the scraped data also contains two commodities that are NOT part of
# the 17-item catalogue at all ("Garam Halus" / salt and "Ikan Kembung" /
# mackerel) -- these are intentionally left out of CATALOGUE_MAPPING and thus
# dropped by the filter.
CATALOGUE_MAPPING = {
    "Rice (medium grade)": "Beras Medium",
    "Rice (Premium Grade)": "Beras Premium",
    # "Rice (SPHP Bulog)": "Beras SPHP Bulog",  # not present in scraped data
    "Granulated Sugar (Bulk)": "Gula Pasir Curah",
    "Premium Packaged Palm Cooking Oil": "Minyak Goreng Sawit Kemasan Premium",
    "Bulk Palm Cooking Oil": "Minyak Goreng Sawit Curah",
    "Minyakita": "Minyakita",
    "Beef (Hind Quarter)": "Daging Sapi Paha Belakang",
    "Broiler Chicken Meat": "Daging Ayam Ras",
    "Broiler Chicken Eggs": "Telur Ayam Ras",
    "Wheat Flour": "Tepung Terigu",
    # "Imported Soybeans": "Kedelai Impor",  # not present in scraped data
    "Curly Red Chili": "Cabai Merah Keriting",
    "Red Bird's Eye Chili": "Cabai Rawit Merah",
    "Large Red Chili": "Cabai Merah Besar",
    "Shallots": "Bawang Merah",
    "Honan Garlic": "Bawang Putih Honan",
}

# Reverse lookup: komoditas (scraped) -> catalogue indicator name.
_KOMODITAS_TO_INDICATOR = {v: k for k, v in CATALOGUE_MAPPING.items()}


def filter_to_catalogue(df: pd.DataFrame) -> pd.DataFrame:
    """Filter scraped rows down to the Catalogue_Regional's 17 SP2KP
    indicators and relabel them with the catalogue's naming.

    Rows whose `komoditas` is not one of CATALOGUE_MAPPING's values (e.g.
    "Garam Halus", "Ikan Kembung") are dropped. Catalogue indicators with no
    matching komoditas in this scrape (e.g. "Rice (SPHP Bulog)", "Imported
    Soybeans") are simply absent from the output -- no row is fabricated for
    them.

    Returns a DataFrame with columns: indicator_name, kode_wilayah,
    provinsi, kabupaten_kota, tanggal, harga.
    """
    df = df.copy()
    df["komoditas_norm"] = df["komoditas"].astype(str).str.strip()
    matched = df[df["komoditas_norm"].isin(_KOMODITAS_TO_INDICATOR)].copy()
    matched["indicator_name"] = matched["komoditas_norm"].map(_KOMODITAS_TO_INDICATOR)

    return matched[
        ["indicator_name", "kode_wilayah", "provinsi", "kabupaten_kota", "tanggal", "harga"]
    ].reset_index(drop=True)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def get_trusted_ticket() -> str:
    """Fetch a one-time-use Tableau trusted-authentication ticket.

    This is a public, unauthenticated endpoint that Kemendag's own
    frontend calls to embed the dashboard for anonymous visitors.
    """
    resp = requests.get(TOKEN_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    ticket = resp.text.strip()
    if not ticket or ticket == "-1":
        raise RuntimeError(
            f"Did not receive a valid trusted ticket from {TOKEN_URL} "
            f"(got: {ticket!r}). The endpoint may be rate-limited or down."
        )
    return ticket


def build_view_url(ticket: str) -> str:
    return (
        f"{TABLEAU_SERVER}/trusted/{ticket}/views/{WORKBOOK}/{VIEW}"
        "?:embed=yes&:showShareOptions=false&:showAskData=false&:customViews=no"
    )


def download_crosstab_csv(headless: bool = True, timeout_ms: int = 45000) -> bytes:
    """Drive a real headless browser through the Download > Crosstab > CSV
    flow and return the raw downloaded file bytes (UTF-16, tab-separated).
    """
    if sync_playwright is None:
        raise RuntimeError(
            "playwright is not installed. Run: pip3 install playwright && "
            "python3 -m playwright install chromium"
        )

    ticket = get_trusted_ticket()
    view_url = build_view_url(ticket)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1400, "height": 1000})
        try:
            page.goto(view_url, wait_until="load", timeout=timeout_ms)
            # Let the viz fully render (it renders as raster tiles, which
            # take a few seconds after the initial vizql bootstrap).
            page.wait_for_timeout(8000)

            # Open the toolbar Download menu.
            page.click("#download")
            page.wait_for_timeout(1000)

            # "Data" (view underlying data) is disabled server-side for this
            # workbook -- only "Crosstab" works. Click it.
            page.get_by_role("menuitem", name="Crosstab", exact=True).first.click()
            page.wait_for_timeout(1500)

            # Select the CSV format radio button (Excel is selected by default).
            page.click(
                '[data-tb-test-id="crosstab-options-dialog-radio-csv-RadioButton"]',
                force=True,
            )
            page.wait_for_timeout(300)

            with page.expect_download(timeout=30000) as dl_info:
                page.click('[data-tb-test-id="export-crosstab-export-Button"]')
            download = dl_info.value

            tmp_path = Path("/tmp") / "sp2kp_crosstab_download.csv"
            download.save_as(str(tmp_path))
            data = tmp_path.read_bytes()
            tmp_path.unlink(missing_ok=True)
            return data
        finally:
            browser.close()


def parse_crosstab_csv(raw_bytes: bytes) -> pd.DataFrame:
    """Parse the UTF-16, tab-separated crosstab export into a tidy DataFrame."""
    df = pd.read_csv(io.BytesIO(raw_bytes), sep="\t", encoding="utf-16")
    df.columns = [c.strip() for c in df.columns]

    # The last column is the price date (its header is literally the date,
    # e.g. "04/09/2026"). Melt it into a proper "tanggal"/"harga" pair so
    # the shape is stable even as the date column name changes daily.
    fixed_cols = ["No", "Kode Wilayah", "Provinsi", "Kabupaten Kota", "Komoditas", "HET/HA"]
    date_cols = [c for c in df.columns if c not in fixed_cols]

    df = df.rename(
        columns={
            "Kode Wilayah": "kode_wilayah",
            "Provinsi": "provinsi",
            "Kabupaten Kota": "kabupaten_kota",
            "Komoditas": "komoditas",
            "HET/HA": "het_ha",
        }
    )

    if date_cols:
        tidy = df.melt(
            id_vars=["kode_wilayah", "provinsi", "kabupaten_kota", "komoditas", "het_ha"],
            value_vars=date_cols,
            var_name="tanggal",
            value_name="harga",
        )
    else:
        tidy = df

    return tidy
