#!/usr/bin/env bash
#
# Yahoo's commodity futures, Bronze into Silver.
#
# Seven instruments, four series each — open, high, low and close, because
# Silver stores one figure per observation and a daily bar is four figures.
#
# The reason this script exists rather than four `normalize` calls per
# instrument in someone's shell history is `--commodity`. Yahoo publishes one
# file per instrument, so no column in the response names the good: the
# commodity is the series' identity, and without it declared here the figures
# never reach the commodity dimension and the Commodities page shows Bank
# Indonesia's groceries and nothing else. The value is resolved through
# `reference/commodities/commodities.csv`, so `gold` becomes `commodity_id`
# rather than a label nobody can join on.
#
# The mapping is three columns:
#
#   date      the session, which Yahoo dates by the exchange
#   open/…    the bar, one series per field
#   currency  stated per response, not asserted here: Yahoo quotes coffee in
#             US cents (USX) and gold in dollars, and one unit for both would
#             be wrong by a hundred
#
# IHSG is landed by the same source family and is deliberately not here: an
# equity index is not a commodity, and it has a volume series these do not.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a day's ingestion replaces that series' observations rather than
# appending.

set -euo pipefail

cd "$(dirname "$0")/.."

# dataset | source slug | commodity id in the registry
INSTRUMENTS=(
  "gold|yahoo-gold|gold"
  "copper|yahoo-copper|copper"
  "brent-crude|yahoo-brent-crude|brent-crude"
  "thermal-coal|yahoo-thermal-coal|thermal-coal"
  "palm-oil|yahoo-palm-oil|palm-oil"
  "coffee|yahoo-coffee|coffee"
  "cocoa|yahoo-cocoa|cocoa"
)

FIELDS=(open high low close)

for entry in "${INSTRUMENTS[@]}"; do
  IFS="|" read -r dataset source commodity <<<"$entry"
  # The indicator key each series was declared under: `palm-oil` lands as
  # `palm_oil_price_close`. The identifier is derived from it (program.md §10),
  # so passing the key re-normalizes the existing series rather than opening a
  # second one beside it.
  key="${dataset//-/_}"
  for field in "${FIELDS[@]}"; do
    uv --project pipelines run terusan silver normalize "${key}_price_${field}" \
      --dataset "$dataset" \
      --source "$source" \
      --period-column date \
      --value-column "$field" \
      --unit-column currency \
      --commodity "$commodity" \
      --number-format en \
      "$@"
  done
done
