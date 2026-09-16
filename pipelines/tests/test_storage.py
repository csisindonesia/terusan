from __future__ import annotations

import pytest

from terusan_pipelines.storage import (
    Layer,
    StorageConfig,
    StorageResolver,
    StorageWriteRefused,
    UnsafePathSegment,
    slugify,
)


def local_config(tmp_path, **overrides) -> StorageConfig:
    base = {
        "STORAGE_PROFILE": "local",
        "STORAGE_BACKEND": "local",
        "STORAGE_ROOT": str(tmp_path / "data"),
        "SCRATCH_DIR": str(tmp_path / "cache"),
    }
    base.update(overrides)
    return StorageConfig(**base)


# ---- the §45.4 contract: one address, three backends ----------------------


def test_resolve_matches_documented_local_shape(tmp_path):
    resolver = StorageResolver(local_config(tmp_path))
    assert resolver.resolve(Layer.SILVER, "observations", "year=2026") == (
        f"{tmp_path / 'data'}/silver/observations/year=2026"
    )


def test_resolve_matches_documented_nas_shape():
    config = StorageConfig(
        STORAGE_PROFILE="shared-dev",
        STORAGE_BACKEND="nas",
        STORAGE_ROOT="/Volumes/research/terusan",
    )
    resolver = StorageResolver(config)
    assert resolver.resolve(Layer.SILVER, "observations", "year=2026") == (
        "/Volumes/research/terusan/silver/observations/year=2026"
    )


def test_resolve_matches_documented_s3_shape():
    config = StorageConfig(
        STORAGE_PROFILE="production",
        STORAGE_BACKEND="s3",
        STORAGE_ROOT="s3://terusan-warehouse",
        S3_ACCESS_KEY_ID="key",
        S3_SECRET_ACCESS_KEY="secret",
    )
    resolver = StorageResolver(config)
    assert resolver.resolve(Layer.SILVER, "observations", "year=2026") == (
        "s3://terusan-warehouse/silver/observations/year=2026"
    )


def test_s3_scheme_survives_joining():
    """pathlib collapses `s3://` to `s3:/`; the resolver must not."""
    config = StorageConfig(
        STORAGE_BACKEND="s3",
        STORAGE_ROOT="s3://terusan-warehouse",
        S3_ACCESS_KEY_ID="key",
        S3_SECRET_ACCESS_KEY="secret",
    )
    assert StorageResolver(config).resolve(Layer.GOLD).startswith("s3://terusan-warehouse/")


def test_glob_targets_parquet_recursively(tmp_path):
    resolver = StorageResolver(local_config(tmp_path))
    assert resolver.glob(Layer.SILVER, "observations").endswith("/silver/observations/**/*.parquet")


# ---- configuration coherence ---------------------------------------------


def test_s3_backend_rejects_non_s3_root():
    with pytest.raises(ValueError, match="s3:// URI"):
        StorageConfig(
            STORAGE_BACKEND="s3",
            STORAGE_ROOT="/Volumes/research/terusan",
            S3_ACCESS_KEY_ID="key",
            S3_SECRET_ACCESS_KEY="secret",
        )


def test_s3_backend_requires_credentials():
    with pytest.raises(ValueError, match="S3_ACCESS_KEY_ID"):
        StorageConfig(STORAGE_BACKEND="s3", STORAGE_ROOT="s3://terusan-warehouse")


def test_local_backend_rejects_s3_root():
    with pytest.raises(ValueError, match="cannot use an s3://"):
        StorageConfig(STORAGE_BACKEND="local", STORAGE_ROOT="s3://terusan-warehouse")


def test_nas_backend_rejects_relative_mount():
    with pytest.raises(ValueError, match="absolute mount path"):
        StorageConfig(STORAGE_BACKEND="nas", STORAGE_ROOT="./mnt/research")


def test_defaults_are_local_when_nothing_is_configured(monkeypatch, tmp_path):
    """Absent configuration must never reach a shared backend (§45.9)."""
    for key in list(os_environ_keys()):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    config = StorageConfig()
    assert config.profile == "local"
    assert config.backend == "local"
    assert config.writes_allowed


def os_environ_keys() -> list[str]:
    import os

    return [k for k in os.environ if k.startswith(("STORAGE_", "S3_", "SCRATCH_"))]


# ---- write guards ---------------------------------------------------------


def test_shared_profile_is_read_only_by_default(tmp_path):
    config = local_config(tmp_path, STORAGE_PROFILE="production")
    with pytest.raises(StorageWriteRefused, match="read-only"):
        StorageResolver(config).resolve_for_write(Layer.SILVER, "observations")


def test_shared_profile_write_needs_explicit_override(tmp_path):
    config = local_config(
        tmp_path, STORAGE_PROFILE="production", STORAGE_ALLOW_SHARED_WRITES="true"
    )
    assert StorageResolver(config).resolve_for_write(Layer.SILVER, "observations")


def test_raw_layer_refuses_casual_writes(tmp_path):
    resolver = StorageResolver(local_config(tmp_path))
    with pytest.raises(StorageWriteRefused, match="immutable"):
        resolver.resolve_for_write(Layer.RAW, "regulations")


def test_raw_layer_accepts_deliberate_landing(tmp_path):
    resolver = StorageResolver(local_config(tmp_path))
    target = resolver.resolve_for_write(Layer.RAW, "regulations", overwrite_immutable=True)
    assert target.endswith("/raw/regulations")


def test_write_creates_the_directory(tmp_path):
    resolver = StorageResolver(local_config(tmp_path))
    from pathlib import Path

    target = resolver.resolve_for_write(Layer.GOLD, "economics", "inflation")
    assert Path(target).is_dir()


# ---- scratch stays local --------------------------------------------------


def test_scratch_ignores_storage_root(tmp_path):
    config = local_config(tmp_path)
    scratch = StorageResolver(config).scratch("compaction")
    assert scratch.is_dir()
    assert str(tmp_path / "data") not in str(scratch)


# ---- path hygiene ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Peraturan Menteri ESDM", "peraturan-menteri-esdm"),
        ("PP No. 12/2026", "pp-no.-12-2026"),
        ("Ekspor Nikel — Q1", "ekspor-nikel-q1"),
        ("year=2026", "year=2026"),
        ("a:b:c", "a-b-c"),
    ],
)
def test_slugify_produces_safe_segments(raw, expected):
    assert slugify(raw) == expected


def test_slugify_rejects_segments_with_no_content():
    with pytest.raises(UnsafePathSegment):
        slugify("///")


def test_slugify_can_drop_partition_syntax():
    assert slugify("year=2026", allow_partition=False) == "year-2026"


@pytest.mark.parametrize("segment", ["..", ".", "", "a/b", "Uppercase", "with space"])
def test_resolve_rejects_unsafe_segments(tmp_path, segment):
    resolver = StorageResolver(local_config(tmp_path))
    with pytest.raises(UnsafePathSegment):
        resolver.resolve(Layer.SILVER, segment)


def test_ensure_layout_creates_every_layer(tmp_path):
    from pathlib import Path

    resolver = StorageResolver(local_config(tmp_path))
    created = resolver.ensure_layout()
    assert len(created) == len(list(Layer))
    assert all(Path(p).is_dir() for p in created)


def test_ensure_layout_is_a_noop_for_object_storage():
    config = StorageConfig(
        STORAGE_BACKEND="s3",
        STORAGE_ROOT="s3://terusan-warehouse",
        S3_ACCESS_KEY_ID="key",
        S3_SECRET_ACCESS_KEY="secret",
    )
    assert StorageResolver(config).ensure_layout() == []
