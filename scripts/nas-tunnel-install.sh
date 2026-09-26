#!/usr/bin/env bash
#
# Point terusan.csis.or.id at the stack running on the NAS.
#
# The tunnel is created from this machine — that is where a browser can
# complete the Cloudflare login — and then its identity is handed to the NAS:
# a credentials file and a rendered config, copied to the share, mounted
# read-only by the cloudflared container.
#
# Run once. Afterwards the connector comes back with the box.
#
#   ./scripts/nas-tunnel-install.sh
#   TUNNEL_HOSTNAME=data.example.org ./scripts/nas-tunnel-install.sh
#
# It changes DNS in a zone other people use. Replacing a name that already
# resolves needs OVERWRITE_DNS=1.

set -euo pipefail

cd "$(dirname "$0")/.."

HOSTNAME_="${TUNNEL_HOSTNAME:-terusan.csis.or.id}"
TUNNEL_NAME="${TUNNEL_NAME:-terusan-nas}"
NAS_HOST="${NAS_HOST:-192.168.1.212}"
NAS_USER="${NAS_USER:-dev}"
NAS_PORT="${NAS_PORT:-22}"
NAS_DIR="${NAS_DIR:-/volume2/terusan}"
REMOTE="$NAS_USER@$NAS_HOST"
SSH_OPTS=(-p "$NAS_PORT" -o ConnectTimeout=10)
CF_DIR="$HOME/.cloudflared"

command -v cloudflared >/dev/null 2>&1 || {
  echo "cloudflared is not installed here. brew install cloudflared" >&2
  exit 1
}
ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$REMOTE" true 2>/dev/null || {
  echo "cannot reach $REMOTE over SSH; enable it in UGOS → Control Panel → Terminal" >&2
  exit 1
}

mkdir -p "$CF_DIR"

# 1. Authorize against the zone. Opens a browser; the account has to hold
#    csis.or.id. Writes ~/.cloudflared/cert.pem, which is what lets the next
#    two steps create a tunnel and a DNS record.
if [ ! -f "$CF_DIR/cert.pem" ]; then
  echo "==> authorizing cloudflared against your Cloudflare account"
  echo "    a browser will open; pick the zone that holds ${HOSTNAME_#*.}"
  cloudflared tunnel login
fi

# 2. The tunnel. A separate name from the laptop's `terusan` tunnel on
#    purpose: two connectors answering for one hostname means requests land on
#    whichever is up, and debugging that is miserable.
if cloudflared tunnel list --name "$TUNNEL_NAME" --output json 2>/dev/null | grep -q '"id"'; then
  echo "==> tunnel $TUNNEL_NAME already exists, reusing it"
else
  echo "==> creating tunnel $TUNNEL_NAME"
  cloudflared tunnel create "$TUNNEL_NAME"
fi

id=$(cloudflared tunnel list --name "$TUNNEL_NAME" --output json \
  | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([0-9a-f-]*\)".*/\1/p' | head -1)
[ -n "$id" ] || { echo "could not read the tunnel id back; run: cloudflared tunnel list" >&2; exit 1; }

credentials="$CF_DIR/$id.json"
[ -f "$credentials" ] || {
  echo "the credentials file for $TUNNEL_NAME is not on this machine ($credentials)." >&2
  echo "It is written by \`cloudflared tunnel create\`; copy it here, or delete" >&2
  echo "the tunnel and create it again." >&2
  exit 1
}
echo "    id          $id"
echo "    credentials $credentials"

# 3. Both files onto the share, where the container mounts them. The
#    credentials are a bearer secret for the hostname: 600, and the directory
#    is mounted read-only so a compromised connector cannot rewrite its own
#    ingress.
echo "==> copying the connector's configuration to $REMOTE:$NAS_DIR/cloudflared"
ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p '$NAS_DIR/cloudflared'"
ssh "${SSH_OPTS[@]}" "$REMOTE" "cat > '$NAS_DIR/cloudflared/credentials.json' \
  && chmod 600 '$NAS_DIR/cloudflared/credentials.json'" < "$credentials"

sed -e "s#__TUNNEL_ID__#$id#g" \
    -e "s#__HOSTNAME__#$HOSTNAME_#g" \
    infra/cloudflare/config.nas.yml \
  | ssh "${SSH_OPTS[@]}" "$REMOTE" "cat > '$NAS_DIR/cloudflared/config.yml'"

# 4. The DNS record. A proxied CNAME to <id>.cfargotunnel.com — which is what
#    makes the hostname resolve to Cloudflare and the request come back down
#    the tunnel. This is the step that takes the name away from whatever
#    answers there now.
existing=$(dig +short "$HOSTNAME_" 2>/dev/null | head -3 | tr '\n' ' ')
if [ -n "$existing" ] && [ "${OVERWRITE_DNS:-}" != "1" ]; then
  cat >&2 <<MSG

==> $HOSTNAME_ already resolves to: $existing

Routing this tunnel replaces that record, and whatever answers there now stops
being reachable at this name. If that is what you want:

  OVERWRITE_DNS=1 ./scripts/nas-tunnel-install.sh

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

The tunnel is ready. Start the stack behind it:

  make nas-deploy

Then https://$HOSTNAME_ is the portal, and https://$HOSTNAME_/v1/... the API.
MSG
