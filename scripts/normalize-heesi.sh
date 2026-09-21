#!/usr/bin/env bash
#
# The HEESI handbook's tables, Bronze into Silver.
#
# Fourteen tables out of the ESDM handbook — coal supply and domestic sales,
# fuel and electricity sales, generating capacity and production, oil and LPG
# supply and demand, energy prices, and the national energy balance. The
# extractor composed one indicator per table column, so this is one
# `normalize-each` over that column.
#
#   year     the figure's year, which is the handbook's own period
#   value    in the handbook's Anglophone notation — hence --number-format en
#   unit     stated per table, not per handbook: tons, gigawatt-hours, dollars
#            per barrel and thousands of barrels of oil equivalent all appear
#   country  Indonesia throughout
#
# `--exclude is_total=1` drops the columns the handbook prints as the sum of
# the others. They are real published figures and the extractor keeps them, but
# normalized beside their parts they double the table — so they are left out
# here and can be read back out of Bronze when a total is what is wanted.
#
# Idempotent: re-running after a new edition replaces each series rather than
# appending a second copy of the years the editions share.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="esdm-heesi"
DATASET="handbook"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column year \
  --value-column value \
  --unit-column unit \
  --geo-column country \
  --name-column series_name \
  --code-column data_id \
  --publisher-column publisher \
  --number-format en \
  --exclude is_total=1
