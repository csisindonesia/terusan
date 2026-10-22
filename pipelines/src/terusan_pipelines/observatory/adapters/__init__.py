"""One adapter per provider."""

from __future__ import annotations

from .base import Adapter, BronzeRow
from .google import GoogleAdapter
from .komdigi import KomdigiAdapter
from .manual import MetaAdapter, XAdapter
from .tiktok import TikTokAdapter


def adapters() -> tuple[Adapter, ...]:
    """Fresh adapters for one build. Fresh, because Komdigi's holds the totals
    it is checking between `normalize` and `validate`."""
    return (GoogleAdapter(), TikTokAdapter(), KomdigiAdapter(), MetaAdapter(), XAdapter())


__all__ = [
    "Adapter",
    "BronzeRow",
    "GoogleAdapter",
    "KomdigiAdapter",
    "MetaAdapter",
    "TikTokAdapter",
    "XAdapter",
    "adapters",
]
