"""Storage addressing for the data lake.

Import `StorageResolver` here rather than constructing paths by hand; see
program.md §45.4.
"""

from .config import Backend, Profile, StorageConfig
from .layers import (
    ANALYTICAL_LAYERS,
    DISPOSABLE_LAYERS,
    IMMUTABLE_LAYERS,
    Layer,
)
from .paths import UnsafePathSegment, slugify
from .resolver import StorageResolver, StorageWriteRefused
from .root import project_root, resolve_path

__all__ = [
    "ANALYTICAL_LAYERS",
    "DISPOSABLE_LAYERS",
    "IMMUTABLE_LAYERS",
    "Backend",
    "Layer",
    "Profile",
    "StorageConfig",
    "StorageResolver",
    "StorageWriteRefused",
    "UnsafePathSegment",
    "project_root",
    "resolve_path",
    "slugify",
]
