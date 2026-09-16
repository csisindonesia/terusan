"""Finding the project root.

`STORAGE_ROOT=./.data` has to mean the same directory whichever one you happen
to be standing in. Without anchoring, running a command from `pipelines/` writes
a second lake at `pipelines/.data` and the first one looks empty — which is a
confusing way to lose an afternoon.

So relative storage paths resolve against the project root, and `.env` is read
from there too.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

#: Set to override the search, for a deployment whose layout differs.
ROOT_ENV_VAR = "TERUSAN_ROOT"

#: A directory is the project root when it holds all of these. Any one alone is
#: too weak: `.git` matches an enclosing repository, and `Makefile` matches
#: plenty of directories that are not this one.
MARKERS = ("pipelines", "reference", "Makefile")


@cache
def project_root() -> Path:
    """The directory holding the project, or the working directory.

    Falls back rather than raising: an installed copy run outside a checkout
    still works, it simply anchors to wherever it was started.
    """
    override = os.getenv(ROOT_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()

    for directory in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        if all((directory / marker).exists() for marker in MARKERS):
            return directory

    return Path.cwd().resolve()


def resolve_path(value: str | Path) -> Path:
    """Anchor a relative path to the project root. Absolute paths pass through."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (project_root() / path).resolve()


def env_file() -> Path:
    """Where `.env` lives: beside the project, not beside the caller."""
    return project_root() / ".env"
