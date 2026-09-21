"""Meta's movement distribution: finding a release, and keeping Indonesia.

The releases are a hundred megabytes of every country Meta reports on, so the
CSV here is six rows of three countries — which is the shape that matters:
the filter, the district label, and the columns a figure carries.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from terusan_pipelines.extract import Landed, MovementDistributionExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.hdx_mobility import indicator_id
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.credentials import SourceCredentials
from terusan_pipelines.sources.hdx import mobility
from terusan_pipelines.sources.hdx.mobility import MovementDistribution, releases

PACKAGE = {
    "name": "movement-distribution",
    "title": "Movement Distribution",
    "license_id": "cc-by",
    "last_modified": "2026-09-16T20:16:06.433591",
    "resources": [
        {
            "id": "adb691f7",
            "name": "Meta Movement Distribution Maps_2026-08-18_to_2026-08-31_.csv",
            "format": "CSV",
            "download_url": "https://data.humdata.org/dataset/x/resource/adb691f7/download/a.csv",
            "last_modified": "2026-09-16T20:16:06.433591",
        },
        {
            "id": "379e2fb7",
            "name": "Meta Movement Distributions Maps_2026-08-01_to_08-16.csv",
            "format": "CSV",
            "download_url": "https://data.humdata.org/dataset/x/resource/379e2fb7/download/b.csv",
            "last_modified": "2026-09-05T18:30:03.905493",
        },
        {
            "id": "767a05fe",
            "name": "Movement Distribution Readme - Data for Good at Meta.pdf",
            "format": "PDF",
            "download_url": "https://data.humdata.org/dataset/x/resource/767a05fe/download/c.pdf",
            "last_modified": "2024-01-18T15:31:11.546743",
        },
    ],
}

CSV = (
    '"gadm_id","gadm_name","country","polygon_level",'
    '"home_to_ping_distance_category","distance_category_ping_fraction","ds"\n'
    '"BRA.4.1_1","Alvarães","BRA","2","[10, 100)","0.02845009162974341","2026-08-18"\n'
    '"IDN.9.4_1","Bogor","IDN","2","0","0.396894560","2026-08-18"\n'
    '"IDN.9.4_1","Bogor","IDN","2","(0, 10)","0.554057205","2026-08-18"\n'
    '"IDN.9.4_1","Bogor","IDN","2","[10, 100)","0.047046393","2026-08-18"\n'
    '"IDN.9.4_1","Bogor","IDN","2","100+","0.002058790","2026-08-18"\n'
    '"IDN.13.3_1","Banjar","IDN","2","0","0.378359112","2026-08-18"\n'
    '"NGA.1.1_1","Aba North","NGA","2","0","0.5","2026-08-18"\n'
)


# -- finding the releases ---------------------------------------------------


def test_the_csv_releases_are_found_newest_first() -> None:
    assert [release.resource_id for release in releases(PACKAGE)] == ["adb691f7", "379e2fb7"]


def test_the_readme_is_not_a_release() -> None:
    """It is documentation, and landing it under the data dataset would put a
    leaflet where the figures belong."""
    assert all(release.url.endswith(".csv") for release in releases(PACKAGE))


def test_a_release_is_named_by_its_resource_id() -> None:
    """Meta's own names collide: two releases differ only in their dates."""
    assert releases(PACKAGE)[0].filename == "adb691f7.csv"


def test_the_date_range_is_read_off_the_resource_name() -> None:
    assert releases(PACKAGE)[0].covers == "2026-08-18"


# -- collecting -------------------------------------------------------------


class FakeResponse:
    def __init__(self, url: str, body: bytes) -> None:
        self.url = url
        self.content = body
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return json.loads(self.content)


class FakeHttp:
    def __init__(self, package: dict) -> None:
        self.package = package
        self.requested: list[str] = []
        #: What the source asked the client to be built with, so a test can
        #: assert on the credential without a live request.
        self.opened_with: dict[str, object] = {}

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        if url.endswith(".csv"):
            return FakeResponse(url, CSV.encode())
        body = {"success": True, "result": self.package}
        return FakeResponse(url, json.dumps(body).encode())


