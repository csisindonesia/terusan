#!/usr/bin/env bash
#
# Air quality per province, computed in Earth Engine, Bronze into Silver.
#
# Eight series — Sentinel-5P's NO2, SO2, CO, ozone, formaldehyde and aerosol
# index, and CAMS's surface PM2.5 and PM10 — each observed per province and
# month. The source wrote one CSV per month with the indicator already named,
# so this is one `normalize-each` over that column.
#
#   period   the month, as `2026-08`
#   value    the province mean, in Anglophone notation — hence --number-format
#            en. Empty where no clear pixel fell on the province all month;
#            `pixels` in Bronze says how many did
#   geo_id   the province's registry id (`ID-31`), which resolves as itself, so
#            no name matching stands between a figure and its province
#
# Idempotent: normalization rebuilds each series from whatever Bronze holds,
# and a month recomputed later supersedes the earlier retrieval.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset air-quality \
  --source gee-air-quality \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column geo_id \
  --name-column series_name \
  --code-column pollutant \
  --publisher-column publisher \
  --number-format en
