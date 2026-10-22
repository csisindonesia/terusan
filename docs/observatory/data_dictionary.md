# Observatory data dictionary

The observatory's Silver tables, queryable as `silver_restriction_*` (`terusan warehouse query`), exported by `terusan observatory export`. Schemas: `pipelines/src/terusan_pipelines/observatory/model.py`. Rules: [methodology.md](methodology.md).

## `restriction_observations`

One published figure per row. Charts and exports read `is_current` rows.

| Column | Meaning |
|---|---|
| `id` | UUIDv5 of `record_hash`. Stable across rebuilds and machines. |
| `source_report_id` | The report the figure first appeared in → `restriction_source_reports.id`. |
| `provider` | `google`, `tiktok`, `komdigi`, `meta`, `x`. |
| `country_code` | ISO 3166-1 alpha-2, or null when unresolved or worldwide. |
| `country_raw` | The country as the publisher wrote it. |
| `platform` | `youtube`, `google_other`, `google_all` (Google's country totals), `tiktok`, `meta`, `x`, `websites`, or another Komdigi platform key. |
| `product` | Google's product (e.g. `Web Search`, `Play Apps`). |
| `period` | `2025-S1`, `2024`, or null for an irregular range. |
| `period_start`, `period_end` | Inclusive dates. |
| `temporal_resolution` | `semiannual`, `annual`, `irregular`. |
| `requester_type` | `government`, `court`, `law_enforcement`, `executive_or_police` (Google pre-2019), `all`, `unknown`. |
| `requester_raw` | The publisher's requester label. |
| `requester_agency` | Set only on Komdigi's own figures. Never inferred. |
| `legal_basis` | Null: no provider in the observatory publishes one per figure. |
| `reason_category_raw` | Google's reason, or Komdigi's content category, as written. |
| `reason_category_normalized` | Per provider only: Google's label case-folded, Komdigi's translated. Not a cross-provider taxonomy. |
| `reason_crosswalk_confidence` | `high` / `medium` for that normalization. |
| `metric` | See below. |
| `metric_value` | decimal(38,9). Null unless `value_status = reported`. |
| `unit` | `count`, `percent` (0–100), `ratio` (0–1). |
| `value_status` | `reported`, `not_available` (`-`, `?`), `below_threshold` (`<10`). |
| `raw_value` | The cell as published. |
| `scope` | `local_law`, `terms_of_service`, `all`, `unspecified`. |
| `geographic_basis` | `request_origin` (platforms), `restriction_location` (Komdigi websites), `unknown` (Komdigi social media). |
| `source_label` | The publisher's column header or metric name. |
| `source_table`, `source_row_locator` | File and line or cell, e.g. `google-government-removal-requests.csv#L1234`, `….xlsx!Sheet1!R7`. |
| `extraction_method` | Extractor and parser version, e.g. `TikTokRemovalsExtractor@26`. |
| `confidence` | `high` / `medium` / `low`: how sure the mapping of label to metric is. |
| `comparability_group` | `provider:platform:metric:scope:requester:basis:resolution:unit`. Compare only within one group. |
| `notes` | The mapping's caveat, where there is one. |
| `quality_flags` | Validation flags (see methodology). |
| `identity_hash`, `record_hash` | What the figure is, and what it is plus its value. |
| `revision`, `is_current`, `last_seen_report_id` | Revision trail (see methodology). |
| `document_id`, `content_hash`, `raw_path`, `source_url`, `retrieved_at`, `parser_version`, `adapter_version`, `processed_at` | Provenance. |

### Metrics

| Metric | Unit | Definition | From |
|---|---|---|---|
| `requests_received` | count | Government requests received | TikTok "Total requests received", Google `number_of_requests` / "Number of Requests" |
| `items_specified` | count | Content items named in requests | TikTok "Total content received", Google items requested |
| `accounts_specified` | count | Accounts named in requests | TikTok |
| `items_actioned` / `accounts_actioned` | count | Actioned, without saying whether removed or restricted. `scope` says local law or guidelines | TikTok |
| `items_not_actioned` / `accounts_not_actioned` | count | Named but not actioned. For Google the reason is in `source_label` | TikTok, Google |
| `items_removed` | count | Removed. `scope` = legal (`local_law`) or policy (`terms_of_service`) | Google 2019+ |
| `urls_blocked` | count | Websites blocked, per content category | Komdigi, Jan 2018–Oct 2021 |
| `content_handled` | count | Komdigi's "penanganan konten", undefined by the publisher | Komdigi 2022, 2024 |
| `compliance_rate` | percent | Requests complied with in full or part | Google through 2019-H1 |
| `removal_rate` | ratio | TikTok's own published rate | TikTok |
| `other` | count | Google's historical `Total`, unit undefined | Google |

## `restriction_source_reports`

One row per landed artifact, parsed or not.

| Column | Meaning |
|---|---|
| `id` | UUIDv5 of `sha256`. |
| `provider`, `source_slug` | Who, and which Terusan source landed it. |
| `report_title`, `report_url`, `download_url` | What it is, the page a reader opens, and the file's URL. |
| `published_at`, `retrieved_at` | Server `Last-Modified` where sent, and landing time. |
| `period_start`, `period_end` | Span of the observations read from it. |
| `country_code` | Set when every observation is one country. |
| `raw_object_key`, `sha256`, `mime_type` | The immutable copy in RAW. |
| `source_method` | `official_download`, `public_html`, `pdf`, `manual`. |
| `source_revision` | Release tag (TikTok `2025H2`), ETag, or hash prefix. |
| `parser_version`, `license_notes`, `methodology_notes` | |
| `parsed`, `observation_count` | Whether figures were read from it. Report pages, Komdigi's 2025 PDF and hand-saved Meta/X files are evidence, not yet figures. |

## `restriction_quarantine`

| Column | Meaning |
|---|---|
| `provider`, `reason` | Why the record was not published. |
| `source_report_id`, `document_id`, `source_row_locator` | Where it came from. |
| `record` | The Bronze record, every column as text. |
| `adapter_version`, `detected_at` | |

## `restriction_ingestion_runs`

Appended on every build, one row per provider: `started_at`, `finished_at`, `status` (`succeeded`, `empty`, `failed`), `report_count`, `rows_extracted`, `rows_accepted`, `rows_quarantined`, `last_checked_at` (newest retrieval), `error_summary`, `code_version` (git), `parser_version`, `adapter_version`.

## `restriction_methodology_changes`

`provider`, `effective_date`, `description`, `affected_metrics`, `evidence`, `source_report_id` (the newest report of the source holding the evidence).

## Silver series (`/datasets`)

| Dataset | Source | Indicators |
|---|---|---|
| `government-removal-requests` | `tiktok-government-removals` | `tiktok_gov_requests_received`, `tiktok_gov_content_specified`, `tiktok_gov_accounts_specified`, `tiktok_gov_content_actioned_local_law`, `tiktok_gov_content_actioned_guidelines`, `tiktok_gov_content_not_actioned`, `tiktok_gov_accounts_actioned_local_law`, `tiktok_gov_accounts_actioned_guidelines`, `tiktok_gov_accounts_not_actioned`, `tiktok_gov_removal_rate` |
| `government-removals-totals` | `google-government-removals` | `google_gov_requests_{all,court,executive}`, `google_gov_items_requested_{all,court,executive}`, `google_gov_compliance_pct_{all,court,executive}` |
| `content-handling` | `komdigi-content-handling` | `komdigi_content_handled_<platform>` (`x`, `meta`, `google_all`, `tiktok`, `file_sharing`, `telegram`, …) |

Every country is included, geo-resolved by ISO code. Breakdown rows are not Silver series: a sum of them is not a figure any publisher stated.
