#!/usr/bin/env bash
#
# One day's ingestion: fetch the daily sources, fold what landed into Bronze,
# and normalize it into the Silver observations the portal and the API read.
#
# All three, not just the fetch: a figure that reaches Bronze and stops there is
# invisible to every reader, because the catalogue is derived from Silver.
#
# Both halves are idempotent. Landing is content-addressed, so a day where a
# page did not change writes nothing; extraction skips documents already in
# Bronze at the current parser version. Running this twice in a day is
# therefore harmless — which is what makes a missed day safe to catch up.
#
# Invoked by the launchd agent in infra/local/, or by hand:
#
#   scripts/daily.sh                              # the daily sources
#   scripts/daily.sh tradingeconomics-indonesia   # one of them

set -euo pipefail

cd "$(dirname "$0")/.."

# Sources that declare a daily schedule. Named rather than taken from
# `terusan sources run` with no arguments, which runs *every* scheduled source:
# that would fetch a monthly APBD release thirty times a month, and the point of
# a cron on each source's registry record is that they do not all run alike.
#
# FRED is not among them: its registry record says Mondays, because a crawl of
# six hundred CSVs to find a monthly OECD release is six days of identical
# bytes. Run it by name — `scripts/daily.sh fred-indonesia` — or let the weekly
# schedule reach it.
DEFAULT_SOURCES=(tradingeconomics-indonesia)

sources=("$@")
if [ ${#sources[@]} -eq 0 ]; then
  sources=("${DEFAULT_SOURCES[@]}")
fi

# The run history is optional, and by default a missing PostgreSQL does not stop
# the ingestion — the lake is the system of record for data, the catalog only
# records what happened to it. Set REQUIRE_CATALOG=1 once the database is
# running, where losing history silently is worse than failing loudly.
catalog=()
if [ "${REQUIRE_CATALOG:-0}" = "1" ]; then
  catalog=(--require-catalog)
fi

LOG_DIR="${LOG_DIR:-.cache/logs}"
mkdir -p "$LOG_DIR" .cache
log="$LOG_DIR/daily-$(date +%Y-%m-%d).log"

# A lock directory rather than flock, which macOS does not ship. mkdir is
# atomic on every filesystem this runs on, so two agents firing at once — a
# laptop waking into a missed 05:00 while the next one is due — cannot have the
# same source fetching twice.
lock=".cache/daily.lock"
if ! mkdir "$lock" 2>/dev/null; then
  echo "another daily run holds $lock; exiting" | tee -a "$log"
  exit 0
fi
trap 'rmdir "$lock" 2>/dev/null || true' EXIT

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ingest: ${sources[*]} ==="

  # `--trigger schedule` is what tells the run history this was the scheduler
  # rather than someone at a terminal, which is the difference between "nobody
  # has run this in a week" and "nobody needed to".
  uv --project pipelines run terusan sources run "${sources[@]}" \
    --trigger schedule ${catalog[@]+"${catalog[@]}"}

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') extract ==="
  uv --project pipelines run terusan warehouse extract

  # Normalization is per indicator and per source, so each source that has a
  # mapping brings its own script. Only run for the sources this run fetched:
  # rebuilding an indicator nobody refreshed is work for no new figures.
  # Yahoo lands one instrument per source, and one script normalizes all of
  # them — so a run that fetched gold and copper normalizes once rather than
  # twice over the same seven.
  yahoo_normalized=0

  for source in "${sources[@]}"; do
    case "$source" in
      tradingeconomics-indonesia)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-tradingeconomics.sh
        ;;
      fred-indonesia)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-fred.sh
        ;;
      sipri-milex)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-sipri.sh
        ;;
      bi-seki)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-seki.sh
        ;;
      esdm-heesi)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-heesi.sh
        ;;
      hdx-meta-movement-distribution)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-mobility.sh
        ;;
      bi-pihps-food-prices)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        ./scripts/normalize-pihps.sh
        ;;
      yahoo-gold|yahoo-copper|yahoo-brent-crude|yahoo-thermal-coal|yahoo-palm-oil|yahoo-coffee|yahoo-cocoa)
        if [ "$yahoo_normalized" = "0" ]; then
          echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: yahoo commodities ==="
          ./scripts/normalize-yahoo.sh
          yahoo_normalized=1
        fi
        ;;
    esac
  done

  # The dataset catalogue and the source registry, republished after the
  # figures: a collection normalized for the first time today has a code and
  # no title until this runs, and a reader would see the code.
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') dimensions ==="
  uv --project pipelines run terusan silver dimensions

  # The document catalogue, last: it counts what each landed file contributed,
  # and those counts come from the observations this run just wrote. Rebuilt
  # from the sidecars every time, so a file landed this morning appears without
  # anyone remembering to add it.
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') documents ==="
  uv --project pipelines run terusan silver documents

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') done ==="
} 2>&1 | tee -a "$log"
