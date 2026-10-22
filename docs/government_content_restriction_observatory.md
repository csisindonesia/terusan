# Indonesia Government Content Restriction Observatory

## 1. Objective
Build a reproducible data ingestion and analytics module within **Terusan** to measure government-origin content restriction requests concerning Indonesia, drawing primarily on transparency disclosures from Meta, TikTok, X, and Google/YouTube, and comparing them against Komdigi statistics.

**Critical distinction:** a government *request* is not the same as a platform *action*, and a content *item* is not the same as an *account*, *URL*, *domain*, or *request*. Do not combine these metrics into one total. Country attribution does not establish that Komdigi specifically initiated a request.

## 2. Primary sources and acquisition

| Provider | Starting URL | Target information | Acquisition approach |
|---|---|---|---|
| Meta | https://transparency.fb.com/reports/content-restrictions/country/ID/ | Indonesia local-law content restrictions; Facebook and Instagram | Discover report download/export; otherwise capture public report tables and archival snapshots |
| TikTok | https://www.tiktok.com/transparency/en/government-removal-requests/ | Government removal requests, content involved, outcomes by country and period | Prefer published machine-readable downloads; verify schema per release |
| X | https://transparency.x.com/en/reports/countries/id | Legal demands, accounts/posts specified, withholding or removals | Inspect report access and exports; preserve source limitations |
| Google | https://transparencyreport.google.com/government-removals/government-requests/ID | Government removal requests, items, products, reasons and outcomes | Find downloadable tables/exports or documented report endpoints; validate counts |
| Komdigi | https://data.komdigi.go.id/ | Government-side content handling and blocking aggregates | Download official XLSX/CSV when available |
| Komdigi PPID | https://eppid.komdigi.go.id/ | Annual reports, supporting PDFs, possible detailed information requests | Fetch public documents and archive versions |

These URLs are **starting points**, not guaranteed stable APIs. Do not invent undocumented API endpoints or assume downloads exist. Respect robots.txt, terms of service, rate limits, and access controls. Do not bypass authentication or anti-bot restrictions.

## 3. Research questions
1. How many Indonesia-attributed government removal/restriction requests are reported by each platform over time?
2. How many items, accounts, posts, and URLs are involved, and how many are actually restricted or removed?
3. How do the platforms differ in action rates, reporting granularity, and legal justifications?
4. How does Indonesia compare with other ASEAN countries on like-for-like metrics?
5. How do platform-side figures compare with Komdigi-side aggregates, with explicit caveats about differing scope?
6. Can trends be associated with elections, policy changes, or major public events without asserting causality?

## 4. Data model
Use a normalized analytical model with immutable raw source artifacts and versioned normalized observations.

### `source_reports`
- `id` UUID primary key
- `provider` enum: `meta`, `tiktok`, `x`, `google`, `komdigi`, `other`
- `report_title`, `report_url`, `download_url` nullable
- `published_at`, `retrieved_at`, `period_start`, `period_end` (date; nullable if unknown)
- `country_code` ISO 3166-1 alpha-2; `ID` for Indonesia
- `raw_object_key` (R2/S3 location), `sha256`, `mime_type`
- `source_method` (`official_download`, `public_html`, `pdf`, `manual`)
- `source_revision`, `parser_version`, `license_notes`, `methodology_notes`

### `restriction_observations`
- `id` UUID primary key
- `source_report_id` foreign key
- `country_code`, `platform` (`facebook`, `instagram`, `tiktok`, `x`, `youtube`, `google_other`, etc.)
- `period_start`, `period_end`
- `requester_type` (`government`, `court`, `law_enforcement`, `unknown`)
- `requester_agency` nullable; never default to Komdigi
- `legal_basis` nullable; `reason_category_raw`, `reason_category_normalized` nullable
- `metric` enum: `requests_received`, `requests_actioned`, `items_specified`, `items_removed`, `items_restricted`, `accounts_specified`, `accounts_restricted`, `urls_blocked`, `other`
- `metric_value` nonnegative integer; null when unavailable (never replace missing with zero)
- `scope` (`local_law`, `terms_of_service`, `all`, `unspecified`)
- `geographic_basis` (`request_origin`, `restriction_location`, `other`, `unknown`)
- `source_label`, `source_table`, `source_row_locator`
- `extraction_method`, `confidence` (`high`, `medium`, `low`)
- `comparability_group`, `notes`
- `record_hash` unique for idempotent ingestion

### `ingestion_runs`
- `id`, `provider`, `started_at`, `finished_at`, `status`
- `report_count`, `rows_extracted`, `rows_accepted`, `rows_quarantined`
- `error_summary`, `code_version`, `parser_version`

### `methodology_changes`
- `provider`, `effective_date`, `description`, `affected_metrics`, `source_report_id`

Optional: `country_reference`, `reason_taxonomy`, `event_annotations`, `observation_revisions`.

