#!/usr/bin/env bash
#
# Land the regulation corpora into the silver layer.
#
# Two corpora, one per source: the perda/perkada corpus under
# `bpk-peraturan-daerah`, and the central one — acts, government and
# presidential regulations, ministerial regulations — under
# `bpk-peraturan-pusat`. Both come out of the same crawler and share a layout
# (catalog, documents, sections, citations, tags, each a directory of
# Parquet), so they are read side by side and written as one dataset each.
#
# The corpus arrives already parsed — BPK's catalogue crawled, the PDFs
# converted, the text segmented into bab/pasal/ayat and the citations pulled
# out. Re-running that from RAW would take days and re-fetch a quarter of a
# million PDFs from a government host, so this lands the finished Parquet and
# records it as an import rather than pretending a pipeline produced it. The
# source row and the run it writes say exactly that; see
# `docs/adding-a-source.md` for what a real source looks like.
#
# Re-runnable: each dataset is rebuilt from scratch out of every corpus, so a
# corrected corpus can simply be imported again. A corpus is never imported
# alone: the silver datasets are overwritten whole, and landing one would
# drop the other.
#
# The crawler keeps its catalogue in SQLite; the other four arrive as Parquet.
# Export the catalogue once, beside them, before importing:
#
#   duckdb -c "LOAD sqlite; COPY (SELECT * REPLACE (TRY_CAST(year AS INTEGER) AS year)
#     FROM sqlite_scan('laws_catalog.db', 'catalog')) TO 'catalog/part-0000.parquet'"

set -euo pipefail

cd "$(dirname "$0")/.."

CORPUS="${CORPUS:-$HOME/Dev/corpus/data}"
LAWS_CORPUS="${LAWS_CORPUS:-$HOME/Dev/corpus/laws}"
STORAGE_ROOT="${STORAGE_ROOT:-./.data}"
SILVER="$STORAGE_ROOT/silver"
MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-6GB}"

for pair in "CORPUS:$CORPUS" "LAWS_CORPUS:$LAWS_CORPUS"; do
  var="${pair%%:*}" dir="${pair#*:}"
  for t in catalog documents sections citations tags; do
    if ! compgen -G "$dir/$t/*.parquet" >/dev/null; then
      echo "no parquet under $dir/$t — set $var to the corpus data directory" >&2
      exit 1
    fi
  done
done

echo "regional  $CORPUS"
echo "central   $LAWS_CORPUS"
echo "silver    $SILVER"
echo

# The catalogue left-joined to its parsed documents, for one corpus: the
# directory, then the registered source that published it. Joined within a
# corpus rather than across the union, though keys do not collide today —
# both are d{bpk_id} off one portal, and a join across would hide the day they
# do.
regulations_of() {
  cat <<SQL
  SELECT
    c.key,
    c.id                AS bpk_id,
    -- Carried in the data rather than assumed by the serving layer, so the
    -- join to the source registry is a real key and the central corpus lands
    -- beside the regional one without the API learning about it.
    '$2' AS source_id,
    c.track,
    -- The parsed guess, corrected by the title and the track; see
    -- sql/silver/regulation_instrument.sql for why the guess is not enough.
    regulation_instrument(c.title, blank_to_null(d.instrument), c.track,
                          blank_to_null(c.region_type)) AS instrument,
    blank_to_null(d.scope) AS scope,
    c.title,
    blank_to_null(c.number) AS number,
    c.year,
    blank_to_null(c.region_name) AS region_name,
    blank_to_null(c.region_type) AS region_type,
    blank_to_null(c.region_code) AS region_code,
    blank_to_null(c.category) AS category,
    blank_to_null(c.subject) AS subject,
    blank_to_null(c.status) AS status,
    blank_to_null(d.legal_status) AS legal_status,
    blank_to_null(c.description) AS description,
    blank_to_null(CAST(c.enacted_date AS VARCHAR)) AS enacted_date,
    blank_to_null(CAST(c.published_date AS VARCHAR)) AS published_date,
    c.detail_url,
    c.pdf_url,
    c.has_pdf,
    c.has_md,
    c.md_quality,
    c.md_coverage,
    blank_to_null(d.parse_status) AS parse_status,
    d.n_char,
    d.n_word,
    d.n_pasal,
    d.n_ayat,
    d.n_bab,
    d.n_citations,
    d.preamble,
    d.menimbang,
    d.mengingat,
    d.penutup,
    c.scraped_at,
    c.updated_at
  FROM read_parquet('$1/catalog/*.parquet') c
  LEFT JOIN read_parquet('$1/documents/*.parquet') d USING (key)
SQL
}

