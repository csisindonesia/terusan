#!/usr/bin/env bash
#
# Chinese metal and coal futures from Sina, Bronze into Silver.
#
# Five series per contract: open, high, low, close — as the Yahoo instruments
# have — and the exchange's settlement price, which is what Shanghai and Dalian
# mark positions to and what a Chinese price report quotes. Volume and open
# interest reach Bronze and stop there.
#
# Every contract is quoted in yuan per tonne. The extractor joins the unit on
# from the landing record and it is read from the row here.
#
# The `0` contracts are Sina's continuous main contract, which rolls between
# delivery months; a step on a roll date can be the roll. See the source's
# docstring.
#
# Idempotent: normalization rebuilds an indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

# dataset | source slug | commodity id | readable name
CONTRACTS=(
  "shfe-nickel|sina-shfe-nickel|nickel|Nickel (SHFE)"
  "shfe-tin|sina-shfe-tin|tin|Tin (SHFE)"
  "shfe-stainless|sina-shfe-stainless|stainless-steel|Stainless steel (SHFE)"
  "shfe-aluminium|sina-shfe-aluminium|aluminium|Aluminium (SHFE)"
  "shfe-zinc|sina-shfe-zinc|zinc|Zinc (SHFE)"
  "dce-iron-ore|sina-dce-iron-ore|iron-ore|Iron ore (DCE)"
  "dce-coking-coal|sina-dce-coking-coal|coking-coal|Coking coal (DCE)"
)

FIELDS=(open high low close settlement)

for entry in "${CONTRACTS[@]}"; do
  IFS="|" read -r dataset source commodity name <<<"$entry"
  key="${dataset//-/_}"
  for field in "${FIELDS[@]}"; do
    uv --project pipelines run terusan silver normalize "${key}_price_${field}" \
      --dataset "$dataset" \
      --source "$source" \
      --period-column date \
      --value-column "$field" \
      --unit-column unit \
      --commodity "$commodity" \
      --number-format en \
      --name "$name price — $field" \
      "$@"
  done
done
