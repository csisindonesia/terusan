#!/usr/bin/env bash
#
# Bank Indonesia's consumer and retail sales surveys, Bronze into Silver.
#
# `extract/bank_indonesia.py` hands both workbooks to the vendored parsers,
# which already produce one figure per row, keyed by an `indicator` column.
# Every series is one value of that column, so each is one `normalize` call
# narrowed on it. The consumer survey's rows are:
#
#   indicator   `cci_ikk`, `ceci_ike`, `cei_iek` per city, and the same three
#               with `_raw_national` for the eighteen-city national figure
#   scope       the city, or `NASIONAL (18 kota)`, which resolves to the country
#   period      the month as the workbook abbreviates it, `Jan`, with `year`
#
# The retail sales survey's rows are the same shape with `city` for the place
# (`INDEKS TOTAL` is the national index) and four indicators: the index, and
# its annual, monthly and quarterly growth. The quarterly table labels its
# periods `Q1` to `Q4`.
#
# Only one city in each survey, Jakarta, is also a province, so the rest stay
# unresolved with their names kept as printed, which is what a city-level
# series should do until the geography registry has cities.
#
# A figure BI marks as provisional (`Nov*`, `Q4*`) has no period the parser
# accepts and is dropped. The next release restates it without the mark, and
# that is the figure worth keeping.
#
# The five series that were in Silver before this script existed keep their
# keys, and with them their identifiers: `consumer_current_conditions` is the
# national CECI, not `ceci_ike`.
#
# `--number-format en`: the parsers write floats with a decimal point.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

normalize() {
  local source="$1" dataset="$2" geo="$3" slug="$4" key="$5" unit="$6" name="$7"
  shift 7
  uv --project pipelines run terusan silver normalize "$slug" \
    --dataset "$dataset" \
    --source "$source" \
    --include "indicator=$key" \
    --period-parts period,year \
    --value-column value \
    --geo-column "$geo" \
    --unit "$unit" \
    --number-format en \
    --name "$name" \
    "$@"
}

consumer() { normalize bi-consumer-survey consumer-survey scope "$@"; }
retail() { normalize bi-retail-sales-survey retail-sales city "$@"; }

consumer consumer_confidence cci_ikk_raw_national index "Consumer confidence" "$@"
consumer consumer_current_conditions ceci_ike_raw_national index "Consumer current conditions" "$@"
consumer consumer_expectations cei_iek_raw_national index "Consumer expectations" "$@"
consumer consumer_confidence_by_city cci_ikk index "Consumer confidence by city" "$@"
consumer consumer_current_conditions_by_city ceci_ike index "Consumer current conditions by city" "$@"
consumer consumer_expectations_by_city cei_iek index "Consumer expectations by city" "$@"

retail retail_sales_index retail_sales_index index "Retail sales index" "$@"
retail retail_sales_growth_yoy retail_sales_index_growth_yoy percent "Retail sales growth yoy" "$@"
retail retail_sales_growth_mtm retail_sales_index_growth_mtm percent "Retail sales growth mtm" "$@"
retail retail_sales_growth_quarterly_yoy retail_sales_index_growth_qoq_yoy percent "Retail sales growth, quarterly yoy" "$@"
