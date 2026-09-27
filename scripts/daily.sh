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

# This script runs the sources it is given. Named none, it runs every active
# source whose registry record says it is collected daily — asked of the
# registry rather than kept in a list here.
#
# A list here is how the ingestion came to cover two of fifteen daily sources:
# it was written when there were two, and every source added since needed a
# second edit nobody made. `terusan sources due` is the scheduler's question;
# this is the blunter one, and neither can go stale.
#
# `terusan sources run` with no arguments would be blunter still — it runs
# *every* scheduled source, which fetches a monthly APBD release thirty times a
# month. Frequency is the filter that makes "daily" mean something.
sources=("$@")
if [ ${#sources[@]} -eq 0 ]; then
  while read -r slug; do
    sources+=("$slug")
  done < <(uv --project pipelines run python - <<'DAILY'
from terusan_pipelines.sources import UpdateFrequency, registry

registry.ensure_loaded()
for source in registry.all():
    meta = source.meta
    if meta.active and meta.update_frequency is UpdateFrequency.DAILY:
        print(meta.slug)
DAILY
  )
fi

if [ ${#sources[@]} -eq 0 ]; then
  echo "no sources to run" >&2
  exit 0
fi

# How far back a nightly fetch reaches. The lake already holds the history, so
# a run that re-pulls five years every night is asking a publisher for figures
# it landed yesterday: Yahoo answers with the whole window whatever it is asked
# for, and a full pull is a thousand bars to learn one.
#
# A week rather than a day, because a night the cron did not fire must not
# leave a hole, and because publishers restate. A restated figure arrives as a
# second document, and normalization resolves the two by retrieval time — the
# later reading wins — so a window that overlaps what is already held is
# correcting, not duplicating.
#
# Widen it for a catch-up (`SINCE_DAYS=90 scripts/daily.sh`), and set it empty
# or to 0 for a full pull, which is what a first run or a backfill wants. A
# source that does not read `--since` — Trading Economics scrapes today's pages
# whatever it is told — simply ignores it.
SINCE_DAYS="${SINCE_DAYS-7}"

since=()
if [ -n "$SINCE_DAYS" ] && [ "$SINCE_DAYS" != "0" ]; then
  # BSD `date` on macOS, GNU `date` in the container. Neither accepts the
  # other's spelling, and this script runs under both.
  if day=$(date -v-"${SINCE_DAYS}"d +%Y-%m-%d 2>/dev/null); then
    since=(--since "$day")
  elif day=$(date -d "${SINCE_DAYS} days ago" +%Y-%m-%d 2>/dev/null); then
    since=(--since "$day")
  else
    echo "cannot compute a date ${SINCE_DAYS} days back; pulling the full window" >&2
  fi
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
    --trigger schedule ${since[@]+"${since[@]}"} ${catalog[@]+"${catalog[@]}"}

  # Extraction walks a RAW subtree, and RAW is laid out `category/slug`. Given
  # no subtree it walks the whole lake — sixteen thousand documents visited to
  # find the handful this run landed. The category is on the source's registry
  # record, so the subtree is derived rather than repeated here: a source that
  # changes category keeps working, and adding one to DEFAULT_SOURCES needs no
  # second edit.
  #
  # The cost of narrowing it: a file landed outside this script — a manual
  # `sources run`, a backfill, something copied into RAW by hand — is no longer
  # swept up by the nightly. Run `terusan warehouse extract` with no arguments
  # after doing that.
  # One source's bad bytes must not end the hour. Extraction exits non-zero
  # when every document in a subtree fails, and under `set -e` that used to
  # abort the run where it stood — so an hour that owed GDELT and IHSG
  # collected both, failed on GDELT's archives, and left IHSG unextracted and
  # unnormalized without saying so. Each subtree is now allowed to fail on its
  # own; the failures are collected and the run still exits non-zero at the
  # end, so nothing is hidden and nothing else is lost.
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') extract ==="
  failures=()
  while read -r category slug; do
    echo "--- extract: $category/$slug"
    if ! uv --project pipelines run terusan warehouse extract "$category" "$slug"; then
      echo "!!! extract failed for $category/$slug — continuing with the rest"
      failures+=("extract:$category/$slug")
    fi
  done < <(uv --project pipelines run python - "${sources[@]}" <<'SUBTREES'
import sys

from terusan_pipelines.sources import registry

registry.ensure_loaded()
for slug in sys.argv[1:]:
    print(registry.get(slug).meta.category, slug)
SUBTREES
  )

  # Normalization is per indicator and per source, so each source that has a
  # mapping brings its own script. Only run for the sources this run fetched:
  # rebuilding an indicator nobody refreshed is work for no new figures.
  # Yahoo lands one instrument per source, and one script normalizes all of
  # them — so a run that fetched gold and copper normalizes once rather than
  # twice over the same seven.
  yahoo_normalized=0
  sina_normalized=0

  for source in "${sources[@]}"; do
    # Same reasoning as the extract loop: a mapping that breaks on one source
    # must not cost every later source its figures.
    normalize() {
      if ! "$@"; then
        echo "!!! normalize failed for $source — continuing with the rest"
        failures+=("normalize:$source")
      fi
    }
    case "$source" in
      tradingeconomics-indonesia)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-tradingeconomics.sh
        ;;
      fred-indonesia)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-fred.sh
        ;;
      sipri-milex)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-sipri.sh
        ;;
      bi-seki)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-seki.sh
        ;;
      hdx-meta-movement-distribution)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-mobility.sh
        ;;
      gee-air-quality)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-air-quality.sh
        ;;
      gee-*)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-gee.sh "${source#gee-}"
        ;;
      bi-pihps-food-prices)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-pihps.sh
        ;;
      news-monitoring)
        # Two steps, and the order matters. Extraction has just coded each new
        # article on its own; clustering is what collapses several papers'
        # reports of one brawl into one incident and counts them, and it is a
        # recompute over the whole window rather than an append — so it has to
        # run after everything this hour landed, and before the counts are
        # normalized into figures.
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') cluster: $source ==="
        normalize uv --project pipelines run terusan news cluster
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-news.sh
        ;;
      ucdp-organized-violence)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-ucdp.sh
        ;;
      djpk-apbd)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-apbd.sh
        ;;
      kemendagri-wilayah)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-kemendagri.sh
        ;;
      comtrade-indonesia)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-comtrade.sh
        ;;
      wits-tradestats|rca-seed)
        # One script for both: the seed's base years barely change and are
        # cheap to rebuild beside the WITS series that do.
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-rca.sh
        ;;
      yahoo-exchange-rates)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-fx.sh
        ;;
      yahoo-ihsg)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-ihsg.sh
        ;;
      # Every other Yahoo source is a commodity, so the pattern rather than a
      # list: a list here is a second edit every new instrument needs.
      yahoo-*)
        if [ "$yahoo_normalized" = "0" ]; then
          echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: yahoo commodities ==="
          normalize ./scripts/normalize-yahoo.sh
          yahoo_normalized=1
        fi
        ;;
      tradingeconomics-coal)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-coal.sh
        ;;
      westmetall-lme)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-lme.sh
        ;;
      sina-*)
        # One script for every contract, run once.
        if [ "$sina_normalized" = "0" ]; then
          echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: sina futures ==="
          normalize ./scripts/normalize-sina.sh
          sina_normalized=1
        fi
        ;;
      kemendag-sp2kp-prices)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-sp2kp.sh
        ;;
      menpan-hari-libur)
        # Not a series: the holiday decrees become the event calendar, rebuilt
        # whole because an amendment rewrites a year already published.
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') events: $source ==="
        normalize uv --project pipelines run terusan silver events
        ;;
      kemendag-sp2kp-national)
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') normalize: $source ==="
        normalize ./scripts/normalize-sp2kp-national.sh
        ;;
      # Sources with no mapping fall through deliberately. JDIHN's legal
      # documents and Geofabrik's extract are not series: they land, they
      # reach Bronze, and `silver documents` below is what catalogues them.
      # A run that fetched only those normalizes nothing and is not a failure.
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

  if [ ${#failures[@]} -gt 0 ]; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') done, with failures: ${failures[*]} ==="
    exit 1
  fi

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') done ==="
} 2>&1 | tee -a "$log"