# `regulations` is the catalogue left-joined to the parsed text, not the parsed
# text alone: BPK publishes records whose PDF never converted, and dropping
# them would make the corpus look complete when it is not. Those rows keep
# their title, region and link, and carry a null parse_status.
#
# Sections partition by track and year because the detail page asks for one
# document's articles and would otherwise scan a gigabyte to find them.
# Citations and tags are small, but they are partitioned too so that every
# dataset here is a directory of parts and reads the same way.
# Written to a file rather than passed with `-c`: past a few kilobytes the
# CLI stops reading the argument as a command and tries to open it as a
# database, which fails as "File name too long".
plan="$(mktemp -t terusan-import-XXXXXX.sql)"
trap 'rm -f "$plan"' EXIT

cat > "$plan" <<SQL
SET memory_limit='$MEMORY_LIMIT';
SET preserve_insertion_order=false;

-- The crawl spells a missing value as the string 'NA', and sometimes as an
-- empty one: 58,931 records carry it in category, subject and both dates,
-- 16,541 in the number. Left alone it travels the whole way — the API serves
-- "NA", the filter offers it as a category, and the detail page prints
-- "Enacted: NA". It is a null, so it is stored as one, here, once.
CREATE OR REPLACE MACRO blank_to_null(v) AS nullif(nullif(trim(v), ''), 'NA');

$(cat sql/silver/regulation_instrument.sql)

COPY (
$(regulations_of "$CORPUS" bpk-peraturan-daerah)
  UNION ALL BY NAME
$(regulations_of "$LAWS_CORPUS" bpk-peraturan-pusat)
) TO '$SILVER/regulations'
  (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (track), OVERWRITE, FILENAME_PATTERN 'part-{i}');

COPY (
  SELECT key, seq, kind, bab, bab_num, bab_title, bagian, paragraf,
         pasal, pasal_num, ayat, text, n_char, n_word, track, year
  FROM read_parquet(['$CORPUS/sections/*.parquet', '$LAWS_CORPUS/sections/*.parquet'], union_by_name = true)
) TO '$SILVER/regulation_sections'
  (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (track, year), OVERWRITE, FILENAME_PATTERN 'part-{i}');

-- Partitioned on the citing document's track, which the corpus calls
-- src_track; a bare track here would read as the cited instrument's.
-- Backticks are deliberately absent: this comment sits inside a
-- double-quoted shell string, where they would run as a command.
COPY (
  SELECT * FROM read_parquet(['$CORPUS/citations/*.parquet', '$LAWS_CORPUS/citations/*.parquet'], union_by_name = true)
) TO '$SILVER/regulation_citations'
  (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (src_track), OVERWRITE, FILENAME_PATTERN 'part-{i}');

-- Partitioned like the rest, and not because 1.7MB needs it: without
-- PARTITION_BY, COPY TO writes a single file at that path rather than a
-- directory, and the dataset stops matching the glob every reader uses.
COPY (
  SELECT * FROM read_parquet(['$CORPUS/tags/*.parquet', '$LAWS_CORPUS/tags/*.parquet'], union_by_name = true)
) TO '$SILVER/regulation_tags'
  (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (track), OVERWRITE, FILENAME_PATTERN 'part-{i}');
SQL

duckdb -f "$plan"

echo
echo "landed:"
duckdb -c "
SELECT 'regulations'        AS dataset, count(*) AS rows FROM read_parquet('$SILVER/regulations/**/*.parquet')
UNION ALL SELECT 'sections',  count(*) FROM read_parquet('$SILVER/regulation_sections/**/*.parquet')
UNION ALL SELECT 'citations', count(*) FROM read_parquet('$SILVER/regulation_citations/**/*.parquet')
UNION ALL SELECT 'tags',      count(*) FROM read_parquet('$SILVER/regulation_tags/**/*.parquet');
SELECT source_id, track, count(*) AS rows, count(parse_status) AS parsed
FROM read_parquet('$SILVER/regulations/**/*.parquet', hive_partitioning = true)
GROUP BY ALL ORDER BY source_id, track;
"

# The assistant searches the regulations through an index built from what
# was just landed; one left from before the import would point at rows that
# are no longer there.
echo
STORAGE_ROOT="$STORAGE_ROOT" ./scripts/index-regulations.sh
