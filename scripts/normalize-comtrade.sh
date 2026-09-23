#!/usr/bin/env bash
#
# UN Comtrade's account of Indonesian trade, Bronze into Silver.
#
# Two series: what Indonesia exported to the world each year, and what it
# imported. Both in US dollars, both annual, both reported to the UN by
# Indonesia's customs administration and by its partners.
#
# The world total is what becomes a series, not the partner breakdown. The
# breakdown is in Bronze — two hundred partners a year, for whoever asks that
# question — but the comparable figure across years is the total, and the
# source asks Comtrade for it directly rather than picking it out of the
# breakdown. It has to: the preview endpoint caps a response at 500 rows, and
# for imports from 2021 on, the row summing the partners falls past the cap.
#
# The totals dataset is already narrowed to `partnerCode=0` and `motCode=0` by
# the request, so no filtering is needed here. Both mattered: Comtrade reports
# the world total once per mode of transport as well as across them, and 2021
# exports are 231.7 billion dollars all told, of which 219.3 billion went by
# sea.
#
# Why hold this at all when Kemendag publishes Indonesian trade: because this
# is the other side of the same shipments. Comtrade's figure is assembled from
# what every customs authority reported; Kemendag's is what Indonesia's
# reported. Where the two disagree, the disagreement is the finding.
#
# The reporter is Indonesia on every row, and the public endpoint says so
# nowhere: `reporterISO` comes back empty and `reporterCode` is the UN's 360.
# So the country is declared with `--geo`, which is what that flag is for.
#
# Idempotent: normalization rebuilds an indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="comtrade-indonesia"

uv --project pipelines run terusan silver normalize comtrade_exports_total \
  --dataset trade-exports-total \
  --source "$SOURCE" \
  --period-column period \
  --value-column primaryValue \
  --geo IDN \
  --unit USD \
  --number-format en \
  "$@"

uv --project pipelines run terusan silver normalize comtrade_imports_total \
  --dataset trade-imports-total \
  --source "$SOURCE" \
  --period-column period \
  --value-column primaryValue \
  --geo IDN \
  --unit USD \
  --number-format en \
  "$@"
