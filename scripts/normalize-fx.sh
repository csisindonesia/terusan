#!/usr/bin/env bash
#
# Yahoo's exchange rates, Bronze into Silver.
#
# Eight pairs, four series each — open, high, low and close, because Silver
# stores one figure per observation and a daily bar is four figures.
#
# Unlike the commodities, all eight land under one dataset: a reader asking
# for "the exchange rate" wants the table. That is what `--include symbol=`
# is doing below. Bronze carries Yahoo's own ticker on every row, so the pair
# is selected by the ticker rather than by the file it arrived in — the file
# is a landing detail, and the ticker is what the publisher said.
#
# The unit comes from the response, not from here: `EURIDR=X` is quoted in
# IDR and `USDCNY=X` in CNY, and asserting one unit for both would invert a
# rate. Which currency is being *priced* is carried by the series key:
# `eur_idr_rate_close` in IDR is rupiah per euro.
#
# No `--commodity`. A currency is not a good, and the commodity dimension is
# what the Commodities page filters on — a rate landing there would put the
# yen beside coffee.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a day's ingestion replaces that series' observations rather than
# appending.

set -euo pipefail

cd "$(dirname "$0")/.."

DATASET=exchange-rates
SOURCE=yahoo-exchange-rates

# ticker | series key | what it is called
PAIRS=(
  "IDR=X|usd_idr|US dollar / rupiah exchange rate"
  "EURIDR=X|eur_idr|Euro / rupiah exchange rate"
  "JPYIDR=X|jpy_idr|Japanese yen / rupiah exchange rate"
  "GBPIDR=X|gbp_idr|Pound sterling / rupiah exchange rate"
  "USDCNY=X|usd_cny|US dollar / Chinese yuan exchange rate"
  "SGDIDR=X|sgd_idr|Singapore dollar / rupiah exchange rate"
  "MYRIDR=X|myr_idr|Malaysian ringgit / rupiah exchange rate"
  "THBIDR=X|thb_idr|Thai baht / rupiah exchange rate"
)

FIELDS=(open high low close)

for entry in "${PAIRS[@]}"; do
  IFS="|" read -r symbol key name <<<"$entry"
  for field in "${FIELDS[@]}"; do
    uv --project pipelines run terusan silver normalize "${key}_rate_${field}" \
      --dataset "$DATASET" \
      --source "$SOURCE" \
      --include "symbol=${symbol}" \
      --period-column date \
      --value-column "$field" \
      --unit-column currency \
      --number-format en \
      --name "$name, $field" \
      "$@"
  done
done
