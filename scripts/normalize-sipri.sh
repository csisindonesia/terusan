#!/usr/bin/env bash
#
# SIPRI's Milex figures for Indonesia, Bronze into Silver.
#
# Six series out of one workbook: military expenditure in constant and current
# US dollars, in rupiah, per capita, as a share of GDP, and as a share of
# government spending. Each is a sheet, and the extractor already named the
# indicator each row belongs to — so this is `normalize-each` over that column
# rather than six invocations reading the same Bronze partition six times.
#
# The mapping is four columns:
#
#   year         the column header the figure sat under, which is the period
#   value        the figure, in SIPRI's Anglophone notation — hence
#                --number-format en, since `1.234` there is one point two
#   unit         stated per series, not per dataset: one is US$ m., the next
#                rupiah, and the two share series are fractions of 1 rather
#                than percentages. Asserting one unit would be wrong by twelve
#                orders of magnitude in one direction and a hundred in another
#   country      always Indonesia — the extractor keeps no other country — and
#                resolving it is what files the observations under IDN
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a new release replaces the series rather than appending a second copy.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="sipri-milex"
DATASET="milex"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column year \
  --value-column value \
  --unit-column unit \
  --geo-column country \
  --name-column series_name \
  --code-column measure \
  --publisher-column publisher \
  --number-format en
