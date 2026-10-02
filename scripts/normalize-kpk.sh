#!/usr/bin/env bash
#
# KPK's enforcement statistics, Bronze into Silver.
#
# `extract/kpk_penindakan.py` reads the page's seven tables into
# `kpk-penindakan-figures`, one figure per row, so the mapping below is plain:
#
#   period      the year. The current year is year to date, which `release`
#               says (`Per-Maret 2026`); a later quarter's page supersedes it
#               in Silver by retrieval
#   geo         `Indonesia` throughout: the caseload is national, and the
#               region table's rows are island groups, the central government
#               and two foreign countries, so they are categories, not places
#   category    the row as KPK printed it: the stage, agency, post, offence,
#               region, court or sex. KPK's totals are not read
#   unit        `cases`, or `persons` for the tables of defendants
#
# `--number-format id`: the page is Indonesian, and a four-digit count written
# with a separator would be written `1.907`.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --name-column name \
  --dataset kpk-penindakan-figures \
  --source kpk-penindakan \
  --period-column period \
  --value-column value \
  --geo-column geo \
  --category-column category \
  --unit-column unit \
  --release-column release \
  --publisher-column publisher \
  --number-format id \
  "$@"
