#!/usr/bin/env bash
#
# The hourly agent, for a machine with no launchd: the NAS's pipelines
# container runs this as its entrypoint.
#
# On a laptop, launchd fires `scheduled.sh` on the hour (infra/local/). The NAS
# has nothing in its place: UGOS's Task Scheduler is set up by hand in a web
# page, outside the repository, and until 2026-10-01 nobody had. So the
# container that holds the CLI also holds the clock, and a deploy that ships a
# new image brings the schedule with it.
#
# Each pass runs `scheduled.sh` and then sleeps to the top of the next hour.
# The window handed to it reaches back to the previous pass's start, not a
# fixed sixty minutes: a pass that spends ninety minutes on the news would
# otherwise leave a half hour of firings that no window covers. Those are
# also caught by the catch-up `scheduled.sh` asks for, but a window that
# meets the last one is the plain way not to lose them.
#
# A pass that fails is logged and the loop goes on. A failed source stays
# owed, so the next pass asks for it again.
#
# Still a CLI container too: `docker exec terusan-pipelines-1 terusan ...`
# works beside the loop. `daily.sh` takes a lock, so a run started by hand
# while a pass is collecting exits instead of fetching the same sources twice.

set -uo pipefail

cd "$(dirname "$0")/.."

# The lock lives in the container's own filesystem, which survives a restart
# (a crash, a reboot of the box) though not a recreate. A lock left by a pass
# that was killed would then make every later pass exit as if another held
# it. Nothing else runs when the container starts, so whatever lock is there
# is stale.
rmdir .cache/daily.lock 2>/dev/null && echo "$(date '+%F %T') removed a stale lock"

echo "$(date '+%F %T') scheduler started (TZ=${TZ:-unset}, catch-up ${CATCH_UP_HOURS:-12}h)"

previous=""
while true; do
  started=$(date +%s)
  window=60
  if [ -n "$previous" ]; then
    # Minutes since the last pass began, rounded up, never under an hour.
    since=$(((started - previous + 59) / 60))
    [ "$since" -gt "$window" ] && window=$since
  fi
  previous=$started

  WINDOW="$window" ./scripts/scheduled.sh
  status=$?
  [ "$status" -ne 0 ] && echo "$(date '+%F %T') pass failed (exit $status); failed sources stay owed"

  # To the top of the next hour, plus a few seconds so `date` already reads
  # the new hour when the pass asks what is due.
  now=$(date +%s)
  sleep $(((now / 3600 + 1) * 3600 - now + 5))
done
