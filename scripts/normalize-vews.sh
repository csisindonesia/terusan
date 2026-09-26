#!/usr/bin/env bash
#
# VEWS's collective violence figures, Bronze into Silver.
#
# Ten series out of the yearly exports: how many incidents, how many people
# killed and injured, how many of them women and children, how much damaged and
# destroyed, and how often someone intervened. The extractor has already
# counted the incidents and named the indicator each figure belongs to — so
# this is `normalize-each` over that column rather than ten invocations reading
# the same Bronze partition ten times.
#
# The mapping is five columns:
#
#   year         the year the export covers, which is the period. Annual, and
#                only annual: a monthly count per province would be a handful
#                of incidents a month, and a series that is mostly zero says
#                less than the year does
#   value        a count — of incidents, of the dead, of damaged buildings —
#                so integers, but --number-format en anyway, so that a figure
#                is never read against Indonesian thousands separators
#   geo          `Indonesia` or `Provinsi <name>`. Qualified because the
#                registry refuses a bare `Gorontalo`, which names a province
#                and a regency inside it. Indonesia is the whole year, not the
#                sum of the provinces — see extract/vews.py
#   unit         incidents, deaths, people or structures. Asserting one unit
#                would say twelve people were killed where twelve buildings
#                were burnt
#   measure      VEWS's own column — `num_death`, `infra_destroyed` — which is
#                what a reader takes back to the codebook
#
# The incident-level collection is deliberately not normalized. An incident is
# not an observation: two brawls in one regency on one day are two facts, and
# no indicator, period and place tells them apart. It stays in Bronze, where a
# query can group it by actor, weapon or issue.
#
# Idempotent: normalization rebuilds an indicator from scratch, so re-running
# after a new year's export replaces the series rather than appending to it.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="vews-collective-violence"
DATASET="collective-violence-early-warning"

uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --dataset "$DATASET" \
  --source "$SOURCE" \
  --period-column year \
  --value-column value \
  --unit-column unit \
  --geo-column geo \
  --name-column series_name \
  --code-column measure \
  --publisher-column publisher \
  --number-format en \
  "$@"
