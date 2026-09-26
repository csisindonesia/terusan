#!/usr/bin/env bash
#
# Build the assistant's full-text index over the regulations, into gold.
#
# The assistant used to find a regulation by matching the question's words
# against titles with ILIKE, through hand-kept tables of acronyms and
# synonyms. Every word those tables missed was a silent miss: "PKPU syarat
# capres" found nothing, though PKPU 19/2023 Pasal 13 is exactly that. This
# index is what replaces the matching — BM25 over stemmed words, so
# "persyaratan", "syarat" and "bersyarat" are one term, and common words weigh
# what they are worth instead of being listed as stop words.
#
# Two fields are indexed, and scored separately by the API:
#
#   title   every regulation's title, subject and region — all tracks;
#   pasal   the text of each article, for the tracks in PASAL_TRACKS. The
#           central and ministerial corpora by default (about 790k articles);
#           the regional ones are four times that again and are titled well
#           enough to be found by their titles.
#
# It lands as one gold dataset of four parts, each a directory of Parquet:
#
#   regulation_search/postings   term, field, key, pasal, tf, dl — sorted by
#                                term, so a lookup reads a few row groups
#   regulation_search/vocab      word → term, the stemmer's answer for every
#                                word the corpus uses, so the API needs no
#                                stemmer of its own
#   regulation_search/docs       key, track, year, instrument, title, region,
#                                subject, status — what a hit is shown with
#   regulation_search/stats      units and mean length per field, for BM25
#
# Terms in more than MAX_DF of a field's units are left out: "peraturan" is in
# every title and "ayat" in most articles, and a term that matches everything
# ranks nothing.
#
# Rebuilt whole beside the old index and swapped in once written, so a
# running API never reads half of one. Re-run it after every
# scripts/import-regulations.sh; that script runs it itself.
#
#   ./scripts/index-regulations.sh
#   STORAGE_ROOT=/data PASAL_TRACKS=pusat,kementerian,perda ./scripts/index-regulations.sh

set -euo pipefail

cd "$(dirname "$0")/.."

STORAGE_ROOT="${STORAGE_ROOT:-./.data}"
SILVER="$STORAGE_ROOT/silver"
GOLD="$STORAGE_ROOT/gold"
OUT="$GOLD/regulation_search"
NEW="$GOLD/.regulation_search-building"
OLD="$GOLD/.regulation_search-before"
PASAL_TRACKS="${PASAL_TRACKS:-pusat,kementerian}"
MAX_DF="${MAX_DF:-0.3}"
MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-8GB}"

for dataset in regulations regulation_sections; do
  if ! compgen -G "$SILVER/$dataset/*" >/dev/null; then
    echo "no $dataset under $SILVER — run scripts/import-regulations.sh first" >&2
    exit 1
  fi
done

# 'pusat,kementerian' as a SQL list.
tracks="$(printf "'%s'," ${PASAL_TRACKS//,/ })"
tracks="${tracks%,}"

rm -rf "$NEW"
mkdir -p "$NEW"/{postings,vocab,docs,stats}

plan="$(mktemp -t terusan-index-XXXXXX.sql)"
trap 'rm -f "$plan"' EXIT

cat > "$plan" <<SQL
SET memory_limit='$MEMORY_LIMIT';
SET preserve_insertion_order=false;
INSTALL fts;
LOAD fts;

