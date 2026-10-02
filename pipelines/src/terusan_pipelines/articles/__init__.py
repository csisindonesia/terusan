"""The portal's in-house articles, and the figures each one is written from.

An article on the Portal page states numbers in its prose and draws them in its
charts. Both come from one file per article, built here from the lake, so a
figure in a sentence and the bar beside it cannot disagree, and rebuilding the
file after a pipeline run brings every number up to date at once.

Each article is a module with a `build()` that reads the lake and returns a
JSON-serialisable dict. `ARTICLES` names them by the slug the portal routes on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import rice_dry_season

ARTICLES: dict[str, Callable[[], dict[str, Any]]] = {
    rice_dry_season.SLUG: rice_dry_season.build,
}
