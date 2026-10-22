# Observatory source registry

What each proposed source actually offers, as checked on **2026-10-22**. Spec: [government_content_restriction_observatory.md](../government_content_restriction_observatory.md). "Confirmed" means the file was downloaded, opened and read. It does not mean the publisher said so.

| Provider | Terusan source | Status | Format | Coverage confirmed | Collected |
|---|---|---|---|---|---|
| Google | `google-government-removals` | **Confirmed** | ZIP of CSVs | 2009-H2 → 2025-H1, every country | automatically, monthly |
| TikTok | `tiktok-government-removals` | **Confirmed** | CSV | per country 2022-H1 → 2025-H2. 2019–2021 worldwide total only | automatically, monthly |
| Komdigi | `komdigi-content-handling` | **Confirmed** (3 XLSX, 1 PDF) | XLSX, PDF | websites Jan 2018–Oct 2021. Social media 2022, 2024. 2025 as PDF only | automatically, monthly |
| Meta | `meta-content-restrictions` | **Not machine-collectable** | page renders client-side | not verified | by hand into `tmp/transparency/meta` |
| X | `x-removal-requests` | **Not machine-collectable** | undocumented internal endpoint | not verified | by hand into `tmp/transparency/x` |
| Komdigi PPID | none | **Unreachable** | n/a | n/a | not registered |

## Google: government requests to remove content

