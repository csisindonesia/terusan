#!/usr/bin/env bash
#
# Re-derive the instrument of every regulation already in silver, in place.
#
# The same rule scripts/import-regulations.sh now applies at import (see
# sql/silver/regulation_instrument.sql), for a lake whose regulations were
# landed before it existed — without re-reading the corpus, which may not be
# on the machine holding the lake. Only `instrument` changes; every row and
# every other column is carried across as it was, and the dataset is written
# beside the old one and swapped in only once its row count matches.
#
#   ./scripts/fix-regulation-instruments.sh
#   STORAGE_ROOT=/data ./scripts/fix-regulation-instruments.sh

set -euo pipefail

cd "$(dirname "$0")/.."

STORAGE_ROOT="${STORAGE_ROOT:-./.data}"
SILVER="$STORAGE_ROOT/silver"
NEW="$SILVER/.regulations-fixing"
OLD="$SILVER/.regulations-before-fix"

rm -rf "$NEW"

plan="$(mktemp -t terusan-fix-XXXXXX.sql)"
trap 'rm -f "$plan"' EXIT
{
  cat sql/silver/regulation_instrument.sql
  cat <<SQL
COPY (
  SELECT * REPLACE (regulation_instrument(title, instrument, track, region_type) AS instrument)
  FROM read_parquet('$SILVER/regulations/**/*.parquet', hive_partitioning = true)
) TO '$NEW'
  (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (track), OVERWRITE, FILENAME_PATTERN 'part-{i}');
SQL
} > "$plan"
duckdb -f "$plan"

before=$(duckdb -csv -noheader -c "SELECT count(*) FROM read_parquet('$SILVER/regulations/**/*.parquet')")
after=$(duckdb -csv -noheader -c "SELECT count(*) FROM read_parquet('$NEW/**/*.parquet')")
if [ "$before" != "$after" ]; then
  echo "row counts differ ($before before, $after after); leaving the old dataset in place" >&2
  exit 1
fi

rm -rf "$OLD"
mv "$SILVER/regulations" "$OLD"
mv "$NEW" "$SILVER/regulations"
rm -rf "$OLD"

echo "re-derived the instrument of $after regulations"
duckdb -c "
SELECT track, instrument, count(*) AS rows
FROM read_parquet('$SILVER/regulations/**/*.parquet', hive_partitioning = true)
WHERE instrument IN ('UU', 'PP', 'Perpres', 'Permen')
GROUP BY ALL ORDER BY track, rows DESC;"
