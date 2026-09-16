"""The Python resolver must satisfy the shared cross-language contract.

`services/api/internal/storage` runs the same fixture. If these two ever
disagree, the pipelines write a dataset to one path and the API reads another
(program.md §45.4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terusan_pipelines.storage import (
    Layer,
    StorageConfig,
    StorageResolver,
    UnsafePathSegment,
    slugify,
)

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "storage" / "contract.json").read_text()
)


def config_for(case: dict) -> StorageConfig:
    extra = {}
    if case["backend"] == "s3":
        extra = {"S3_ACCESS_KEY_ID": "key", "S3_SECRET_ACCESS_KEY": "secret"}
    return StorageConfig(STORAGE_BACKEND=case["backend"], STORAGE_ROOT=case["root"], **extra)


@pytest.mark.parametrize("case", CONTRACT["resolve"], ids=lambda c: c["expected"])
def test_resolve_matches_contract(case):
    resolver = StorageResolver(config_for(case))
    assert resolver.resolve(Layer(case["layer"]), *case["segments"]) == case["expected"]


@pytest.mark.parametrize("case", CONTRACT["slugify"], ids=lambda c: c["input"])
def test_slugify_matches_contract(case):
    assert slugify(case["input"]) == case["expected"]


@pytest.mark.parametrize("segment", CONTRACT["unsafe_segments"])
def test_unsafe_segments_are_rejected(segment):
    resolver = StorageResolver(StorageConfig(STORAGE_BACKEND="local", STORAGE_ROOT="./.data"))
    with pytest.raises(UnsafePathSegment):
        resolver.resolve(Layer.SILVER, segment)
