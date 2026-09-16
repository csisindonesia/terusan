"""Storage configuration: which physical backend `STORAGE_ROOT` points at.

This is the only module that reads storage environment variables. Everything
downstream takes a `StorageConfig` instance (program.md §45.4).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    LOCAL = "local"
    SHARED_DEV = "shared-dev"
    STAGING = "staging"
    PRODUCTION = "production"


class Backend(StrEnum):
    LOCAL = "local"
    NAS = "nas"
    S3 = "s3"


#: Profiles a developer machine may write to without an explicit override.
#: Absent configuration must never resolve to a shared backend
#: (program.md §45.9), so `local` is the default everywhere.
WRITABLE_BY_DEFAULT: frozenset[Profile] = frozenset({Profile.LOCAL})


class StorageConfig(BaseSettings):
    """Resolved storage settings for one process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    profile: Profile = Field(default=Profile.LOCAL, alias="STORAGE_PROFILE")
    backend: Backend = Field(default=Backend.LOCAL, alias="STORAGE_BACKEND")
    root: str = Field(default="./.data", alias="STORAGE_ROOT")

    # Scratch always lives on local disk. Pointing it at a NAS share degrades
    # DuckDB spill and compaction badly enough to be worth refusing outright
    # (program.md §45.6).
    scratch_dir: Path = Field(default=Path("./.cache"), alias="SCRATCH_DIR")

    s3_endpoint: str | None = Field(default=None, alias="S3_ENDPOINT")
    s3_region: str = Field(default="auto", alias="S3_REGION")
    s3_access_key_id: str | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")
    s3_use_ssl: bool = Field(default=True, alias="S3_USE_SSL")

    #: Set explicitly to allow writes against a shared profile. Guards against
    #: a developer shell inheriting production credentials.
    allow_shared_writes: bool = Field(default=False, alias="STORAGE_ALLOW_SHARED_WRITES")

    @model_validator(mode="after")
    def _check_coherent(self) -> StorageConfig:
        if self.backend is Backend.S3:
            if not self.root.startswith("s3://"):
                raise ValueError(
                    f"backend=s3 requires STORAGE_ROOT to be an s3:// URI, got {self.root!r}"
                )
            if not self.s3_access_key_id or not self.s3_secret_access_key:
                raise ValueError("backend=s3 requires S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
        elif self.root.startswith("s3://"):
            raise ValueError(f"backend={self.backend} cannot use an s3:// STORAGE_ROOT")

        if self.backend is Backend.NAS and not Path(self.root).is_absolute():
            raise ValueError(
                f"backend=nas requires an absolute mount path, got {self.root!r}; "
                "session automounts are not stable enough for pipeline use"
            )
        return self

    @property
    def is_object_storage(self) -> bool:
        return self.backend is Backend.S3

    @property
    def writes_allowed(self) -> bool:
        """Whether this process may write to the lake at all."""
        return self.profile in WRITABLE_BY_DEFAULT or self.allow_shared_writes
