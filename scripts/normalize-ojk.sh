#!/usr/bin/env bash
#
# OJK's banking statistics, Bronze into Silver.
#
# The series the Statistik Perbankan Indonesia printed, from July 2025 onward:
# commercial bank deposits by type and by currency, credit by purpose, by
# orientation and by business size, non-performing credit by grade, and rural
# bank deposits. OJK's SJK portal publishes cubes rather than tables, so
# `extract/ojk_sjk.py` sums each month of a cube into these series; by the
# time Bronze is reached every row is already one figure, and the mapping
# below is plain:
#
#   period      the month, `YYYY-MM`, which is the cube's own `Bulan`
#   geo         the province, qualified with `Provinsi` so Gorontalo is not
#               the regency, or `Indonesia` for the whole country. The
#               country is summed from every row, so the provinces fall short
#               of it by what banks did not place in a province
#   category    the breakdown as OJK labels it, numbering and all
#               (`1. Giro`, `003. Kurang Lancar`); blank on a series' total
#   unit        Rupiah, as the portal reports them, not the SPI's billions
#
# Commercial banks report on 34 provinces, so their Papua and Papua Barat are
# the territory before the 2022 split and stay unresolved rather than being
# filed under the smaller provinces of today.
#
# `--number-format en`: the sums are written with a decimal point, and read
# the Indonesian way a decimal point is a thousands separator.
#
# Idempotent: normalization rebuilds each indicator from scratch. A month OJK
# revised lands as a second artifact, and the later retrieval supersedes the
# earlier one.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --name-column name \
  --dataset ojk-banking-figures \
  --source ojk-banking-sjk \
  --period-column period \
  --value-column value \
  --geo-column geo \
  --category-column category \
  --unit-column unit \
  --publisher-column publisher \
  --number-format en \
  "$@"
