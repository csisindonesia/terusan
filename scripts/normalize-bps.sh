#!/usr/bin/env bash
#
# BPS's national catalogue, Bronze into Silver — one indicator per series.
#
# `bps-indicators` lands a table per variable and three years, for every
# variable in the national catalogue (some 1,750). The extractor reads each into
# one record per row, breakdown and period, and names the series each record
# belongs to: the variable, its breakdown (urban, rural, male, female, the
# total), and — for a table whose rows are not places, such as age groups or
# sectors — the row. That name is `indicator`, so this is one pass of
# `normalize-each` rather than a mapping per series: Bronze is read once, and a
# variable BPS adds is published the next run without anyone writing a line.
#
# **Places.** Tables of provinces and cities resolve on `geo`, BPS's code
# rewritten as the registry keys it (`11`, `11.06`, `IDN`), or BPS's name for
# the place where its code cannot be trusted — the 150-city inflation table
# numbers its cities 1 to 151, and BPS and Kemendagri number the regencies of
# the post-2022 Papua provinces differently (see extract/bps.py). Tables of
# anything else are national: `geo` is `IDN`.
#
# **One resolution per series.** Where BPS publishes a figure twice a year, or
# monthly, it also publishes `Tahunan` — one annual figure, from the years
# before the second survey or as a summary of the months. An annual 2008
# beside a March 2009 charts as one line changing frequency halfway, so those
# rows are marked `period_kind=summary` at extraction and left out here. They
# stay in Bronze.
#
# **Units** are BPS's own (`Persen`, `Ribu Jiwa`, `Rupiah`), and blank where it
# says `Tidak Ada Satuan`.
#
# These overlap Kemendagri's `provincial_*` series (scripts/normalize-
# kemendagri.sh), which republish BPS's inflation, unemployment and growth.
# BPS is the publisher.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset bps-indicators \
  --source bps-indicators \
  --exclude period_kind=summary \
  --period-column period \
  --value-column value \
  --geo-column geo \
  --unit-column unit \
  --name-column series_name \
  --code-column series_code \
  --number-format en \
  "$@"
