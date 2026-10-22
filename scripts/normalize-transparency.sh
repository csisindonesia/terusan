#!/usr/bin/env bash
#
# Government content restriction figures, Bronze into Silver.
#
# Four datasets, each holding figures exactly as a publisher states them.
# Nothing here is a sum the publisher did not publish: a breakdown is kept as a
# breakdown, with its category, rather than added up into a country total:
#
#   government-removal-requests   TikTok's requests, content and accounts
#                                 received and actioned, per country and
#                                 half-year, from 2022
#   government-removals-totals    Google's country totals by branch of
#                                 government, 2009 to the first half of 2019,
#                                 the years Google published totals
#   government-removals-breakdown Google from 2019 to the latest half-year, per
#                                 requester, product and reason, as published
#   content-handling              Komdigi's negative content handled per
#                                 social media platform, per year
#
# The extractor already named the indicator each figure belongs to, so this is
# `normalize-each` over that column.
#
# The mapping:
#
#   period       canonical already: `2025-S1` for a half-year, `2024` for a
#                year. The publisher's own label is kept beside it in Bronze
#   value        a count, a percentage (Google's compliance) or a ratio
#                (TikTok's removal rate), as published. Google's `<10` is a
#                withheld small count and Silver records it as suppressed;
#                Komdigi's `-` is a missing figure. Neither becomes zero
#   unit         count, percent or ratio — stated per row, because one dataset
#                holds all three
#   geo          the ISO alpha-2 code where the country resolved, the raw name
#                otherwise, which Silver keeps unresolved rather than guessing
#
# Requester, scope and comparability are not in Silver's schema. They are in
# the observatory's own tables: `terusan observatory build`.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

normalize() {
  # The code is the publisher's own name for the series: TikTok's metric name
  # and Google's column header are, but Komdigi's column header is "Jumlah" on
  # every series, so its platform label stands in.
  local source="$1" dataset="$2" code="$3"
  shift 3
  uv --project pipelines run terusan silver normalize-each \
    --by indicator \
    --dataset "$dataset" \
    --source "$source" \
    --period-column period \
    --value-column value \
    --unit-column unit \
    --geo-column geo \
    --name-column series_name \
    --code-column "$code" \
    --publisher-column publisher \
    --number-format en \
    "$@"
}

normalize tiktok-government-removals government-removal-requests measure "$@"
normalize google-government-removals government-removals-totals measure "$@"
# Google from 2019: the rows as published, one figure per requester, product
# and reason, the three named in `category`. Not summed into a country total:
# for 2019-S1, published in both formats, the rows add up to Google's own total
# for under half the countries.
normalize google-government-removals government-removals-breakdown measure \
  --category-column category "$@"
normalize komdigi-content-handling content-handling platform_raw "$@"

# The dataset catalogue and the source registry, so the three datasets carry
# their declared titles on /datasets, and the observatory's own tables.
uv --project pipelines run terusan silver dimensions
uv --project pipelines run terusan observatory build
