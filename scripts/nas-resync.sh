#!/usr/bin/env bash
#
# Bring the NAS's Silver up to the laptop's: finish BPS, add ADB and the
# metals, sweep duplicates, republish the catalogue.
#
# Runs inside the pipelines container, which holds only pipelines/ and a venv
# at /opt/venv — no scripts/ and no uv project to sync. So the scripts are
# copied in first, and a shim stands in for `uv --project pipelines run`,
# running the command straight from the venv. Every step runs; a failure is
# logged and the next goes ahead.
#
# From the laptop, after `make nas-deploy`:
#
#   ssh terusan-nas 'docker cp /volume2/terusan/app/scripts terusan-pipelines-1:/app/ \
#     && docker exec -d terusan-pipelines-1 bash -c \
#        "bash /app/scripts/nas-resync.sh > /data/temporary/resync.log 2>&1"'
#
# `--bps-only` skips ADB and the metals, for a lake that already has them.
#
# Progress:  ssh terusan-nas tail -f /volume2/terusan/lake/temporary/resync.log

set -uo pipefail

mkdir -p /tmp/shim
cat > /tmp/shim/uv <<'EOF'
#!/bin/sh
# `uv --project X run CMD...` -> CMD..., straight from the image's venv.
while [ $# -gt 0 ]; do case "$1" in --project) shift 2;; run) shift; break;; *) shift;; esac; done
exec "$@"
EOF
chmod +x /tmp/shim/uv
export PATH=/tmp/shim:/opt/venv/bin:$PATH
cd /app

failed=()
step() {
  echo "=== $(date '+%F %T') $*"
  if "$@"; then echo "--- ok: $*"; else echo "!!! FAILED ($?): $*"; failed+=("$*"); fi
}

# 1. BPS. The script packs its batches by Bronze rows, which keeps each one
#    under what the NAS's earlyoom tolerates.
step ./scripts/normalize-bps.sh

if [[ "${1:-}" != "--bps-only" ]]; then

# 2. ADB.
step terusan sources run adb-kidb
step terusan warehouse extract statistics adb-kidb
step ./scripts/normalize-adb.sh

# 3. The metals, never collected on the NAS.
MARKETS=(
  westmetall-lme
  sina-shfe-nickel sina-shfe-tin sina-shfe-stainless sina-shfe-aluminium
  sina-shfe-zinc sina-dce-iron-ore sina-dce-coking-coal
  yahoo-aluminium yahoo-zinc yahoo-iron-ore yahoo-silver yahoo-platinum
  yahoo-palladium yahoo-hot-rolled-coil
  tradingeconomics-coal
)
for source in "${MARKETS[@]}"; do
  step terusan sources run "$source"
  step terusan warehouse extract statistics "$source"
done
step ./scripts/normalize-lme.sh
step ./scripts/normalize-sina.sh
step ./scripts/normalize-yahoo.sh
step ./scripts/normalize-coal.sh
fi

# 4. Duplicates, then the catalogue.
step terusan silver dedupe --show 0
step terusan silver dimensions
step terusan silver documents

echo "=== $(date '+%F %T') done; ${#failed[@]} failed step(s)"
for f in "${failed[@]}"; do echo "  - $f"; done
echo RESYNC-FINISHED
