#!/usr/bin/env bash
#
# The Jakarta Composite Index, Bronze into Silver.
#
# Landed by the same source family as the commodity futures and deliberately
# not in `normalize-yahoo.sh`: an equity index is not a commodity, so it takes
# no `--commodity` and must not reach the commodity dimension — and it has a
# volume series the futures do not.
#
# The unit is asserted here rather than read from the response, which is the
# opposite of what the commodities do and for a reason. Yahoo states IHSG's
# currency as IDR, which is true of the shares and false of the index: the
# index is a number against a 1982 base, not an amount of rupiah, and filing it
# as currency would invite a reader to convert it. Volume is shares, which the
# response does not say at all.
#
# Until now this series was normalized by whoever remembered to, by hand, which
# is why its names read `Ihsg close` — derived from the key because no `--name`
# was passed. They are set properly below.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a day's ingestion replaces that series' observations rather than
# appending.

set -euo pipefail

cd "$(dirname "$0")/.."

DATASET=ihsg
SOURCE=yahoo-ihsg

# series key | value column | unit | what it is called
SERIES=(
  "ihsg_open|open|index|Jakarta Composite Index, open"
  "ihsg_high|high|index|Jakarta Composite Index, high"
  "ihsg_low|low|index|Jakarta Composite Index, low"
  "ihsg_close|close|index|Jakarta Composite Index, close"
  "ihsg_volume|volume|shares|Jakarta Composite Index, shares traded"
)

for entry in "${SERIES[@]}"; do
  IFS="|" read -r key column unit name <<<"$entry"
  uv --project pipelines run terusan silver normalize "$key" \
    --dataset "$DATASET" \
    --source "$SOURCE" \
    --period-column date \
    --value-column "$column" \
    --unit "$unit" \
    --number-format en \
    --name "$name" \
    "$@"
done
