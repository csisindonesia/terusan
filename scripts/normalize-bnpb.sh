#!/usr/bin/env bash
#
# BNPB's disaster impact, Bronze into Silver.
#
# One series per measure and hazard: deaths from floods, houses damaged by
# earthquakes, people displaced by volcanic eruptions. That split is BNPB's
# own — it publishes one table per measure with a column per hazard — and it is
# the split that matters, because a drought and a tsunami affect people in ways
# no single number describes. `extract/bnpb_impact.py` turns each of those rows
# into one record per hazard and names the series; by the time Bronze is
# reached, `indicator` is an ordinary column and the mapping below is plain:
#
#   period            the year, which is in neither the file nor its title —
#                     it is the CKAN dataset the table was published under,
#                     carried through the landing partition
#   province_code     the BPS code, corrected: BNPB numbers the six Papua
#                     provinces created in 2022 in an order of its own, so
#                     joining on the code it prints files Papua Tengah's
#                     casualties under Papua
#   unit              people, houses, bridges, events — one table counts
#                     buildings and the next counts the dead, so the unit
#                     travels per row rather than being asserted here
#
# `--number-format en` rather than auto: BNPB writes a zero count as `0.0`, and
# read the Indonesian way a decimal point is a thousands separator.
#
# Idempotent: normalization rebuilds each indicator from scratch, so re-running
# after an ingestion replaces those observations rather than appending. The
# same figures reaching it twice — BNPB republishes its 2024 tables inside the
# all-years dataset — collapse to one observation per province and year.

set -euo pipefail

cd "$(dirname "$0")/.."

# `normalize-each` rather than 135 `normalize` calls: Bronze is read once, and
# it is the command that publishes the indicators table. Without that a reader
# searching for disaster deaths finds a code.
uv --project pipelines run terusan silver normalize-each \
  --by indicator \
  --name-column name \
  --dataset bnpb-impact \
  --source bnpb-disaster \
  --period-column period \
  --value-column value \
  --geo-column province_code \
  --unit-column unit \
  --publisher-column publisher \
  --number-format en \
  "$@"
