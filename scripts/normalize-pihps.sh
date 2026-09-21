#!/usr/bin/env bash
#
# PIHPS food prices, Bronze into Silver.
#
# Bank Indonesia prices the same ten foods in four different markets, and the
# whole reason this is a script rather than one `normalize` call is that those
# four are four series, not four readings of one. A farmgate price and a
# supermarket price for the same kilo of rice answer different questions and
# differ by a wide margin; averaged together they answer neither. So each
# market becomes its own indicator, and the mapping narrows to it on the
# `price_type` column the extractor carries.
#
# That column exists because the market type is a request parameter, not
# something the portal's response says. The same goes for the province — see
# `extract/pihps.py`, which reads both back off the landing record. By the time
# Bronze is reached they are ordinary columns, which is what lets the mapping
# below be as plain as it is:
#
#   geo         the place, `Indonesia` or `Provinsi Jawa Barat`, resolved
#               through reference/geography/indonesia-provinces.csv
#   commodity   the food, `Beras` or `Beras Kualitas Medium I`, resolved
#               through reference/commodities/commodities.csv — the category
#               and its varieties are separate commodities, because the
#               category's price is the average of the varieties beneath it
#               and a total over both double-counts
#   unit        IDR/kg, which every one of the thirty-one is quoted in
#   dd/mm/yyyy  the dates, which are the grid's own columns — hence
#               `--period-columns` rather than a list that would need editing
#               every day
#
# `--number-format en` rather than auto: the portal renders sixteen thousand
# three hundred and fifty rupiah as `16,350`, and read the Indonesian way that
# is 16.35 — a thousandfold error that looks entirely plausible in a column of
# prices.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a day's ingestion replaces that series' observations rather than
# appending.

set -euo pipefail

cd "$(dirname "$0")/.."

# `normalize-each` rather than four `normalize` calls: it is the command that
# publishes the indicators table, and a series with figures but no row there has
# no name in the portal — a reader searching for food prices finds nothing. The
# split is on the `indicator` column the extractor writes, one value per market.
uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --name-column series_name \
  --dataset food-prices \
  --source bi-pihps-food-prices \
  --period-columns \
  --geo-column geo \
  --commodity-column commodity \
  --unit-column unit \
  --number-format en \
  "$@"
