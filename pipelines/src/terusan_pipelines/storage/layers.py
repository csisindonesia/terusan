"""Logical storage layers of the data lake.

The layer set is fixed by program.md §45.2. Physical location is decided
elsewhere (see `config.py` / `resolver.py`); nothing here knows about
filesystems, NAS mounts or buckets.
"""

from __future__ import annotations

from enum import StrEnum


class Layer(StrEnum):
    """A top-level directory of the logical storage hierarchy."""

    RAW = "raw"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    EXPORTS = "exports"
    TEMPORARY = "temporary"


#: Layers that must never be rewritten in place. RAW is the only
#: non-reproducible lake layer (program.md §45.8), so the resolver refuses
#: to hand out write paths for it unless explicitly asked.
IMMUTABLE_LAYERS: frozenset[Layer] = frozenset({Layer.RAW})

#: Layers safe to delete wholesale: everything in them is rebuildable from
#: RAW plus pipeline code, or is bounded by a lifecycle policy.
DISPOSABLE_LAYERS: frozenset[Layer] = frozenset({Layer.EXPORTS, Layer.TEMPORARY})

#: Layers holding Parquet that analytical queries read.
ANALYTICAL_LAYERS: tuple[Layer, ...] = (Layer.BRONZE, Layer.SILVER, Layer.GOLD)
