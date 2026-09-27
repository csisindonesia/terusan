#!/usr/bin/env bash
#
# ICE Newcastle thermal coal, Bronze into Silver.
#
# One series: the daily close. Trading Economics' market chart carries no open,
# high or low, so this is not the four-series shape the Yahoo instruments have.
# It files under `thermal-coal` beside the retired API2 series, so the
# Commodities page shows both — the European price that stopped in December
# 2025 and the Asian one that replaced it.
#
# Idempotent: normalization rebuilds an indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize newcastle_coal_price_close \
  --dataset newcastle-coal \
  --source tradingeconomics-coal \
  --period-column date \
  --value-column close \
  --unit-column unit \
  --commodity thermal-coal \
  --number-format en \
  --name "Thermal coal (ICE Newcastle) price — close" \
  "$@"
