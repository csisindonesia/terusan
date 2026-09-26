#!/usr/bin/env bash
#
# The scheduled run: collect whatever is due, and nothing else.
#
# Every registry record carries a cron — `0 18 * * 1-5` for IHSG, after the
# Jakarta close; `0 22 * * 1-5` for the commodity futures, after New York;
# `7 * * * *` for BMKG's earthquakes. Until this existed nothing read them. One
# agent fired at 05:00 and ran a list of two slugs somebody had typed, so
# thirteen of fifteen daily sources were collected only when a person
# remembered — and IHSG, asked for at five in the morning, would have returned
# the session before last.
#
# So the agent runs every hour and this decides what that hour owes. Adding a
# source with a schedule needs no edit here or anywhere else.
#
# The window matters. An agent starts a few seconds late, a laptop wakes at
# 09:03 into the 09:00 it slept through, and a matcher demanding the exact
# minute would skip the run in silence. `--window` asks whether a firing fell
# anywhere in the last hour, so a late agent is harmless and a missed hour is
# caught by the next one rather than lost.
#
# Invoked by the launchd agent in infra/local/, or by hand:
#
#   scripts/scheduled.sh                              # collect what this hour owes
#   scripts/scheduled.sh --list                       # say it, collect nothing
#   scripts/scheduled.sh --list --at 2026-09-25T22:00 # what a Friday evening owes
#   scripts/scheduled.sh --window 180                 # catch up three hours
#
# `--list` first, because `--at` answers a question and answering it should not
# also fetch: without it, asking what a Friday evening owes collects a Friday
# evening. Everything else is handed to `sources due` unread.
#
# It delegates to `daily.sh`, which is where fetch, extract and normalize live.
# Nothing due means nothing run and exit 0: most hours are quiet, and a quiet
# hour is not a failure.

set -euo pipefail

cd "$(dirname "$0")/.."

WINDOW="${WINDOW:-60}"

list_only=0
if [ "${1:-}" = "--list" ]; then
  list_only=1
  shift
fi

# The rest is passed through to `sources due` — `--window` and `--at` are its
# own flags, and repeating their parsing here would be a second place for them
# to disagree.
due=()
while read -r slug; do
  due+=("$slug")
done < <(uv --project pipelines run terusan sources due --window "$WINDOW" "$@")

if [ ${#due[@]} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') nothing due"
  exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') due: ${due[*]}"

if [ "$list_only" = "1" ]; then
  exit 0
fi

exec ./scripts/daily.sh "${due[@]}"
