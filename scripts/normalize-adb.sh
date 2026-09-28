#!/usr/bin/env bash
#
# ADB's Key Indicators for Indonesia, Bronze into Silver.
#
# Some five hundred and fifty series in one collection, each read by the same
# mapping, so this is `normalize-each` over the identifier the extractor
# derived from ADB's code rather than one invocation per series.
#
#   period      the year. KIDB is annual only
#   value       as published, --number-format en: KIDB writes `1389.76985`
#   unit        ADB's unit code with its multiplier folded in — `IDR trillion`,
#               `USD million`, `PCT`. The value is not rescaled
#   country     `Indonesia`; ADB's own code is `INO`, which the registry does
#               not resolve
#   publisher   who ADB compiled the figure from — BPS, Bank Indonesia, the
#               World Bank — which is not ADB
#
# The names and definitions come from the codelist collection, one row per
# code, so a paragraph of definition is not held on every observation.
#
# Idempotent: each indicator is rebuilt from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="adb-kidb"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset adb-key-indicators \
  --source "$SOURCE" \
  --period-column period \
  --value-column value \
  --unit-column unit \
  --geo-column country \
  --name-column title \
  --code-column code \
  --describe-dataset adb-key-indicators-codelist \
  --description-column notes \
  --publisher-column publisher \
  --number-format en \
  "$@"