- **Download:** `https://storage.googleapis.com/transparencyreport/google-government-removals.zip` (358 KB). Google links it from the report's overview page. It is a published download, not an endpoint read out of the page.
- **Contents** (from the archive's `README.txt` and the files):
  - `google-government-removal-requests.csv`: 2019-H1 → 2025-H1. One row per country, half-year, requester category, product and reason. Columns: `number_of_requests`, `items_requested_removal`, and six item outcomes (`items_removed_legal`, `items_removed_policy`, `items_content_not_found`, `items_not_enough_info`, `items_no_action_taken`, `items_content_already_removed`). 17,482 rows, 330 of them for Indonesia.
  - `historical/google-government-removal-requests-historical.csv`: country totals by half-year. Columns: all requests, court orders, and "Other Requests (Executive, Police, etc.)", each with number of requests, % complied with, and items. The README says "through 2018". **The file actually runs to 2019-06-30**, so it overlaps the new format by one half-year.
  - `historical/google-government-detailed-removal-requests-historical.csv`: product × reason, with court and executive counts and items.
  - `historical/google-government-detailed-removal-requests.csv`: product × reason with one `Total` column. The README does not say whether it counts requests or items. Kept as metric `other` with confidence `low`, and never compared.
- **Markers:** `<10` is a withheld small count and `?` is unknown. Both are kept as statuses and never read as numbers.
- **Requester categories** (2019+): Information and Communications Authority, Government Officials, Police, Court Order directed at Google, Court Order directed at 3rd party, Consumer Protection Authority, Data Protection Authority, Military, Supression Orders, Other. **None names an agency.** "Information and Communications Authority" is the closest Google comes to Komdigi, and the observatory does not close that gap.
- **Not in the download:** country totals of requests after 2019-H1. Rows are per product and reason, so summing them is not a country total: one request naming two products appears on two rows.
- **Licence:** Google Transparency Report, published for public use with attribution.

## TikTok: Government Removal Requests Report

- **Page:** `https://www.tiktok.com/transparency/en/government-removal-requests/`, which redirects to `/safety/en/transparency/government-removals-report`. It has one Download link.
- **Download (2025-H2 release):** `https://sf16-va.tiktokcdn.com/obj/eden-va2/…/2025H2_CLIGR/Final/Downloads/2025H2_GRFCR_English.csv`. The path changes with each release, so the source reads the link off the page on every run.
- **Contents:** 5,864 rows in long format (`metric_name`, `period_value`, `location_value`, `result`). Eleven metrics: total requests received, total content received, total accounts received, content and accounts actioned for local law, content and accounts actioned for community guidelines, content and accounts not actioned, removal rate, and "Total Government Requests" (worldwide, 2019–2021). The `policy` and `task` columns are `all` throughout this release.
- **Self-consistency:** content received = actioned (local law) + actioned (guidelines) + not actioned. Checked on every row, and true in this release (Indonesia 2025-H2: 1,282 + 1,359 + 1,254 = 3,895).
- **Values** are written as `1113.0`. The `.0` comes from the export tool, not from TikTok's precision.
- **The report page** carries per-request tokens, so each run lands a new snapshot of it. The CSV deduplicates by content.
- **Licence:** TikTok Transparency Center, published for public use with attribution.

## Komdigi: negative content handled and blocked

- **Portal:** `https://data.komdigi.go.id/opendata`, an Inertia app. Datasets are found through the portal's search (`/opendata/dataset?q=…`) and filtered to the publisher Ditjen Pengawasan Ruang Digital with "konten" or "blokir" in the slug.
- **Downloads:** each resource's address in the page data points at the portal's internal CKAN (`http://172.30.103.58:5000/…`), which is not reachable. The portal's own JavaScript rewrites it to `https://data.komdigi.go.id/warehouse/<path>`, and so does the source. All four files answer there.
- **Files confirmed:**
  - `penanganan-konten-negatif-pada-situs-internet-periode-januari-2018-oktober-2021.xlsx`: websites blocked by 15 content categories, as a single figure for the whole 46-month period. **Its total row (2,679,352) does not equal the sum of its categories (1,558,736).** The total is quarantined with both numbers.
  - `jumlah-penanganan-konten-internet-negatif-pada-media-sosial-tahun-2022.xlsx`: content handled per platform (11 platforms, "Twitter" among them).
  - `penanganan-konten-internet-negatif-pada-sosial-media-2024.xlsx`: content handled per platform (15 platforms, "X" among them). Hello-App and Yahoo are `-`.
  - `breakdown-penanganan-konten-internet-negatif-tahun-2025.pdf` (1.4 MB): landed, not yet parsed.
- **Not found:** 2023, and any definition of a "handled" item.
- **Licence:** Satu Data Indonesia open data.

## Meta: content restrictions based on local law

- **Page:** `https://transparency.meta.com/reports/content-restrictions/country/ID/`. Answers a plain client with HTTP 200 and a browser user-agent string with 400. **The served HTML contains no figures**: they render in the browser.
- **robots.txt:** "Collection of data on Facebook through automated means is prohibited unless you have express written permission from Facebook." The observatory does not fetch the page by machine.
- **What would open it:** a researcher saves the report, or an export if Meta offers one, into `tmp/transparency/meta`, then runs `terusan sources run meta-content-restrictions`. Whether Meta offers a CSV export has **not been verified**, so no reader has been written. Saved files land in RAW with provenance and appear in `restriction_source_reports` as unparsed.

## X: removal requests

- **Page:** `https://transparency.x.com/en/reports/countries/id`. robots.txt allows crawling. The HTML holds no figures. The tables are filled by `project.js` from `/1.1/strato/column/None/periodtype=…/cms/prod/transparency/…`, an internal endpoint X does not document.
- **Not used**, under the spec's rule against undocumented endpoints.
- **What would open it:** the same drop directory, `tmp/transparency/x`.

## Komdigi PPID

- `https://eppid.komdigi.go.id/` timed out (30 s) from this network on 2026-10-22. Not registered as a source until it answers. Annual reports there would be PDFs needing their own reader.

## How to refresh

```bash
cd pipelines
uv run terusan sources run google-government-removals
uv run terusan sources run tiktok-government-removals
uv run terusan sources run komdigi-content-handling
uv run terusan warehouse extract research google-government-removals
uv run terusan warehouse extract research tiktok-government-removals
uv run terusan warehouse extract government komdigi-content-handling
cd .. && ./scripts/normalize-transparency.sh     # Silver series, catalogue, observatory tables
```

The three automatic sources carry monthly schedules (`0 4 3 * *`, `10 4 3 * *`, `20 4 3 * *`). Unchanged downloads land nothing. Each build records `last_checked_at` per provider in `restriction_ingestion_runs`, even when nothing new was published.
