#!/usr/bin/env bash
#
# SP2KP's national weighted prices, Bronze into Silver.
#
# One indicator, not forty-two. The commodity is a dimension here, the way
# PIHPS does it and unlike the crosstab source's split by measure: every row
# prices a different good on the same basis, so `--commodity-column variant`
# tells them apart and the Commodities page can put SP2KP's rice beside Bank
# Indonesia's.
#
# `variant` and not `komoditas`: the ministry nests a category over its goods —
# `Beras` over `Beras Medium`, `Beras Premium`, `Beras SPHP Bulog` — and the
# category is the wrong grain. Three rice prices filed as "rice" would average
# the subsidised one into the premium one.
#
# `--unit-column unit`, because the basket is not all kilograms: cooking oil is
# by the litre, instant noodles by the packet, toddler formula by the 400-gram
# tin, free-range chicken by the bird. The API states it per variant.
#
# `--geo Indonesia`: the *harga nasional tertimbang* is one number for the
# country, and nothing in the row says so because the endpoint only ever
# returns the national figure. Declared here rather than left null so the
# series sits somewhere a reader can filter on.
#
# No `--number-format`: the API answers with plain integers. It is the crosstab
# that writes `14.043` and needs telling.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a day's ingestion replaces that series' observations rather than
# appending.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize sp2kp_national_price \
  --dataset sp2kp-national-prices \
  --source kemendag-sp2kp-national \
  --period-column date \
  --value-column price \
  --commodity-column variant \
  --unit-column unit \
  --geo Indonesia \
  --name "National weighted food price — SP2KP" \
  "$@"
