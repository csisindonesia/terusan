# Observatory methodology

How the Government Content Restriction Observatory turns transparency reports into figures, and what those figures do and do not say. Spec: [government_content_restriction_observatory.md](../government_content_restriction_observatory.md). Sources: [source_registry.md](source_registry.md). Columns: [data_dictionary.md](data_dictionary.md).

## Pipeline

```text
source (discover + fetch) → RAW (immutable, sha256) → extractor (parse) → Bronze (text)
   → adapter (normalize + validate) → restriction_* tables in Silver
   → as-published country series → Silver observations → /datasets, API, OData
```

| Spec step | Where |
|---|---|
| Discover, download, archive | `sources/transparency/{google,tiktok,manual}.py`, `sources/komdigi/content_handling.py`. RAW is content-addressed with a `metadata.json` sidecar. |
| Parse | `extract/transparency.py`: one Bronze record per figure, keeping the publisher's own label (`measure`) and cell locator. |
| Normalize, validate | `observatory/adapters/*.py`, `observatory/quality.py`. Mappings are declared in `observatory/taxonomy.py`. |
| Version | `observatory/pipeline.py`: identity and record hashes, revisions, `is_current`. |
| Publish | `terusan observatory build`, and `scripts/normalize-transparency.sh` for Silver series. |
| Refresh | monthly source schedules. Every build appends to `restriction_ingestion_runs`. |

## What is never done

- **No totals across providers.** A TikTok request, a Google item and a Komdigi "handled" item are different units. Each figure keeps its own `metric`, `unit` and `comparability_group`, and every group includes the provider.
- **No Komdigi attribution by country.** `requester_agency` is set only on Komdigi's own figures, where the publisher is the agency. Google's "Information and Communications Authority" stays a requester category.
- **No zero for missing.** `-` and `?` become `value_status = not_available`. Google's `<10` becomes `below_threshold`. In Silver observations these are `missing` and `suppressed`. `metric_value` is null in every such case.
- **No invented distinction.** TikTok's "actioned" is `items_actioned`, not `items_removed` or `items_restricted`, because TikTok does not say which.
- **No guessed labels.** A requester or measure label not in `taxonomy.py` is quarantined, not mapped by resemblance. This is how a provider's schema change shows up.
- **No silent fixes.** Unparseable, negative or impossible values, unreadable periods, and one report giving two values for one figure all go to `restriction_quarantine` with a reason.
- **No subtraction of government from platform figures.** `side_by_side` lays them next to each other and computes no difference.

## Validation

| Check | Outcome |
|---|---|
| Value is a non-negative number. Counts are whole, percent ≤ 100, ratio ≤ 1 | otherwise quarantined |
| Period parses and starts before it ends | otherwise quarantined |
| Expected columns present in each file | otherwise the whole document fails extraction, so the run says which file and which column |
| TikTok: content received = actioned (law) + actioned (guidelines) + not actioned. Same for accounts | flagged `components_do_not_sum_to_…` on every figure involved |
| Google: item outcomes sum to items requested, per row | flagged `item_outcomes_sum_…` |
| Komdigi: a stated total equals its rows | if it agrees, rows flagged `publisher_total_confirmed`. If not, the total is quarantined and the rows flagged `publisher_total_disagrees` |
| Same identity, two values in one report | both quarantined |
| Country not resolvable to ISO alpha-2 | kept with `country_raw`, flagged `country_unresolved` (e.g. Europol, Interpol in Google's rows) |
| Period not over when the report was retrieved | flagged `period_incomplete_at_retrieval` |

## Identity, re-ingestion, revisions

- **Identity hash**: everything that says which figure this is: provider, platform, product, country, period, metric, scope, requester (type and raw label), raw reason, source label, a stable table key, and the provider's extra breakdown. It excludes the value and the file name (TikTok renames its file every release).
- **Record hash**: identity plus value, unit and status. `id` is a UUIDv5 of it.
- **Re-ingesting** the same bytes lands nothing new (content-addressed RAW). A rebuild from the same Bronze gives the same record hashes. Builds replace the tables, so nothing is duplicated.
- **A revised release** is a new RAW object. Each changed figure becomes a new record under the same identity with `revision` + 1, and only the value from the latest report holding that identity is `is_current`. Unchanged figures stay one record, with `last_seen_report_id` updated. Earlier values are never overwritten.

## Comparisons

- **Within a provider, across countries or periods**: allowed when `comparability_group` matches. `compare_countries` refuses a selection spanning groups (e.g. local-law and guidelines actions together) and says why.
- **Across providers**: not like-for-like. The analytics offer no cross-provider total or ratio.
- **Periods**: platforms report half-years and Komdigi reports years. `side_by_side` shows a platform's calendar year only when both halves are reported, as their sum. Komdigi's January 2018 – October 2021 figure matches no calendar period, so it stays in the observatory tables (`temporal_resolution = irregular`) and is not a Silver series.
- **Rates**: only the declared pairs in `analytics.RATE_PAIRS`. TikTok: content and accounts actioned for local law ÷ specified. Google (2019+ table only): items removed for legal reasons ÷ items requested. Both sides must come from the same report, period, country and requester. Any side withheld gives `N/A`. Komdigi publishes no denominator.
- **Population normalisation**: not implemented. It needs a cited population dataset and would be a supplementary view only.

## Reporting changes

`restriction_methodology_changes` records changes that can be shown from a landed file:

- **Google, 2019-01-01**: country totals by branch of government, with compliance %, replaced by requester × product × reason rows with item outcomes. Evidence: the archive README. Note that the historical file still includes 2019-H1.
- **TikTok, 2022-01-01**: per-country breakdown begins. 2019–2021 is a worldwide total only. Evidence: the CSV.
- **Komdigi, 2022-01-01**: the published series switches from websites blocked by category to content handled per platform. 2023 is absent. Evidence: the portal listing.

A trend crossing one of these dates is not continuous.

## Event annotations

Not built yet. Elections (14 Feb 2024), the revisions of the ITE law and its implementing regulations, and the PSE registration regime (Permenkominfo 5/2020) are candidates. Any annotation must cite a source and must not be presented as a cause.

## Known gaps

- Meta and X: no figures until a researcher saves the reports and a reader is written against the verified format.
- Komdigi 2025: PDF only, not parsed. Komdigi 2023: not published on the portal.
- Komdigi PPID: unreachable on 2026-10-22.
- Google 2019+: no country total of requests in the download.
- TikTok 2019–2021 per-country figures are on archived report pages, not in the CSV. Not collected.
- Reason crosswalk across providers: not attempted. `reason_category_normalized` is per provider (Google case-folded, Komdigi translated), with `reason_crosswalk_confidence`.
- Portal dashboards: the three as-published datasets are on `/datasets`. Observatory-specific views (funnel, coverage heatmap, source panel) are not built.
