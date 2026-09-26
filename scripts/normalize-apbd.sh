#!/usr/bin/env bash
#
# DJPK's APBD, Bronze into Silver.
#
# One call for all three collections — the national total, each province's
# governments summed, and each government on its own — because the indicators
# table is replaced one source at a time, and three calls would each delete
# the other two's series.
#
# The extractor has already named each record's indicator, series and place,
# so this is the long-format mapping: one indicator per `indicator` value. The
# place is a reference id where the extractor could resolve one — DJPK
# numbers its provinces its own way — and the name DJPK printed where it
# could not, which Silver reports as unresolved rather than guessing.
#
# Values are written with a decimal point and no grouping, so `en`.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --source djpk-apbd \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column geo \
  --name-column series_name \
  --code-column line \
  --publisher-column publisher \
  --number-format en \
  "$@"
