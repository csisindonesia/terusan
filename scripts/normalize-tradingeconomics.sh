#!/usr/bin/env bash
#
# Trading Economics' Indonesian indicators, Bronze into Silver.
#
# Three passes, in this order (the third is described beside it below):
#
#   1. The index page, which lists every indicator the country has. It carries
#      the reference period each figure belongs to, but rounds the figure to
#      three significant digits: Indonesia's housing index prints as `111`.
#      This pass is what gives the warehouse complete coverage, and it is the
#      pass that publishes what each series is called.
#
#   2. Each indicator's own page, which prints the same reading at full
#      precision — `110.89` — in the table of neighbouring indicators, where
#      the page lists itself. Ninety-nine of the hundred-odd pages carry one.
#
# The second pass replaces the first's observations for the indicators it
# covers, because normalization rebuilds an indicator from scratch rather than
# appending to it. The handful of pages with no row of their own — the
# manufacturing PMI, which is licensed from S&P Global and shown to
# subscribers only, the currency and stock market pages, which print a live
# quote table instead — keep the index's rounded figure, which is the whole
# reason the index is landed as an artifact of its own.
#
# Names are published by the first pass alone. The Silver indicators table is
# replaced one source at a time, so a second pass carrying --name-column would
# delete the names the first one wrote.
#
# Idempotent: re-running after a day's ingestion replaces each indicator's
# observations rather than appending a second copy.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="tradingeconomics-indonesia"
DATASET="indonesia-indicators"

# `indicator_key` is the vendor-prefixed key the extractor composes —
# `te_housing_index`. Prefixed because Trading Economics carries Bank
# Indonesia's and BPS's figures at its own revision and rounding, so an
# unprefixed `inflation_cpi` would eventually collide with the same series
# taken from the agency that publishes it, and the two are not interchangeable.
#
# `indicator_title` is the English name read off the page's URL. The Indonesian
# name the page prints is in Bronze beside it, under `indicator_name`.
common=(
  --by indicator_key
  --dataset "$DATASET"
  --source "$SOURCE"
  --period-column reference
  --value-column last
  --unit-column unit
  --geo-column country
  --number-format en
)

echo "pass 1/3: the index, every indicator"
uv --project pipelines run terusan silver normalize-each \
  "${common[@]}" --include kind=index --name-column indicator_title

echo "pass 2/3: each page's own reading, at full precision"
uv --project pipelines run terusan silver normalize-each \
  "${common[@]}" --include kind=reading

# Last, and it wins: an indicator whose chart was collected is rebuilt from
# its history, which includes the latest figure and dates every point at the
# series' own frequency. The readings date everything by a month — `Dec 2025`
# for an annual series — so they would otherwise sit beside the history as a
# second, monthly copy of the same figure. An indicator with no chart (the
# market pages) is not in this pass and keeps its reading.
echo "pass 3/3: each chart's history"
uv --project pipelines run terusan silver normalize-each \
  --by indicator_key \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --include kind=history \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column country \
  --publisher-column publisher \
  --number-format en
