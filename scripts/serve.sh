#!/usr/bin/env bash
#
# Run the stack the way the public hostname expects it: built, not reloading.
#
# `make dev` is for a person at this machine — vite in dev mode, a proxy for
# `/v1`, CORS pointed at localhost. Behind the tunnel none of that holds: the
# portal is a built Node server with no proxy in it, the split between portal
# and API is made by the connector's ingress rules (infra/cloudflare/config.yml),
# and the browser is on https at a hostname this process never sees.
#
# So this sets the three things that follow from being published, and then runs
# both halves bound to the loopback — the connector is the only way in.

set -euo pipefail

cd "$(dirname "$0")/.."

PUBLIC_URL="${PUBLIC_URL:-https://${TUNNEL_HOSTNAME:-terusan.csis.or.id}}"
export API_PORT="${API_PORT:-8080}"
export API_HOST="${API_HOST:-127.0.0.1}"
PORTAL_PORT="${PORTAL_PORT:-3000}"

# The portal reads this at build time, so it is set before the build, not after.
export VITE_API_URL="${VITE_API_URL:-$PUBLIC_URL}"
# Same-origin requests the browser still labels cross-origin — a POST from the
# page — must be allowed by name; the allow-list is never a wildcard.
export API_CORS_ORIGINS="${API_CORS_ORIGINS:-$PUBLIC_URL}"
# TLS ends at Cloudflare and the request arrives here over plain HTTP, so the
# API cannot otherwise tell the portal is on https and would set a cookie
# without Secure.
export AUTH_SECURE_COOKIES="${AUTH_SECURE_COOKIES:-true}"
# Off unless somebody says otherwise: this endpoint starts scrapers on the host
# and has no authorization in front of it (program.md §34). `make dev` turns it
# on because the person clicking is the person at the machine. Nobody at a
# public hostname is.
export PIPELINES_ENABLED="${PIPELINES_ENABLED:-false}"
export APP_DB="${APP_DB:-$PWD/.data/app.duckdb}"

for port in "$API_PORT" "$PORTAL_PORT"; do
  if lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    holder=$(lsof -ti:"$port" -sTCP:LISTEN | head -1)
    echo "port $port is already in use by pid $holder" >&2
    exit 1
  fi
done

cleanup() {
  trap - INT TERM EXIT
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "==> building"
(cd services/api && go build -o bin/api ./cmd/api)
corepack pnpm --filter @terusan/portal build

echo
echo "public $PUBLIC_URL"
echo "API    http://$API_HOST:$API_PORT"
echo "portal http://127.0.0.1:$PORTAL_PORT"
echo

./services/api/bin/api &
api=$!

(cd apps/portal && PORT="$PORTAL_PORT" HOST=127.0.0.1 node server.mjs) &
portal=$!

wait "$api" "$portal"
