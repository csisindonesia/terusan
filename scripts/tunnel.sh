#!/usr/bin/env bash
#
# Run the local stack and publish it on a public HTTPS URL.
#
# A Cloudflare quick tunnel (https://try.cloudflare.com): `cloudflared` opens an
# outbound connection to the nearest Cloudflare location and requests arrive
# back down it, so nothing here listens on a public port and no account, DNS
# record or router change is involved. The hostname is three random words and
# lasts as long as the process.
#
# One tunnel, not two. The portal and the API are different ports, and the
# obvious thing — a tunnel each — breaks the login: two `*.trycloudflare.com`
# hostnames are cross-site to a browser (the domain is on the public suffix
# list), and the session cookie is SameSite=Lax, so it would never be sent.
# Instead vite proxies `/v1` to the API (see apps/portal/vite.config.ts) and
# the whole stack answers on one origin.
#
# This is for showing someone the portal, not for deploying it. The URL is
# unauthenticated unless AUTH_REQUIRED says otherwise, and anyone who has it
# reaches this machine.

set -euo pipefail

cd "$(dirname "$0")/.."

PORTAL_PORT="${PORTAL_PORT:-3000}"
LOG_DIR=".cache/logs"
LOG="$LOG_DIR/tunnel-$(date +%Y%m%d-%H%M%S).log"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed. Install it first:" >&2
  echo "  brew install cloudflared" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

tunnel=""
cleanup() {
  trap - INT TERM EXIT
  [ -n "$tunnel" ] && kill "$tunnel" 2>/dev/null || true
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Started before the portal on purpose: the hostname has to be known before
# vite reads VITE_API_URL, and cloudflared is happy to hold a tunnel to a port
# nothing is listening on yet — it answers 502 until the portal comes up.
echo "opening a tunnel to :$PORTAL_PORT..."
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORTAL_PORT" \
  >"$LOG" 2>&1 &
tunnel=$!

# The URL is printed in a banner a second or two in. Polled rather than
# streamed, because the banner is written to a pipe cloudflared buffers.
url=""
for _ in $(seq 1 60); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$url" ] && break
  if ! kill -0 "$tunnel" 2>/dev/null; then
    echo "cloudflared exited before it printed a URL; see $LOG" >&2
    exit 1
  fi
  sleep 1
done

if [ -z "$url" ]; then
  echo "no tunnel URL after 60s; see $LOG" >&2
  exit 1
fi

# What the browser should call. Same origin as the page, so the proxy carries
# it to the API and the cookie stays same-site.
export VITE_API_URL="$url"
# Requests the browser labels cross-origin anyway — a POST from the page — must
# still be allowed by name, because the allow-list is never a wildcard.
export API_CORS_ORIGINS="${API_CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000},$url"
# The tunnel terminates TLS at Cloudflare and reaches this process over plain
# HTTP, so the API cannot tell from the request that the portal is on https.
export AUTH_SECURE_COOKIES=true

echo
echo "public $url"
echo "log    $LOG"
echo

exec ./scripts/dev.sh
