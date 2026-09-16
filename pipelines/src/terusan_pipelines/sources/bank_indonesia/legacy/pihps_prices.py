# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Scraper: PIHPS regional food prices (Bank Indonesia)
Source: https://www.bi.go.id/hargapangan/TabelHarga/PasarTradisionalDaerah
Covers indicators: Food price - Rice, Chicken Meat, Beef, Chicken Eggs, Shallots,
Garlic, Red Chili, Bird's Eye Chili, Cooking Oil, Granulated Sugar (rows 51-60 in
Catalogue_Regional).

Endpoint reverse-engineered from page's DevExtreme grid config (functions.js /
inline script): GET /hargapangan/WebSite/TabelHarga/GetGridDataDaerah
Params (from JS OnBeforeSend handler):
  price_type_id  - 1 = "Rata-rata" (traditional market average) per site default
  comcat_id      - commodity/category id, empty = all commodities
  province_id    - empty = national level
  regency_id     - empty = national level
  market_id      - empty = all markets
  tipe_laporan   - 1 = per-commodity report
  start_date/end_date - dd not required to be zero padded, format YYYY-MM-DD
Response: JSON {"data": [{"no", "name", "level", "<dd/mm/yyyy>": "price", ...}], "totalCount"}
No auth required. Confirmed working via plain GET (see run below).
"""
import csv

import requests

BASE = "https://www.bi.go.id/hargapangan/WebSite/TabelHarga/GetGridDataDaerah"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(start_date: str, end_date: str, province_id: str = "", regency_id: str = ""):
    params = {
        "price_type_id": 1,
        "comcat_id": "",
        "province_id": province_id,
        "regency_id": regency_id,
        "market_id": "",
        "tipe_laporan": 1,
        "start_date": start_date,
        "end_date": end_date,
        "requireTotalCount": "true",
    }
    r = requests.get(BASE, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def save_csv(rows, out_path):
    if not rows:
        return
    fields = sorted({k for row in rows for k in row.keys()}, key=lambda k: (k not in ("no", "name", "level"), k))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


