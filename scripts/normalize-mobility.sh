#!/usr/bin/env bash
#
# Meta's movement distribution, Bronze into Silver.
#
# Four series — the share of movements staying put, going under 10 km, going
# 10–100 km, and going further — each observed per Indonesian district per day.
# The extractor kept the Indonesian rows out of a global release and named the
# series after the distance band, so this is one `normalize-each` over that
# column.
#
#   period   the day Meta reports, as published
#   value    a fraction between 0 and 1 in Anglophone notation — hence
#            --number-format en. It is a share of one district's movements, so
#            summing the four bands for a district and day gives about 1
#   district the place, as `Bogor (IDN.9.4_1)`. These are regencies and cities,
#            which the geography registry does not hold yet, so the GADM code
#            travels with the name: without it a bare `Gorontalo` resolves to
#            the province of that name, and `Gorontalo` and `Kota Gorontalo`
#            collapse onto one observation. They stay unresolved until
#            regencies are registered, which keeps the gap visible
#
# Idempotent per release: normalization rebuilds each series from whatever
# Bronze holds, so a re-pull of an overlapping date range replaces rather than
# duplicates.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="hdx-meta-movement-distribution"
DATASET="movement-distribution"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column district \
  --name-column series_name \
  --code-column category \
  --publisher-column publisher \
  --number-format en
