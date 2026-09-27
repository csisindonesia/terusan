#!/usr/bin/env bash
#
# The Earth Engine products, Bronze into Silver.
#
#   ./scripts/normalize-gee.sh                     every product
#   ./scripts/normalize-gee.sh chirps-rainfall     one, by its dataset slug
#
# Each product landed one CSV per period with the indicator already named, so
# each is one `normalize-each` over that column. The list of products is read
# from sources/gee/catalog.py rather than kept here, so a product added there
# is normalized without this script changing.
#
#   period   `2026-08` for a monthly product, `2024` for an annual one
#   value    a province's figure in Anglophone notation — hence
#            --number-format en. Empty where no valid pixel fell on it
#   geo_id   the province's registry id (`ID-31`), which resolves as itself
#
# One product failing does not stop the rest: they are independent series,
# and a gap in one should not hold back thirty others. The failures are listed
# at the end and the script exits non-zero.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -gt 0 ]]; then
  products=("$@")
else
  read -r -a products <<<"$(uv --project pipelines run python -c \
    'from terusan_pipelines.sources.gee.catalog import PRODUCTS; print(" ".join(p.slug for p in PRODUCTS))')"
fi

failed=()
for product in "${products[@]}"; do
  echo "=== $product ==="
  if ! uv --project pipelines run terusan silver normalize-each \
    --by indicator \
    --dataset "$product" \
    --source "gee-$product" \
    --period-column period \
    --value-column value \
    --unit-column unit \
    --geo-column geo_id \
    --name-column series_name \
    --code-column code \
    --publisher-column publisher \
    --number-format en; then
    failed+=("$product")
  fi
done

if [[ ${#failed[@]} -gt 0 ]]; then
  echo "failed: ${failed[*]}" >&2
  exit 1
fi
