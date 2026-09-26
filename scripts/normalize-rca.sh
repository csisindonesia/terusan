#!/usr/bin/env bash
#
# Revealed comparative advantage, Bronze into Silver: the HS6 base years and
# WITS's sector series.
#
# Three collections, one mapping. Every extractor involved already named the
# indicator each row belongs to, so each is a `normalize-each` over that
# column:
#
#   rca-atlas-hs6      the Atlas of Economic Complexity, summed per
#                      environmental goods list: exports, RCA of the list as a
#                      basket, products with RCA ≥ 1 — and ECI, COI, diversity
#                      and the growth projection. Indonesia, ASEAN and peers,
#                      1995–2024. From `rca-seed`, landed once.
#   rca-indonesia-hs6  Indonesia alone from WITS HS6 trade, 1995–2025: the same
#                      lists' exports, export shares and product counts. No
#                      basket RCA — the file holds no row for a product
#                      Indonesia did not export, so the world's side of the
#                      basket is incomplete. From `rca-seed`, landed once.
#   tradestats         WITS's own RCA, exports and export shares by HS section,
#                      SITC group and stage of processing. From
#                      `wits-tradestats`, refreshed monthly: this is the part
#                      that keeps moving.
#
# The reporter is an ISO3 code on every row and resolves through
# reference/geography/countries.csv, so one indicator holds Indonesia and its
# peers side by side.
#
# Idempotent: normalization rebuilds an indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

normalize() {
  local source="$1"
  shift
  uv --project pipelines run terusan silver normalize-each \
    --by indicator \
    --source "$source" \
    --period-column year \
    --value-column value \
    --unit-column unit \
    --geo-column reporter \
    --name-column series_name \
    --code-column measure \
    --publisher-column publisher \
    --number-format en \
    "$@"
}

# Both seed collections in one pass. The indicators table is replaced one
# source at a time, so two passes over `rca-seed` would leave only the second
# collection's series named; each series carries its own collection instead.
normalize rca-seed "$@"
normalize wits-tradestats "$@"