@pytest.fixture
def hdx(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp(PACKAGE)

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        http.opened_with = kwargs
        yield http

    monkeypatch.setattr(mobility, "fetcher", fake_fetcher)
    # No credential unless a test sets one: a developer with a token in their
    # own `.env` must not make these pass for a reason CI does not have.
    monkeypatch.setattr(mobility, "credentials", lambda: SourceCredentials(HDX_API_TOKEN=None))
    return http


def test_a_run_lands_the_listing_and_the_newest_release(hdx: FakeHttp) -> None:
    """The listing because a CSV states neither its date range nor its
    license; one release because the archive is over a gigabyte."""
    artifacts = list(MovementDistribution().collect(ScrapeContext()))

    assert [artifact.dataset for artifact in artifacts] == [
        mobility.LISTING_DATASET,
        mobility.DATASET,
    ]
    assert artifacts[0].filename == "package.json"
    assert artifacts[1].filename == "adb691f7.csv"
    assert artifacts[1].partition == ("year=2026", "month=08")
    assert artifacts[1].published_at == date(2026, 9, 16)
    assert artifacts[1].metadata["resource_id"] == "adb691f7"


def test_limit_asks_for_more_releases(hdx: FakeHttp) -> None:
    artifacts = list(MovementDistribution().collect(ScrapeContext(limit=2)))

    assert [artifact.metadata.get("resource_id") for artifact in artifacts[1:]] == [
        "adb691f7",
        "379e2fb7",
    ]


def test_since_leaves_earlier_releases_alone(hdx: FakeHttp) -> None:
    context = ScrapeContext(since=date(2026, 9, 10), limit=5)
    artifacts = list(MovementDistribution().collect(context))

    assert [artifact.metadata.get("resource_id") for artifact in artifacts[1:]] == ["adb691f7"]


def test_a_package_with_no_csv_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = {**PACKAGE, "resources": [PACKAGE["resources"][2]]}
    http = FakeHttp(empty)

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(mobility, "fetcher", fake_fetcher)

    with pytest.raises(ValueError, match="lists no CSV resources"):
        list(MovementDistribution().collect(ScrapeContext()))


def test_hdx_answering_without_a_package_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Refusing(FakeHttp):
        def get(self, url: str, **kwargs: object) -> FakeResponse:
            body = {"success": False, "error": {"message": "Not found"}}
            return FakeResponse(url, json.dumps(body).encode())

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[Refusing]:
        yield Refusing(PACKAGE)

    monkeypatch.setattr(mobility, "fetcher", fake_fetcher)

    with pytest.raises(ValueError, match="did not return a package"):
        list(MovementDistribution().collect(ScrapeContext()))


# -- reading a release ------------------------------------------------------


def landed(tmp_path: Path, body: str = CSV) -> Landed:
    path = tmp_path / "adb691f7.csv"
    path.write_text(body, encoding="utf-8")
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="hdx-meta-movement-distribution",
        dataset="movement-distribution",
        extra={"resource_id": "adb691f7", "resource_name": "Movement Distribution"},
    )


def test_only_indonesia_is_kept(tmp_path: Path) -> None:
    """The file is global and this warehouse is not: read generically, one
    release puts a million rows of Brazil and Nigeria into Bronze."""
    rows = list(MovementDistributionExtractor().extract(landed(tmp_path)))

    assert {row["columns"]["country"] for row in rows} == {"IDN"}
    assert len(rows) == 5


def test_each_distance_band_is_its_own_series(tmp_path: Path) -> None:
    rows = list(MovementDistributionExtractor().extract(landed(tmp_path)))

    assert [row["columns"]["indicator"] for row in rows] == [
        "meta_movement_0km",
        "meta_movement_0_10km",
        "meta_movement_10_100km",
        "meta_movement_100km_plus",
        "meta_movement_0km",
    ]


