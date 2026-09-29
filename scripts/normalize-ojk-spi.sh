#!/usr/bin/env bash
#
# OJK's Statistik Perbankan Indonesia archive, Bronze into Silver.
#
# The province tables of every monthly edition until June 2025, after which
# the same series come from the SJK portal (`scripts/normalize-ojk.sh`).
# `extract/ojk_spi.py` reads each edition's own month out of its workbook, so
# by the time Bronze is reached every row is one figure and the mapping below
# is plain:
#
#   period      the month, `YYYY-MM`, the edition's own
#   geo         the province, qualified with `Provinsi`, or `Indonesia` for the
#               table's own total row. The provinces fall short of it by what
#               OJK could not place (`Lainnya`, `Tidak Teridentifikasi`)
#   category    the breakdown, labelled as the SJK cubes label it so a series
#               reads the same on both sides of July 2025; blank on the total
#   unit        `IDR billion` as the SPI printed it, or `offices`
#
# The SPI reports on 34 provinces, so its Papua and Papua Barat are the
# territory before the 2022 split and stay unresolved.
#
# `--number-format en`: the workbook's cells are floats written with a decimal
# point, which read the Indonesian way would be a thousands separator.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --name-column name \
  --dataset ojk-spi-figures \
  --source ojk-banking-spi \
  --period-column period \
  --value-column value \
  --geo-column geo \
  --category-column category \
  --unit-column unit \
  --publisher-column publisher \
  --number-format en \
  "$@"
