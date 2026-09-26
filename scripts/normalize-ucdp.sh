#!/usr/bin/env bash
#
# UCDP's organized violence figures for Indonesia, Bronze into Silver.
#
# Two dozen series out of one country-year table: deaths in state-based,
# intrastate, interstate, non-state and one-sided violence, the three together,
# who the dead were, and how many pairs of actors were fighting. Each is a
# column of the table, and the extractor already named the indicator each row
# belongs to — so this is `normalize-each` over that column rather than
# twenty-four invocations reading the same Bronze partition twenty-four times.
#
# The mapping is four columns:
#
#   year         the year the row is about, which is the period
#   value        a count of the dead, or of active dyads — integers, but
#                --number-format en anyway, so that the day UCDP publishes a
#                decimal it is not read as a thousands separator
#   unit         deaths for most series, dyads for the three counts of how many
#                conflicts were running. Asserting one unit would say twelve
#                people were killed where twelve conflicts were fought
#   country      always Indonesia — the extractor keeps no other country — and
#                resolving it is what files the observations under IDN
#
# The low and high estimates are their own indicators, not a bound on one
# series, because Silver observations carry a value and not a range. Charting
# `ucdp_state_based_deaths` alone states a precision UCDP does not claim, so the
# three are published together and named so they read as one range.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a new release replaces the series rather than appending a second copy.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="ucdp-organized-violence"
DATASET="organized-violence"

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
  --number-format en \
  "$@"
