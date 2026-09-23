"""Ina-Geoportal — the national geospatial clearing house, and its closed API.

Every boundary in this warehouse has to come from somewhere authoritative, and
this is where: BIG publishes the administrative boundaries, the base maps, the
elevation and the thematic layers that the One Map policy requires every agency
to use. A regency polygon from here and a regency code from Kemendagri are the
two halves of the same fact.

Nothing of it is reachable without a route into the map application. The portal
page is a 677-byte shell that assembles itself in the browser. The API root at
`/api-inageo/` answers — with `{"message":"Welcome to API Inageoportal."}` and
nothing else — and every catalogue path tried under it returns `Cannot GET`.
The routes exist, because the map uses them; they are simply not documented and
not guessable.

Landing the shell and the welcome message daily would be a source that collects
two hundred bytes of nothing and reports success, which is worse than saying
this plainly. The reconnaissance that opens it is an hour with the map
application's network tab.

Administrative boundaries are meanwhile reachable two other ways, neither a
substitute: Kemendagri's register has the codes and names without the geometry
(`kemendagri-wilayah`), and Geofabrik's OSM extract has boundary geometry that
is contributed rather than official (`osm-geofabrik`).
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import GatedSource

SITE = "https://tanahair.indonesia.go.id"

#: Answers, and says only that it is alive. The catalogue routes under it are
#: known to the map application and were not found from outside.
API_ROOT = f"{SITE}/api-inageo/"


class InaGeoportal(GatedSource):
    """The geospatial clearing house."""

    meta = SourceMeta(
        slug="big-inageoportal",
        name="Ina-Geoportal — Badan Informasi Geospasial",
        organization="Badan Informasi Geospasial",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.API,
        base_url=SITE,
        license="Public sector information; per-layer terms",
        update_frequency=UpdateFrequency.IRREGULAR,
        active=False,
        schedule=None,
        notes=(
            "Administrative boundaries, base maps and thematic layers under the "
            "One Map policy — the authoritative geometry every other subnational "
            "figure should be drawn on."
        ),
    )

    access = (
        f"{API_ROOT} answers a welcome message and 404s every guessed path; read "
        "the catalogue routes out of the map application at /map and turn this "
        "into an ApiSource"
    )
