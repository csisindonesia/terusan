#!/usr/bin/env bash
#
# Run the serving layer and the portal together.
#
# `make -j2` cannot do this properly: interrupting it leaves both children
# running, because `go run` spawns the compiled binary as a further child and
# neither is in make's signal path. The ports then stay held, vite quietly moves
# to 3001, and the browser keeps talking to yesterday's code.
#
# So both run inside one process group, and the trap kills the group.

set -euo pipefail

cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-8080}"
PORTAL_PORT="${PORTAL_PORT:-3000}"

# A port already in use means something is still running from last time.
# Reported rather than worked around: vite would take the next free port, and a
# portal on 3001 talking to an API that only allows 3000 fails in a way nobody
# enjoys diagnosing.
for port in "$API_PORT" "$PORTAL_PORT"; do
  if lsof -ti:"$port" >/dev/null 2>&1; then
    holder=$(lsof -ti:"$port" | head -1)
    echo "port $port is already in use by pid $holder" >&2
    echo "stop it first:  kill $holder" >&2
    exit 1
  fi
done

cleanup() {
  trap - INT TERM EXIT
  # Kill the process group, which is what catches `go run`'s compiled child.
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "API    http://localhost:$API_PORT"
echo "portal http://localhost:$PORTAL_PORT"
echo "Ctrl-C stops both."
echo

(cd services/api && API_PORT="$API_PORT" go run ./cmd/api) &
api=$!

(corepack pnpm --filter @terusan/portal dev -- --port "$PORTAL_PORT" --strictPort) &
portal=$!

# Exit as soon as either dies, so a crashed API does not leave a portal running
# against nothing. Polled rather than `wait -n`, which macOS's bash 3.2 does not
# have — and the default shell here is the one this has to work in.
while kill -0 "$api" 2>/dev/null && kill -0 "$portal" 2>/dev/null; do
  sleep 1
done
