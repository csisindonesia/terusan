#!/usr/bin/env bash
#
# Put the stack on the NAS.
#
# The repository is copied to the share and a single, self-contained compose
# file is rendered beside it, which the UGOS Docker app runs as a Project.
#
# Why rendered rather than run from here: on UGOS the login user is not in the
# docker group and sudo wants a password, so `docker compose up` over SSH is
# not available. `docker compose config` is — it only reads files — so the
# merge of compose.yaml and infra/nas/compose.nas.yaml happens on the box,
# with every path and variable resolved, and the UGOS UI starts the result.
# Where the socket *is* reachable, this script goes ahead and starts it
# itself.
#
# Idempotent. Run it again after a commit: the code is re-synced, the compose
# file re-rendered, and the lake, the catalog volume and the .env written on
# the first deploy are left alone.
#
#   ./scripts/nas-deploy.sh
#   ./scripts/nas-deploy.sh --no-tunnel      # leave the connector out
#
# Needs SSH enabled on the NAS: UGOS → Control Panel → Terminal → SSH.

set -euo pipefail

cd "$(dirname "$0")/.."

NAS_HOST="${NAS_HOST:-192.168.1.212}"
NAS_USER="${NAS_USER:-dev}"
NAS_PORT="${NAS_PORT:-22}"
NAS_DIR="${NAS_DIR:-/volume2/terusan}"
APP_DIR="$NAS_DIR/app"
STACK_DIR="$NAS_DIR/stack"
REMOTE="$NAS_USER@$NAS_HOST"
SSH_OPTS=(-p "$NAS_PORT" -o ConnectTimeout=10)

PROFILES=(--profile catalog --profile tunnel)
for arg in "$@"; do
  case "$arg" in
    --no-tunnel) PROFILES=(--profile catalog) ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '==> %s\n' "$*"; }
nas() { ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; }

# ---- 1. can we get in ------------------------------------------------------

if ! ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$REMOTE" true 2>/dev/null; then
  cat >&2 <<MSG
Cannot reach $REMOTE over SSH on port $NAS_PORT without a password.

  * enable SSH: UGOS → Control Panel → Terminal → SSH
  * then put a key on the box:  ssh-copy-id -p $NAS_PORT $REMOTE
MSG
  exit 1
fi
say "ssh $REMOTE ok"

nas "command -v docker >/dev/null" || {
  echo "docker is not installed on the NAS: UGOS → App Center → Docker" >&2
  exit 1
}

# ---- 2. the directories ----------------------------------------------------
#
# The lake is created here rather than by a container, because a bind mount of
# a path that does not exist gets an empty directory owned by root instead of
# an error — an ingestion then writes into nothing.

say "creating $NAS_DIR/{app,stack,cloudflared,lake}"
nas "mkdir -p '$APP_DIR' '$STACK_DIR' '$NAS_DIR/cloudflared' \
  '$NAS_DIR/lake'/{raw,bronze,silver,gold,exports,temporary}"

# ---- 3. the code -----------------------------------------------------------
#
# tar rather than rsync: UGOS's rsync refuses an absolute destination path
# ("invalid path"), and a copy that always works beats an incremental one that
# works on some firmware.
#
# What is excluded matters more than what is copied: .data is the laptop's own
# lake and would overwrite the NAS's, and node_modules and dist are rebuilt
# inside the images from the lockfile.

say "copying the repository to $APP_DIR"
COPYFILE_DISABLE=1 tar czf - --no-xattrs \
  --exclude '.git' --exclude '.data' --exclude '.cache' \
  --exclude 'node_modules' --exclude 'dist' --exclude '.venv' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude 'tmp' --exclude '.DS_Store' --exclude '.env' \
  . | nas "tar xzf - -C '$APP_DIR'"

# ---- 4. the environment ----------------------------------------------------
#
# Written once. A redeploy must not roll the catalog password: PostgreSQL
# keeps the one it was initialised with, and the two drifting apart shows up
# as an API that cannot reach its catalog rather than as anything about
# passwords.

if nas "test -f '$STACK_DIR/.env'"; then
  say ".env already on the NAS, leaving it alone"
else
  say "writing $STACK_DIR/.env with a generated catalog password"
  pw=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)
  sed -e "s#^DATA_DIR=.*#DATA_DIR=$NAS_DIR/lake#" \
      -e "s#^CLOUDFLARED_DIR=.*#CLOUDFLARED_DIR=$NAS_DIR/cloudflared#" \
      -e "s#__GENERATED__#$pw#g" \
      infra/nas/env.nas.example \
    | nas "cat > '$STACK_DIR/.env' && chmod 600 '$STACK_DIR/.env'"
fi

# ---- 5. the compose file the UGOS project runs -----------------------------
#
# Fully resolved: the UGOS UI is given a path, not an environment, so a file
# still carrying ${DATA_DIR} would mount nothing. Build contexts and the two
# PostgreSQL bind mounts come out as absolute paths under app/, which is why
# the project can live in a directory of its own.
#
# 0600, because the rendered file carries the catalog password.

if [[ " ${PROFILES[*]} " == *" --profile tunnel "* ]] \
   && ! nas "test -f '$NAS_DIR/cloudflared/credentials.json'"; then
  cat >&2 <<MSG

==> no tunnel credentials at $NAS_DIR/cloudflared/credentials.json

Run ./scripts/nas-tunnel-install.sh first, or leave the connector out:

  ./scripts/nas-deploy.sh --no-tunnel
MSG
  exit 1
fi

# `--profile tools` as well, so the pipelines container is in the file; the
# NAS runs it idle and starts pipelines with `docker exec` (see the overlay).
#
# Then the profile markers come back out. `config` keeps them, and a UGOS
# project starts a file the plain way — which, with the markers left in,
# would bring up the API and the portal and silently skip the catalog and the
# connector.

# .yaml rather than .yml: that is the name the UGOS project was created
# with, and a project points at one file.
say "rendering $STACK_DIR/docker-compose.yaml"
nas "cd '$APP_DIR' && docker compose --env-file '$STACK_DIR/.env' \
  -f compose.yaml -f infra/nas/compose.nas.yaml \
  ${PROFILES[*]} --profile tools config \
  | python3 -c \"
import sys, yaml
d = yaml.safe_load(sys.stdin)
for name, svc in d.get('services', {}).items():
    svc.pop('profiles', None)
yaml.safe_dump(d, sys.stdout, sort_keys=False)
\" > '$STACK_DIR/docker-compose.yaml'; chmod 600 '$STACK_DIR/docker-compose.yaml' 2>/dev/null || true"

# ---- 6. start it, if this user is allowed to ------------------------------

if nas "docker ps >/dev/null 2>&1"; then
  say "starting the stack over SSH"
  nas "cd '$STACK_DIR' && docker compose up -d --build && docker compose ps"
  echo
  echo "Deployed. LAN: http://$NAS_HOST:3000 (portal), :8080 (api)"
  exit 0
fi

cat <<MSG

Everything is staged on the NAS. The login user cannot reach the Docker
socket, so the last step is the UGOS UI:

  Docker → Project → Create
    Name          terusan
    Path          $STACK_DIR
    Compose file  the docker-compose.yaml already there — do not paste over it
  → Build and start

Afterwards, a redeploy is this script plus **Rebuild** in the same project.

  make nas-status     is the hostname answering, and from where
MSG
