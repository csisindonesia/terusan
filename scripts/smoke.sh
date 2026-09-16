#!/usr/bin/env bash
#
# Check that a running stack actually serves data to a browser.
#
# Exists because the portal once rendered perfectly server-side and then sat
# there: `@vitejs/plugin-react` was missing from the vite config, so the dev
# client entry 500'd, nothing hydrated, and no query ever ran. Every other check
# passed — typecheck, build, and curling the SSR HTML, which looked complete.
# The only thing that would have caught it is asking whether the client JS loads.
#
# Assumes `make dev` is already running.

set -uo pipefail

API="${API_URL:-http://localhost:8080}"
PORTAL="${PORTAL_URL:-http://localhost:3000}"

failures=0

check() {
  local label="$1" actual="$2" expected="$3"
  if [ "$actual" = "$expected" ]; then
    printf '  ok    %s\n' "$label"
  else
    printf '  FAIL  %s (got %s, want %s)\n' "$label" "$actual" "$expected"
    failures=$((failures + 1))
  fi
}

status() { curl -s -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000; }

echo "API $API"
check "health"                  "$(status "$API/healthz")"          200
check "readiness"               "$(status "$API/readyz")"           200
check "datasets"                "$(status "$API/v1/datasets")"      200
check "observations"            "$(status "$API/v1/observations?limit=1")" 200
check "rejects a bad parameter" "$(status "$API/v1/observations?limit=999999")" 400

echo
echo "portal $PORTAL"
check "home"         "$(status "$PORTAL/")"             200
check "observations" "$(status "$PORTAL/observations")" 200

# The check that matters. In dev the client entry is served by vite; in a
# production build it is a hashed bundle referenced by the page. Either way, a
# page whose client JS does not load renders once and never fetches anything.
entry="$PORTAL/@id/virtual:tanstack-start-dev-client-entry"
entry_status=$(status "$entry")
if [ "$entry_status" = "404" ]; then
  echo "  --    dev client entry absent, assuming a production build"
  if curl -s "$PORTAL/observations" | grep -qE 'src="[^"]*\.js"'; then
    echo "  ok    page references a client bundle"
  else
    echo "  FAIL  page references no client bundle; nothing will hydrate"
    failures=$((failures + 1))
  fi
else
  check "dev client entry loads" "$entry_status" 200
fi

# Rendering the page in a browser is the only way to see hydration actually
# happen. Skipped where there is no Chrome rather than failing, so this stays
# usable on a machine without one.
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
echo
if [ -x "$CHROME" ]; then
  echo "browser"
  dom=$(mktemp)
  "$CHROME" --headless --disable-gpu --no-sandbox --virtual-time-budget=15000 \
    --dump-dom "$PORTAL/observations" >"$dom" 2>/dev/null
  if grep -q 'animate-pulse' "$dom"; then
    echo "  FAIL  still showing loading skeletons; the client never fetched"
    failures=$((failures + 1))
  else
    echo "  ok    table rendered rows rather than skeletons"
  fi
  rm -f "$dom"
else
  echo "browser  skipped (no Chrome at $CHROME)"
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "all checks passed"
else
  echo "$failures check(s) failed"
  exit 1
fi
