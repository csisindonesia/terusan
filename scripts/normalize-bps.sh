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
# numbers its cities 1 to 151, and the regency registry keys several provinces
# by Kemendagri's numbering rather than BPS's, so a regency code is used only
# where the registry calls it by BPS's name (see extract/bps.py). Tables of
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
# **A slice at a time.** The whole history is six million Bronze rows, and
# `normalize-each` holds what it reads in memory — which on an 8 GB machine is
# how the first attempt ended. So the variables are read in batches
# (BPS_NORMALIZE_BATCH, 150 by default), each with `--include var_id=…`, which
# filters in the scan. A batch adds its series' names to the indicators table
# rather than replacing it.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

BATCH="${BPS_NORMALIZE_BATCH:-150}"

# Every variable Bronze holds, read in the scan so nothing else is loaded.
VARS=$(uv --project pipelines run python -c '
import duckdb
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
pattern = StorageResolver(StorageConfig()).glob(Layer.BRONZE, "records")
rows = duckdb.sql(
    "SELECT DISTINCT CAST(columns[$$var_id$$] AS INTEGER) v FROM read_parquet($p, "
    "union_by_name=true, hive_partitioning=true) "
    "WHERE source_id = $$bps-indicators$$ AND columns[$$var_id$$] IS NOT NULL ORDER BY 1",
    params={"p": pattern},
).fetchall()
print(" ".join(str(r[0]) for r in rows))
')

read -r -a ids <<<"$VARS"
echo "normalizing ${#ids[@]} BPS variables, $BATCH at a time"

# A batch with a failing series exits non-zero after normalizing the rest of
# its series; the other batches still run, and the script fails at the end.
failed=0
for ((i = 0; i < ${#ids[@]}; i += BATCH)); do
  batch=$(IFS=,; echo "${ids[*]:i:BATCH}")
  uv --project pipelines run terusan silver normalize-each \
    --by indicator \
    --dataset bps-indicators \
    --source bps-indicators \
    --include "var_id=$batch" \
    --exclude period_kind=summary \
    --period-column period \
    --value-column value \
    --geo-column geo \
    --unit-column unit \
    --name-column series_name \
    --code-column series_code \
    --number-format en \
    "$@" || failed=$((failed + 1))
done

if ((failed)); then
  echo "$failed batch(es) had series that failed to normalize; see the run journal" >&2
  exit 1
fi
