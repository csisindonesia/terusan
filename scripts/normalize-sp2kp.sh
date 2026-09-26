#!/usr/bin/env bash
#
# SP2KP food prices, Bronze into Silver.
#
# Two series, and the second is the point. `price` is what Kemendag's
# enumerators found in the market; `ceiling` is the Harga Eceran Tertinggi or
# Harga Acuan the government set for that good. Apart, they are two price
# series; together they answer the question the system exists for, which is not
# "what does rice cost" but "is it selling above the ceiling, and where".
#
# The ceiling is blank for the goods that have none — bulk cooking oil, salt,
# mackerel, the large red chili — so that series is shorter than the other by
# design, not by loss.
#
# Both dimensions come off the row rather than being asserted:
#
#   --geo-column kode_wilayah   the BPS code, which resolves against
#                               reference/geography/indonesia-regencies.csv.
#                               The code and not `kabupaten_kota`: two regencies
#                               are called Banjar and the name alone cannot tell
#                               a kabupaten from a kota.
#   --commodity-column komoditas  Kemendag's own Indonesian labels, aliased onto
#                                 the commodity registry so `Beras Medium` and
#                                 BI's `Beras Kualitas Medium I` stay the
#                                 separate goods they are.
#
# `--number-format id` matters more here than anywhere else in the repository.
# The ministry writes forty-one thousand five hundred rupiah as `41.500`, and
# read as an English decimal that is 41.5 — a thousandfold error that would look
# entirely plausible in a chart. Every value in the file matches
# `\d{1,3}(\.\d{3})*`, so there is no decimal point to lose.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a day's ingestion replaces that series' observations rather than
# appending.

set -euo pipefail

cd "$(dirname "$0")/.."

DATASET=sp2kp-food-prices
SOURCE=kemendag-sp2kp-prices

# series key | value column | what it is called
SERIES=(
  "sp2kp_price|price|Food price by regency — SP2KP"
  "sp2kp_price_ceiling|ceiling|Government price ceiling (HET/HA) — SP2KP"
)

for entry in "${SERIES[@]}"; do
  IFS="|" read -r key column name <<<"$entry"
  uv --project pipelines run terusan silver normalize "$key" \
    --dataset "$DATASET" \
    --source "$SOURCE" \
    --period-column date \
    --value-column "$column" \
    --geo-column kode_wilayah \
    --commodity-column komoditas \
    --unit "IDR/kg" \
    --number-format id \
    --name "$name" \
    "$@"
done
