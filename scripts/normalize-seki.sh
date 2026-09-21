#!/usr/bin/env bash
#
# SEKI's mapped series, Bronze into Silver.
#
# Two hundred-odd series out of six of Bank Indonesia's hundred-odd tables —
# the CPI and wholesale price indices, quarterly GDP by industry at current and
# constant prices, budget and actual central government expenditure, and export
# value by commodity. The extractor already named the indicator each figure
# belongs to, from `reference/seki/variables.csv`, so this is `normalize-each`
# over that column rather than two hundred invocations of `normalize`.
#
# The mapping is four columns:
#
#   period   the period the column header stands for, already restated by the
#            extractor at the series' own frequency — 2026-08 for a monthly
#            table, 2026-Q2 for a quarterly one, 2024 for a yearly one. SEKI
#            writes the year once per block of twelve columns, which is why
#            this cannot be read from the header alone in Silver
#   value    the figure, in SEKI's Anglophone notation — hence
#            --number-format en
#   unit     stated per series, not per table: index points, rupiah billions
#            and thousands of dollars all appear, and asserting one would be
#            wrong by orders of magnitude
#   country  Indonesia throughout, which is what files these under IDN
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a release replaces that series' observations rather than appending.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="bi-seki"
DATASET="seki-tables"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column country \
  --name-column series_name \
  --code-column code \
  --publisher-column publisher \
  --number-format en