-- Every word of every unit. Split on anything that is not a letter or a
-- digit, which is also how the API splits a question: the two must agree, or
-- a word the reader typed is looked up in a form the index never stored.
-- Title units carry an empty pasal rather than a null, so they join.
CREATE TEMP TABLE words AS
WITH units AS (
  SELECT 'title' AS field, key, '' AS pasal,
         lower(concat_ws(' ', title, subject, region_name)) AS text
  FROM read_parquet('$SILVER/regulations/**/*.parquet', union_by_name = true, hive_partitioning = true)
  UNION ALL
  SELECT 'pasal', key, coalesce(pasal, ''), lower(text)
  FROM read_parquet('$SILVER/regulation_sections/**/*.parquet', union_by_name = true, hive_partitioning = true)
  WHERE kind = 'pasal' AND track IN ($tracks)
    -- Not the definitions. "Dalam Peraturan ini yang dimaksud dengan: 1.
    -- Komisi Pemilihan Umum ... 2. Presiden ..." names every term the
    -- regulation uses, so it matched every question better than the article
    -- that answers it: PKPU 19/2023's best article for "syarat capres" was
    -- Pasal 1 rather than Pasal 13. The bab is matched with its letters
    -- only, since the parse spells it KETENTUANUMUM as often as not.
    AND regexp_replace(upper(coalesce(bab_title, '')), '[^A-Z]', '', 'g') <> 'KETENTUANUMUM'
    AND left(text, 200) NOT ILIKE '%yang dimaksud dengan%'
    -- Nor the closing provisions, for the same reason: "Pada saat Peraturan
    -- Komisi ini mulai berlaku, Peraturan KPU Nomor 22 Tahun 2018 tentang
    -- Pencalonan ... Presiden ... dicabut" quotes the title of the rule it
    -- replaces, and so matches whatever that title does.
    AND regexp_replace(upper(coalesce(bab_title, '')), '[^A-Z]', '', 'g') NOT IN ('KETENTUANPENUTUP', 'PENUTUP')
    AND left(text, 120) NOT ILIKE 'pada saat%mulai berlaku%'
), split AS (
  SELECT field, key, pasal, unnest(regexp_split_to_array(text, '[^a-z0-9]+')) AS word FROM units
)
-- Longer than 30 is an OCR run-on, not a word anyone will ask for.
SELECT field, key, pasal, word FROM split WHERE length(word) BETWEEN 1 AND 30;

CREATE TEMP TABLE vocab AS
  SELECT word, stem(word, 'indonesian') AS term FROM (SELECT DISTINCT word FROM words);

CREATE TEMP TABLE lengths AS
  SELECT field, key, pasal, count(*)::INTEGER AS dl FROM words GROUP BY ALL;

CREATE TEMP TABLE stats AS
  SELECT field, count(*)::BIGINT AS n_units, avg(dl)::DOUBLE AS avg_dl FROM lengths GROUP BY field;

CREATE TEMP TABLE postings AS
  SELECT w.field, v.term, w.key, w.pasal, count(*)::INTEGER AS tf
  FROM words w JOIN vocab v USING (word) GROUP BY ALL;

CREATE TEMP TABLE kept AS
  SELECT field, term FROM postings JOIN stats USING (field)
  GROUP BY field, term, n_units HAVING count(*) <= $MAX_DF * n_units;

COPY (
  SELECT p.term, p.field, p.key, p.pasal, p.tf, l.dl
  FROM postings p
  JOIN kept USING (field, term)
  JOIN lengths l USING (field, key, pasal)
  ORDER BY p.term, p.field
) TO '$NEW/postings/part-0.parquet' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 50000);

COPY (SELECT word, term FROM vocab ORDER BY word)
  TO '$NEW/vocab/part-0.parquet' (FORMAT parquet, COMPRESSION zstd);

COPY (
  SELECT key, track, year, instrument, title, region_name, subject, status
  FROM read_parquet('$SILVER/regulations/**/*.parquet', union_by_name = true, hive_partitioning = true)
) TO '$NEW/docs/part-0.parquet' (FORMAT parquet, COMPRESSION zstd);

COPY (SELECT * FROM stats) TO '$NEW/stats/part-0.parquet' (FORMAT parquet);
SQL

echo "indexing  $SILVER (articles of: $PASAL_TRACKS)"
# Read from stdin, not with -f: DuckDB 1.2's -f exits 0 after a failed
# statement, and the swap below would then put a broken index in place.
duckdb -bail < "$plan"

# Swapped in only when every part was written.
rm -rf "$OLD"
if [[ -d "$OUT" ]]; then mv "$OUT" "$OLD"; fi
mv "$NEW" "$OUT"
rm -rf "$OLD"

echo
duckdb -c "
SELECT field, n_units, round(avg_dl, 1) AS avg_dl FROM read_parquet('$OUT/stats/*.parquet');
SELECT count(*) AS postings, count(DISTINCT term) AS terms FROM read_parquet('$OUT/postings/*.parquet');
"
du -sh "$OUT"
