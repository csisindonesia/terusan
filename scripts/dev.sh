#!/usr/bin/env bash
#
# Run the serving layer and the portal together, both reloading on change.
#
# `make -j2` cannot do this properly: interrupting it leaves both children
# running, because `go run` spawns the compiled binary as a further child and
# neither is in make's signal path. The ports then stay held, vite quietly moves
# to 3001, and the browser keeps talking to yesterday's code.
#
# So both run inside one process group, and the trap kills the group.
#
# The portal reloads itself: vite watches its own sources. The API does not, so
# this script watches services/api and rebuilds. `go build` into a binary we
# start ourselves, rather than `go run`, because `go run`'s child is the actual
# server and killing the parent leaves it holding :8080.

set -euo pipefail

cd "$(dirname "$0")/.."

# Exported, not just set: vite reads API_PORT too, to point its `/v1` proxy at
# the API. That proxy is what lets one hostname serve both halves, which is
# what `make tunnel` needs and what keeps the session cookie same-site.
export API_PORT="${API_PORT:-8080}"
PORTAL_PORT="${PORTAL_PORT:-3000}"
# The portal's "run ingestion now" needs the API to be allowed to start a
# pipeline, and this script is the local-machine case that is: the pipelines
# are checked out right here, the API listens on 127.0.0.1, and the person
# clicking is the person who would otherwise be typing the command. It stays
# off everywhere else — nothing but this line ever sets it.
export PIPELINES_ENABLED="${PIPELINES_ENABLED:-true}"
export PIPELINES_ROOT="${PIPELINES_ROOT:-$PWD}"
# The application database: the shelf (collections and saved queries) and the
# accounts people log in with. A file beside the lake, not in it — the lake is
# pipeline-written and immutable, and these are the things here a person wrote.
# With it set, a collection has a URL that survives a cleared cache and opens
# on another machine, and the portal shows a login page.
export APP_DB="${APP_DB:-$PWD/.data/app.duckdb}"
# How often to look for changed Go files. Polling rather than fsevents/inotify,
# which differ per platform and would need a dependency nobody has installed.
WATCH_INTERVAL="${WATCH_INTERVAL:-1}"

BIN_DIR="services/api/bin"
BIN="$BIN_DIR/api-dev"
STAMP="$BIN_DIR/.dev-stamp"

# A port already in use means something is still running from last time.
# Reported rather than worked around: vite would take the next free port, and a
# portal on 3001 talking to an API that only allows 3000 fails in a way nobody
# enjoys diagnosing.
#
# Listeners only. Without `-sTCP:LISTEN` this also matches the dead client
# sockets an editor or a browser leaves behind after the last server stopped,
# and then it reports a port as taken and names a pid — VS Code's network
# service — that must not be killed.
for port in "$API_PORT" "$PORTAL_PORT"; do
  if lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    holder=$(lsof -ti:"$port" -sTCP:LISTEN | head -1)
    echo "port $port is already in use by pid $holder" >&2
    echo "stop it first:  kill $holder" >&2
    exit 1
  fi
done

api=""

cleanup() {
  trap - INT TERM EXIT
  # Kill the process group, which is what catches every child started here.
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Any Go input newer than the last build. The stamp is touched *before* the
# build, so an edit made while the compiler runs is picked up on the next tick
# rather than lost.
changed_go_files() {
  find services/api \
    -type d -name bin -prune -o \
    -type f \( -name '*.go' -o -name 'go.mod' -o -name 'go.sum' \) -newer "$STAMP" -print
}

stop_api() {
  [ -n "$api" ] || return 0
  kill "$api" 2>/dev/null || true
  wait "$api" 2>/dev/null || true
  api=""
}

start_api() {
  API_PORT="$API_PORT" "./$BIN" &
  api=$!
}

# Builds into a scratch path and only swaps it in on success, so a syntax error
# leaves the last working server running instead of taking the stack down.
build_api() {
  touch "$STAMP"
  if (cd services/api && go build -o "bin/api-dev.next" ./cmd/api); then
    mv "$BIN.next" "$BIN"
    return 0
  fi
  rm -f "$BIN.next"
  return 1
}

mkdir -p "$BIN_DIR"
echo "building API..."
if ! build_api; then
  echo "initial build failed" >&2
  exit 1
fi

echo "API    http://localhost:$API_PORT   (rebuilds on .go change)"
echo "portal http://localhost:$PORTAL_PORT   (vite HMR)"
echo "Ctrl-C stops both."
echo

start_api

# `pnpm --filter ... dev -- --port` does not work here: the package script
# already ends in `--port 3000`, and vite keeps the first one. Call vite
# directly so PORTAL_PORT is the only port on the command line.
(corepack pnpm --filter @terusan/portal exec vite dev --port "$PORTAL_PORT" --strictPort) &
portal=$!

# Polled rather than `wait -n`, which macOS's bash 3.2 does not have — and the
# default shell here is the one this has to work in. The same loop carries the
# file watch, so there is one place that owns the API's pid.
while kill -0 "$portal" 2>/dev/null; do
  sleep "$WATCH_INTERVAL"

  if [ -n "$(changed_go_files)" ]; then
    echo
    echo "---> API sources changed, rebuilding"
    if build_api; then
      stop_api
      start_api
      echo "---> API restarted on :$API_PORT"
    else
      echo "---> build failed; previous API still serving" >&2
    fi
    echo
    continue
  fi

  # A crashed API does not end the session: the next edit rebuilds and restarts
  # it, which is the whole point of watching. A crashed portal does end it,
  # because vite recovers from its own errors and a dead one means real trouble.
  if [ -n "$api" ] && ! kill -0 "$api" 2>/dev/null; then
    wait "$api" 2>/dev/null || true
    api=""
    echo "---> API exited; edit a .go file to rebuild and restart it" >&2
  fi
done