## 5. Processing pipeline
1. **Discover** public reports for each provider, preserving publication dates and period coverage.
2. **Download and archive** source HTML, PDF, CSV, XLSX, or JSON as immutable objects; compute SHA-256 and retrieval timestamps.
3. **Parse** provider-specific schemas with adapters, never assuming consistent units or definitions across providers.
4. **Normalize** country, platform, reporting period, metric, legal basis, and requester attribution.
5. **Validate** values, date ranges, duplicate keys, and source totals; quarantine anomalies instead of silently coercing them.
6. **Version** corrected or revised source reports; do not overwrite historical evidence.
7. **Publish** curated tables and query-ready views in Terusan.
8. **Refresh** monthly for discovery and after new transparency report releases; record `last_checked_at` even when no new data is published.

Suggested adapters: `meta_adapter`, `tiktok_adapter`, `x_adapter`, `google_adapter`, `komdigi_adapter`. Each must implement `discover()`, `fetch()`, `parse()`, `validate()`, `normalize()` and produce provenance metadata.

## 6. Analytics and dashboards
- Time series: government requests received by platform and reporting period.
- Outcome funnel: requests -> specified items -> restricted/removed items **only where provider definitions allow linkage**.
- Restriction rate: `items_restricted / items_specified` only when numerator and denominator share compatible definitions and scope; otherwise show N/A.
- Country comparison: Indonesia vs Malaysia, Thailand, Vietnam, Philippines, Singapore; normalize by population only as an optional supplementary view with a cited population dataset.
- Categories: legal grounds and reason categories, with raw labels retained and crosswalk uncertainty displayed.
- Coverage heatmap: missing periods and unreported metrics.
- Government vs platform reporting: side-by-side, **not** a subtraction presented as unreported censorship.
- Source detail panel: original report, reporting definition, retrieval date, parsing version, and caveats.

## 7. Data-quality and interpretation rules
- Distinguish **requests received**, **requests complied with**, **content items affected**, and **accounts affected**.
- Do not interpret Indonesia-related requests as exclusively Komdigi requests unless the source identifies the agency.
- Do not label all content moderation as government censorship: distinguish legal requests from platform-initiated policy enforcement.
- Do not compare a six-month figure to a calendar-year figure without aligning periods.
- Do not infer unique accounts/users from post counts.
- Mark report publication lag, missing data, changes in platform methodology, and partial coverage.
- Store raw and normalized reason categories separately; mappings can be many-to-one and imperfect.
- Preserve takedown versus geoblocking versus account suspension as separate action types where data supports it.
- Every chart and export must link back to a source report and methodology.
- Never fabricate a number if a source is inaccessible or omits it.

## 8. Implementation guidance
Integrate into the existing Terusan ingestion/catalog system rather than introducing a separate application. Prefer scheduled ingestion workers, object storage for immutable source artifacts, SQL analytical tables or Parquet for normalized records, and a browser dashboard. Keep adapters modular and configuration-driven. Add structured logging, retry with backoff, idempotent writes, schema-drift alerts, and unit tests using legally obtained fixtures.

Suggested repository layout:
```text
src/observatory/
  adapters/{meta,tiktok,x,google,komdigi}/
  schemas/
  pipelines/
  quality/
  analytics/
  api/
  tests/
docs/observatory/
  methodology.md
  source_registry.md
  data_dictionary.md
```

## 9. AI agent execution plan
### Phase 0 — Source feasibility audit
- Open and inspect each source and document accessible periods, download format, update cadence, licensing, and restrictions.
- Produce `source_registry.md` with a confirmed/not-confirmed field for every proposed dataset.
- Identify actual public download URLs only after verifying them.
- Report unavailable or non-machine-readable sources explicitly.

### Phase 1 — Minimal ingestion
- Implement TikTok and Google adapters first **if their public export paths are verified**; otherwise prioritize whichever sources are reliably accessible.
- Archive original reports, load observations, and add source provenance.
- Backfill available historical periods without extrapolating missing values.

### Phase 2 — Expand and reconcile
- Add Meta, X, and Komdigi adapters.
- Create a comparability matrix for units, time periods, and country attribution.
- Build discrepancy notes for official vs platform figures, without treating them as equivalent.

### Phase 3 — Dashboards and research exports
- Build platform trends, country comparisons, outcomes, and source coverage.
- Offer CSV/Parquet exports with data dictionary and methodological caveats.
- Add event annotations and research-ready citations.

## 10. Acceptance criteria
- Every normalized value is traceable to an immutable source artifact and report period.
- All published metrics have an explicit unit and definition.
- Re-ingesting the same source produces no duplicate observations.
- Revised reports create auditable revisions.
- Unsupported comparisons are disabled or clearly flagged.
- Missing data is never represented as zero.
- At least one verified source can be ingested end-to-end with automated tests.
- A user can inspect the original source from every dashboard observation.

## 11. Agent instructions
**Start with the source feasibility audit.** Inspect the existing Terusan repository and reuse its architecture, conventions, authentication, scheduler, and storage. Do not assume a stack beyond what the repository actually contains. Before coding, report verified source availability and any blockers. Build small tested increments; do not invent platform APIs, dataset fields, statistics, or successful ingestion results.
