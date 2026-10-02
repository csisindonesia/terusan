#!/usr/bin/env bash
#
# OJK's fintech lending (LPBBTI) statistics, Bronze into Silver.
#
# `extract/ojk_fintech.py` reads the workbook's nine province tables into
# `ojk-fintech-figures`, one figure per row, so the mapping below is plain:
#
#   period      the month, `YYYY-MM`. Every month a workbook holds, the
#               thirteen to its own; a later edition's restatement of a month
#               supersedes the earlier one in Silver by retrieval
#   geo         the province, qualified with `Provinsi`, or `Indonesia` for
#               the table's own `JUMLAH` row. The provinces fall short of it by
#               lending from abroad, which is in no province
#   unit        `IDR billion`, `accounts` or `entities` as each table counts,
#               and for TWP 90 `share of outstanding loans`: the fraction the
#               cell holds, 0.026, not the 2.60% the sheet displays
#
# The tables list all thirty-eight provinces, so Papua is the province after
# the 2022 split and resolves as one.
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
  --dataset ojk-fintech-figures \
  --source ojk-fintech-p2p \
  --period-column period \
  --value-column value \
  --geo-column geo \
  --unit-column unit \
  --publisher-column publisher \
  --number-format en \
  "$@"
