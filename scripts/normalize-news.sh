#!/usr/bin/env bash
#
# The news monitor's counts into Silver observations.
#
# The crawl produces three things and only one of them is a figure: the corpus
# and the coded events are read through /v1/news/*, while the counts — incidents
# and casualties per province per month — are observations like any other, and
# belong on the same axis as every other series in the warehouse.
#
# Run after `terusan news cluster`, which is what produces the counts. Both are
# idempotent, so running this twice writes the same rows.
#
#   scripts/normalize-news.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="news-monitoring"
DATASET="news-violence-counts"

# `normalize-each --by indicator`, exactly as the human VEWS figures are
# normalized: the collection holds several series side by side and each needs
# its own indicator, or ten measures collapse into one.
uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column geo \
  --name-column series_name \
  --code-column measure \
  --publisher-column publisher \
  --number-format en \
  "$@"
