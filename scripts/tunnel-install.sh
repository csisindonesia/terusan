#!/usr/bin/env bash
#
# Put the portal on a permanent hostname, behind a named Cloudflare Tunnel.
#
# Three things, none of which the quick tunnel (`scripts/tunnel.sh`) has:
#
#   * a tunnel with a name and a credentials file, so it comes back with the
#     same identity after a restart;
#   * a CNAME in the zone, so the hostname is ours rather than three random
#     words;
#   * a config file with ingress rules, so one hostname reaches both the portal
#     and the API by path.
#
# Run once. Afterwards `cloudflared service install` keeps it running, and
# `make serve` runs the stack it points at.
#
# It changes DNS in a zone other people use. Every step that does is announced
# before it runs, and replacing an existing record needs OVERWRITE_DNS=1.

set -euo pipefail

cd "$(dirname "$0")/.."

HOSTNAME_="${TUNNEL_HOSTNAME:-terusan.csis.or.id}"
TUNNEL_NAME="${TUNNEL_NAME:-terusan}"
API_PORT="${API_PORT:-8080}"
PORTAL_PORT="${PORTAL_PORT:-3000}"
CF_DIR="$HOME/.cloudflared"
CONFIG="$CF_DIR/config.yml"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed. Install it first:" >&2
  echo "  brew install cloudflared" >&2
  exit 1
fi

mkdir -p "$CF_DIR"

# 1. Authorize this machine against the zone. Opens a browser; the account has
#    to be one that holds csis.or.id. Writes ~/.cloudflared/cert.pem, which is
#    what lets the next two steps create a tunnel and a DNS record.
if [ ! -f "$CF_DIR/cert.pem" ]; then
  echo "==> authorizing cloudflared against your Cloudflare account"
  echo "    a browser will open; pick the zone that holds ${HOSTNAME_#*.}"
  cloudflared tunnel login
fi

# 2. The tunnel itself. Named lookups are stable, so an existing one is reused
#    rather than duplicated — a second tunnel with the same purpose is how you
#    end up with a hostname pointing at whichever one happens to be up.
if cloudflared tunnel list --name "$TUNNEL_NAME" --output json 2>/dev/null \
  | grep -q '"id"'; then
  echo "==> tunnel $TUNNEL_NAME already exists, reusing it"
else
  echo "==> creating tunnel $TUNNEL_NAME"
  cloudflared tunnel create "$TUNNEL_NAME"
fi

# `--output json` is pretty-printed, so the spaces around the colon are part
# of what has to be matched.
id=$(cloudflared tunnel list --name "$TUNNEL_NAME" --output json \
  | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([0-9a-f-]*\)".*/\1/p' | head -1)
if [ -z "$id" ]; then
  echo "could not read the tunnel id back; run: cloudflared tunnel list" >&2
  exit 1
fi
credentials="$CF_DIR/$id.json"
echo "    id          $id"
echo "    credentials $credentials"

if [ ! -f "$credentials" ]; then
  echo "the credentials file for $TUNNEL_NAME is not on this machine." >&2
  echo "It is written by \`cloudflared tunnel create\` on the machine that ran" >&2
  echo "it; copy it here, or delete the tunnel and create it again." >&2
  exit 1
fi

# 3. The config the connector runs with, rendered from the reviewable template.
echo "==> writing $CONFIG"
sed -e "s#__TUNNEL_ID__#$id#g" \
    -e "s#__CREDENTIALS_FILE__#$credentials#g" \
    -e "s#__HOSTNAME__#$HOSTNAME_#g" \
    -e "s#__API_PORT__#$API_PORT#g" \
    -e "s#__PORTAL_PORT__#$PORTAL_PORT#g" \
    infra/cloudflare/config.yml > "$CONFIG"

cloudflared tunnel ingress validate
cloudflared tunnel ingress rule "https://$HOSTNAME_/v1/indicators"
cloudflared tunnel ingress rule "https://$HOSTNAME_/"

# 4. The DNS record. A CNAME to <id>.cfargotunnel.com, proxied — which is what
#    makes the hostname resolve to Cloudflare and the request come back down
#    the tunnel.
#
#    If the name already points somewhere, this is the step that takes it away
#    from whatever is serving it now. Refused unless it is asked for by name.
existing=$(dig +short "$HOSTNAME_" 2>/dev/null | head -3 | tr '\n' ' ')
if [ -n "$existing" ] && [ "${OVERWRITE_DNS:-}" != "1" ]; then
  cat >&2 <<MSG

==> $HOSTNAME_ already resolves to: $existing

Routing the tunnel replaces that record, and whatever answers there now stops
being reachable at this name. If that is what you want:

  OVERWRITE_DNS=1 ./scripts/tunnel-install.sh

Everything above this line is already done; only the DNS record is left.
MSG
  exit 1
fi

echo "==> pointing $HOSTNAME_ at the tunnel"
if [ -n "$existing" ]; then
  cloudflared tunnel route dns --overwrite-dns "$TUNNEL_NAME" "$HOSTNAME_"
else
  cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_"
fi

cat <<MSG

Done. The hostname exists; nothing is serving it until both halves run.

  make serve                     # build the portal, run it and the API

Then keep the connector up across reboots:

  sudo cloudflared service install        # launchd on macOS, systemd on Linux
  sudo launchctl start com.cloudflare.cloudflared   # macOS, first start

Check it:

  cloudflared tunnel info $TUNNEL_NAME
  curl https://$HOSTNAME_/healthz
MSG
