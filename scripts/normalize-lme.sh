#!/usr/bin/env bash
#
# The LME's official base-metal prices, Bronze into Silver.
#
# Westmetall publishes one table per metal, and each row carries three figures:
# the official cash settlement, the three-month price, and LME warehouse
# stocks. Silver stores one figure per observation, so each metal becomes three
# series.
#
# The prices are USD/t and the stocks tonnes. Neither is on the page; the
# extractor joins them on, and they are read from the row here rather than
# asserted, so a metal quoted otherwise one day is caught by its own row.
#
# `--commodity` for the same reason as the Yahoo script: one file per metal, so
# no column names the good, and without it the figures never reach the
# commodity dimension.
#
# Idempotent: normalization rebuilds an indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

# dataset | commodity id | readable name
METALS=(
  "lme-nickel|nickel|Nickel"
  "lme-tin|tin|Tin"
  "lme-copper|copper|Copper"
  "lme-aluminium|aluminium|Aluminium"
  "lme-zinc|zinc|Zinc"
  "lme-lead|lead|Lead"
)

for entry in "${METALS[@]}"; do
  IFS="|" read -r dataset commodity name <<<"$entry"
  key="${dataset//-/_}"

  # column | indicator suffix | unit column | readable suffix
  for series in \
    "cash_settlement|price_cash|unit|LME official cash settlement" \
    "three_month|price_3m|unit|LME three-month price" \
    "stock|stock|stock_unit|LME warehouse stocks"; do
    IFS="|" read -r column suffix unit_column label <<<"$series"
    uv --project pipelines run terusan silver normalize "${key}_${suffix}" \
      --dataset "$dataset" \
      --source westmetall-lme \
      --period-column date \
      --value-column "$column" \
      --unit-column "$unit_column" \
      --commodity "$commodity" \
      --number-format en \
      --name "$name — $label" \
      "$@"
  done
done
