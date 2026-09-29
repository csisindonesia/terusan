#!/usr/bin/env bash
#
# The health ministry's workforce planning dashboard, Bronze into Silver.
#
# SATUSEHAT's "Perencanaan Tenaga Medis dan Tenaga Kesehatan": health workers
# registered in SISDMK, facility vacancies and minimum-standard shortfalls,
# and the supply-demand projection of need to 2032. The dashboard's tables are
# recast by `extract/kemkes_workforce.py` as one figure per row, so the mapping
# below is plain:
#
#   period      the SISDMK snapshot (`YYYY-MM-DD`, the first of a month) for
#               the register and the vacancy tables; the projection year
#               (`YYYY`) for need, existing and gap
#   geo         the province, qualified with `Provinsi` so Gorontalo is not
#               the regency, or `Indonesia`: the register family summed, the
#               vacancy tables' own `Total` row, the projection's own summary
#   category    the profession as the dashboard labels it; in the register,
#               named with its family ("Bidan — Bidan Desa"), because the
#               dashboard files a few professions under two. Blank on a
#               vacancy table's total. The register has no total of its own:
#               each artifact is one family, and no reader of one sees them all
#   unit        persons
#
# The workload analysis stays in Bronze: its pages do not say which year the
# need was computed for.
#
# `--number-format en`: every figure is plain digits by the time it reaches
# Bronze (the summary's `146.119` is ungrouped in the extractor), so either
# format reads them the same; `en` is named so nothing is left to a default.
#
# Idempotent: normalization rebuilds each indicator from scratch. Each month's
# register and vacancy tables are a period of their own; a projection landed
# again in a later month is the same period, and the later retrieval supersedes
# the earlier one.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --name-column name \
  --dataset kemkes-sdmk-figures \
  --source kemkes-sdmk-planning \
  --period-column period \
  --value-column value \
  --geo-column geo \
  --category-column category \
  --unit-column unit \
  --publisher-column publisher \
  --number-format en \
  "$@"
