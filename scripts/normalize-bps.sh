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
# how the first attempt ended. So the variables are read in batches, each with
# `--include var_id=…`, which filters in the scan. A batch adds its series'
# names to the indicators table rather than replacing it.
#
# Batches are packed by Bronze rows (BPS_NORMALIZE_ROWS, 200,000 by default),
# not by a count of variables. Variables differ in size by four orders of
# magnitude — a regency table of every month holds 130,000 rows, a national
# yearly figure a dozen — so a batch of 75 variables was 5,000 rows or over a
# million depending on where in the catalogue it fell. On the NAS the large
# ones were stopped by earlyoom, which sends SIGTERM rather than letting the
# kernel's OOM killer in, and a process stopped that way writes no summary to
# the run journal: three batches' series were simply absent. A variable larger
# than the budget gets a batch of its own.
#
# Idempotent: normalization rebuilds each indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

BUDGET="${BPS_NORMALIZE_ROWS:-200000}"

# Every variable Bronze holds at the current parser version, with its size,
# packed into batches in the scan so nothing else is loaded. One line per
# batch, the variable ids comma-separated. Without its progress bar: on a slow
# machine DuckDB draws one on stdout, and the bar's characters become ids.
BATCHES=$(BUDGET="$BUDGET" uv --project pipelines run python -c '
import os
import duckdb
duckdb.sql("SET enable_progress_bar = false")
from terusan_pipelines.extract.base import PARSER_VERSION
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
pattern = StorageResolver(StorageConfig()).glob(Layer.BRONZE, "records")
rows = duckdb.sql(
    "SELECT CAST(columns[$$var_id$$] AS INTEGER) v, count(*) n FROM read_parquet($p, "
    "union_by_name=true, hive_partitioning=true) "
    "WHERE source_id = $$bps-indicators$$ AND parser_version = $version "
    "AND columns[$$var_id$$] IS NOT NULL GROUP BY 1 ORDER BY 1",
    params={"p": pattern, "version": PARSER_VERSION},
).fetchall()
budget = int(os.environ["BUDGET"])
batch, size = [], 0
for var_id, count in rows:
    if batch and size + count > budget:
        print(",".join(batch))
        batch, size = [], 0
    batch.append(str(var_id))
    size += count
if batch:
    print(",".join(batch))
')

mapfile -t batches <<<"$BATCHES"
echo "normalizing BPS in ${#batches[@]} batches of at most $BUDGET Bronze rows"

# A batch with a failing series exits non-zero after normalizing the rest of
# its series; the other batches still run, and the script fails at the end.
# A batch that was stopped outright — exit 143 for SIGTERM, 137 for SIGKILL —
# is named, because it left nothing in the journal to say which it was.
failed=0
for ((i = 0; i < ${#batches[@]}; i++)); do
  status=0
  uv --project pipelines run terusan silver normalize-each \
    --by indicator \
    --dataset bps-indicators \
    --source bps-indicators \
    --include "var_id=${batches[i]}" \
    --exclude period_kind=summary \
    --period-column period \
    --value-column value \
    --geo-column geo \
    --unit-column unit \
    --name-column series_name \
    --code-column series_code \
    --number-format en \
    "$@" || status=$?
  if ((status)); then
    failed=$((failed + 1))
    if ((status >= 128)); then
      echo "batch $((i + 1)) was stopped by signal $((status - 128)): var_id=${batches[i]}" >&2
    fi
  fi
done

if ((failed)); then
  echo "$failed batch(es) had series that failed to normalize; see the run journal" >&2
  exit 1
fi
