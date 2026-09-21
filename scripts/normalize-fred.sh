#!/usr/bin/env bash
#
# FRED's Indonesian series, Bronze into Silver.
#
# One invocation, not one per series. FRED's crawl holds six hundred-odd series
# under one dataset, all the same shape — a date, a figure, and the units the
# search listing stated — so they differ only in which series a row belongs to.
# `normalize-each` reads Bronze once and produces one indicator per distinct
# value of the `indicator` column, which the extractor composed from the
# series' title and its FRED id.
#
# The mapping itself is four columns:
#
#   period  the observation date restated at the series' declared frequency,
#           so a quarterly figure reads as 1952-Q2 rather than as a single day
#   value   the figure, in FRED's Anglophone notation — hence --number-format en
#   units   stated per series, not per dataset: one is in rupiah, the next in
#           percent, and asserting one unit for both would be wrong by orders
#           of magnitude
#   country the place the series is about, where its title says so. A state's
#           exports *to* Indonesia are that state's figures, and the extractor
#           leaves those blank rather than filing them under Indonesia.
#
# The indicators table is published alongside the figures, and described from
# the series pages rather than from the figures: FRED's identifiers are
# eight-character codes — its titles are too long for a URL and too alike to
# shorten — so the title is what a reader sees, the FRED series id is what they
# take back to FRED, and the page's notes are what say what the series counts.
# Those notes live in their own dataset, one row per series, because copying a
# paragraph onto each of a quarter-million observations says the same thing six
# hundred times.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after an ingestion replaces that series' observations rather than appending a
# second copy.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="fred-indonesia"
DATASET="fred-indonesia-series"
PAGES="fred-indonesia-series-pages"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column period \
  --value-column value \
  --unit-column units \
  --geo-column country \
  --name-column title \
  --code-column series_id \
  --describe-dataset "$PAGES" \
  --description-column notes \
  --publisher-column publisher \
  --release-column release \
  --number-format en