def test_a_district_is_named_the_way_the_registry_resolves_it(tmp_path: Path) -> None:
    """The bare name, which the geography registry answers for, with the GADM
    code kept beside it as a fact about the source rather than part of the
    place's name."""
    rows = list(MovementDistributionExtractor().extract(landed(tmp_path)))

    assert rows[0]["columns"]["district"] == "Bogor"
    assert rows[-1]["columns"]["district"] == "Banjar"
    assert rows[0]["columns"]["gadm_name"] == "Bogor"
    assert rows[0]["columns"]["gadm_id"] == "IDN.9.4_1"


def test_the_district_name_resolves_to_a_regency() -> None:
    """The point of the change: these labels used to resolve to nothing."""
    from terusan_pipelines.normalize.reference import geography_registry

    registry = geography_registry()
    assert registry.resolve("Bogor").identifier == "ID-32.01"
    # Two places called Banjar, written differently by BPS, and both resolve.
    assert registry.resolve("Banjar").identifier == "ID-63.03"
    assert registry.resolve("Kota Banjar").identifier == "ID-32.79"
    # A name a province and a regency both answer to resolves to neither.
    assert registry.resolve("Gorontalo").identifier is None


def test_the_figure_is_carried_as_written(tmp_path: Path) -> None:
    rows = list(MovementDistributionExtractor().extract(landed(tmp_path)))

    assert rows[0]["columns"]["value"] == "0.396894560"
    assert rows[0]["columns"]["period"] == "2026-08-18"
    assert rows[0]["columns"]["unit"] == "share of movements"


def test_the_band_keeps_its_brackets(tmp_path: Path) -> None:
    """`[10, 100)` says whether 100 km is in the band, and the label says it
    in words for a reader."""
    rows = list(MovementDistributionExtractor().extract(landed(tmp_path)))

    assert rows[2]["columns"]["category"] == "[10, 100)"
    assert rows[2]["columns"]["category_label"] == "10–100 km"


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("0", "meta_movement_0km"),
        ("(0, 10)", "meta_movement_0_10km"),
        ("[10, 100)", "meta_movement_10_100km"),
        ("100+", "meta_movement_100km_plus"),
        ("200 to 500", "meta_movement_200_to_500"),
    ],
)
def test_a_band_becomes_an_identifier(category: str, expected: str) -> None:
    assert indicator_id(category) == expected


def test_a_run_without_a_token_still_collects(hdx: FakeHttp) -> None:
    """The package listing and the CSVs are public; the token is an extra."""
    artifacts = list(MovementDistribution().collect(ScrapeContext()))

    assert len(artifacts) == 2
    assert "Authorization" not in (hdx.opened_with.get("headers") or {})


def test_a_token_is_sent_on_every_request(hdx: FakeHttp, monkeypatch: pytest.MonkeyPatch) -> None:
    """CKAN takes the token bare, not as `Bearer <token>`."""
    monkeypatch.setattr(mobility, "credentials", lambda: SourceCredentials(HDX_API_TOKEN="a-token"))

    list(MovementDistribution().collect(ScrapeContext()))

    assert hdx.opened_with["headers"] == {"Authorization": "a-token"}


def test_a_reshaped_release_is_refused(tmp_path: Path) -> None:
    """Rather than yielding rows of empty strings, which read downstream as a
    district with no figures instead of a broken parse."""
    body = '"gadm_id","country","ds"\n"IDN.9.4_1","IDN","2026-08-18"\n'

    with pytest.raises(ExtractionError, match="missing the columns"):
        list(MovementDistributionExtractor().extract(landed(tmp_path, body)))


def test_a_release_without_indonesia_is_an_error(tmp_path: Path) -> None:
    """Meta has dropped the country or changed how it writes the code, and
    the silence is the thing worth surfacing."""
    body = CSV.replace('"IDN"', '"IDX"')

    with pytest.raises(ExtractionError, match="no rows for country"):
        list(MovementDistributionExtractor().extract(landed(tmp_path, body)))


def test_only_this_source_is_claimed(tmp_path: Path) -> None:
    other = Landed(
        path=landed(tmp_path).path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="fred-indonesia",
        dataset="fred-indonesia-series",
    )

    assert MovementDistributionExtractor().handles(landed(tmp_path))
    assert not MovementDistributionExtractor().handles(other)
