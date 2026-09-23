"""The portal engines, and the registry records the agency sources declare.

These are the three shapes forty-odd Indonesian portals were found to come in
— an API, an index of files, a page that is itself the document — and one
refusal for the ones that cannot be collected at all. What is tested here is
the engine: which links are followed, what lands, what a run's limit means, and
that a failing URL does not discard the rest of the run. The portals' own markup
is not tested, because it is theirs to change.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from terusan_pipelines.sources import (
    Category,
    CollectionMethod,
    Registry,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    portals,
)
from terusan_pipelines.sources.portals import (
    AccessNotProvisioned,
    ApiSource,
    Endpoint,
    FileIndexSource,
    GatedSource,
    PageSource,
    filename_of,
    links,
    year_in,
)

INDEX_PAGE = """
<html><body>
  <a href="/files/laporan-2024.pdf">Laporan 2024</a>
  <a href="/files/laporan-2024.pdf">download</a>
  <a href="https://other.example/tabel-2023.xlsx">Tabel 2023</a>
  <a href="/about.html">Tentang</a>
  <a href="#top">naik</a>
  <a href="javascript:void(0)">menu</a>
  <a href="/files/handbook-of-energy-2025.pdf">Handbook</a>
</body></html>
"""

DOCUMENTS = re.compile(r"\.(pdf|xlsx)(\?|$)", re.IGNORECASE)


@dataclass
class FakeResponse:
    url: str
    content: bytes = b"{}"
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode()


class FakeHttp:
    """A client that answers from a dict and records what was asked for."""

    def __init__(self, pages: dict[str, bytes], fails: set[str] | None = None) -> None:
        self.pages = pages
        self.fails = fails or set()
        self.requested: list[str] = []
        self.opened_with: dict[str, object] = {}

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        if url in self.fails:
            raise RuntimeError(f"pretend {url} is down")
        return FakeResponse(
            url,
            self.pages.get(url, b"{}"),
            headers={
                "content-type": "application/json",
                "last-modified": "Tue, 22 Sep 2026 10:00:00 GMT",
            },
        )

    def try_get(self, url: str, **kwargs: object) -> FakeResponse | None:
        try:
            return self.get(url, **kwargs)
        except RuntimeError:
            return None


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    client = FakeHttp({})

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        client.opened_with = kwargs
        yield client

    monkeypatch.setattr(portals, "fetcher", fake_fetcher)
    return client


# ---- reading a page ------------------------------------------------------


def test_links_are_absolute_deduplicated_and_filtered() -> None:
    found = links(INDEX_PAGE, "https://portal.example/publikasi", DOCUMENTS)

    assert found == (
        "https://portal.example/files/laporan-2024.pdf",
        "https://other.example/tabel-2023.xlsx",
        "https://portal.example/files/handbook-of-energy-2025.pdf",
    )


def test_anchors_and_scripts_are_never_followed() -> None:
    """A `javascript:` href is not a URL, and following `#top` is a loop."""
    assert links(INDEX_PAGE, "https://portal.example/", re.compile(r".")) == (
        "https://portal.example/files/laporan-2024.pdf",
        "https://other.example/tabel-2023.xlsx",
        "https://portal.example/about.html",
        "https://portal.example/files/handbook-of-energy-2025.pdf",
    )


def test_the_year_becomes_the_partition_where_a_url_carries_one() -> None:
    assert year_in("https://x.example/laporan-2024.pdf") == "2024"
    assert year_in("https://x.example/laporan.pdf") is None
    # Not every four digits is a year: an id is not 1823.
    assert year_in("https://x.example/doc/1823") is None


def test_a_filename_is_the_last_segment_or_an_honest_fallback() -> None:
    assert filename_of("https://x.example/a/b/tabel.xlsx") == "tabel.xlsx"
    assert filename_of("https://x.example/", "page.html") == "page.html"


# ---- the API engine ------------------------------------------------------


class _Api(ApiSource):
    meta = SourceMeta(
        slug="test-api",
        name="Test API",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
    )
    endpoints = (
        Endpoint(dataset="one", url="https://api.example/one", filename="one.json"),
        Endpoint(dataset="two", url="https://api.example/two", filename="two.json"),
        Endpoint(dataset="three", url="https://api.example/three", filename="three.json"),
    )


def test_every_endpoint_lands_with_its_provenance(http: FakeHttp) -> None:
    artifacts = list(_Api().collect(ScrapeContext()))

    assert [a.dataset for a in artifacts] == ["one", "two", "three"]
    assert [a.filename for a in artifacts] == ["one.json", "two.json", "three.json"]
    # The headers are the only record of when the portal said this was published.
    assert artifacts[0].metadata["last_modified"] == "Tue, 22 Sep 2026 10:00:00 GMT"
    assert str(artifacts[0].published_at) == "2026-09-22"
    assert artifacts[0].source_url == "https://api.example/one"


def test_a_limit_stops_the_run(http: FakeHttp) -> None:
    assert len(list(_Api().collect(ScrapeContext(limit=2)))) == 2


def test_one_dead_endpoint_does_not_discard_the_others(http: FakeHttp) -> None:
    """A portal having a bad day on one route is not a failed run."""
    http.fails = {"https://api.example/two"}

    artifacts = list(_Api().collect(ScrapeContext()))

    assert [a.dataset for a in artifacts] == ["one", "three"]


# ---- the file index engine -----------------------------------------------


class _Files(FileIndexSource):
    meta = SourceMeta(
        slug="test-files",
        name="Test files",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
    )
    index_urls = ("https://portal.example/publikasi",)
    index_dataset = "listing"
    dataset = "documents"
    file_pattern = DOCUMENTS


@pytest.fixture
def portal(http: FakeHttp) -> FakeHttp:
    http.pages["https://portal.example/publikasi"] = INDEX_PAGE.encode()
    return http


def test_the_index_lands_beside_the_files_it_named(portal: FakeHttp) -> None:
    """A workbook called `Lampiran-3.xlsx` says nothing about itself; the page
    that linked it is the only record of what it was called."""
    artifacts = list(_Files().collect(ScrapeContext()))

    assert artifacts[0].dataset == "listing"
    assert artifacts[0].metadata["role"] == "index"
    assert [a.filename for a in artifacts[1:]] == [
        "laporan-2024.pdf",
        "tabel-2023.xlsx",
        "handbook-of-energy-2025.pdf",
    ]
    # Each file records which page pointed at it.
    assert artifacts[1].metadata["index_url"] == "https://portal.example/publikasi"


def test_the_year_in_a_filename_partitions_it(portal: FakeHttp) -> None:
    artifacts = list(_Files().collect(ScrapeContext()))
    by_name = {a.filename: a for a in artifacts}

    assert by_name["laporan-2024.pdf"].partition == ("year=2024",)
    assert by_name["tabel-2023.xlsx"].partition == ("year=2023",)


def test_an_excluded_file_is_never_fetched(portal: FakeHttp) -> None:
    """A file another source already lands should not be downloaded to be
    discarded — the exclusion has to happen before the request."""

    class _Some(_Files):
        meta = SourceMeta(
            slug="test-files-excluding",
            name="Test files, excluding",
            category=Category.STATISTICS,
            source_type=SourceType.OFFICIAL_PORTAL,
            collection_method=CollectionMethod.BULK_DOWNLOAD,
        )
        exclude_pattern = re.compile(r"handbook-of-energy", re.IGNORECASE)

    artifacts = list(_Some().collect(ScrapeContext()))

    assert "handbook-of-energy-2025.pdf" not in [a.filename for a in artifacts]
    assert not any("handbook" in url for url in portal.requested)


def test_the_ceiling_holds_when_a_ministry_publishes_thousands(portal: FakeHttp) -> None:
    class _Two(_Files):
        meta = SourceMeta(
            slug="test-files-capped",
            name="Test files, capped",
            category=Category.STATISTICS,
            source_type=SourceType.OFFICIAL_PORTAL,
            collection_method=CollectionMethod.BULK_DOWNLOAD,
        )
        max_files = 2

    files = [a for a in _Two().collect(ScrapeContext()) if a.dataset == "documents"]

    assert len(files) == 2


def test_a_listing_of_dataset_pages_is_followed_one_level(http: FakeHttp) -> None:
    """Several portals list datasets and put the file one page deeper. Without
    following, the source lands a catalogue and none of the figures."""
    listing = """
    <a href="/datasets/detail_data/548">Lahan</a>
    <a href="/datasets/detail_data/549">Panen</a>
    <a href="/tentang">Tentang</a>
    """
    http.pages["https://portal.example/datasets"] = listing.encode()
    http.pages["https://portal.example/datasets/detail_data/548"] = (
        b'<a href="/assets/lahan-2024.xlsx">unduh</a>'
    )
    http.pages["https://portal.example/datasets/detail_data/549"] = (
        b'<a href="/assets/panen-2023.xlsx">unduh</a>'
    )

    class _Deep(FileIndexSource):
        meta = SourceMeta(
            slug="test-files-deep",
            name="Test files, one level deep",
            category=Category.STATISTICS,
            source_type=SourceType.OFFICIAL_PORTAL,
            collection_method=CollectionMethod.SCRAPE,
        )
        index_urls = ("https://portal.example/datasets",)
        index_dataset = "listing"
        dataset = "documents"
        file_pattern = DOCUMENTS
        follow_pattern = re.compile(r"/datasets/detail_data/\d+")

    artifacts = list(_Deep().collect(ScrapeContext()))
    files = [a for a in artifacts if a.dataset == "documents"]
    pages = [a for a in artifacts if a.dataset == "listing"]

    assert [a.filename for a in files] == ["lahan-2024.xlsx", "panen-2023.xlsx"]
    # The dataset page is kept too: it carries the title and the period that
    # a workbook called `lahan-2024.xlsx` does not.
    assert [a.metadata["role"] for a in pages] == ["index", "detail", "detail"]
    # `/tentang` does not match the follow pattern and is never opened.
    assert "https://portal.example/tentang" not in http.requested


def test_following_stops_at_its_own_ceiling(http: FakeHttp) -> None:
    """A portal with a thousand dataset pages must not be walked end to end by
    a nightly run, even when none of them holds a file."""
    http.pages["https://portal.example/datasets"] = b"".join(
        f'<a href="/datasets/detail_data/{n}">x</a>'.encode() for n in range(50)
    )

    class _Capped(FileIndexSource):
        meta = SourceMeta(
            slug="test-files-follow-capped",
            name="Test files, follow capped",
            category=Category.STATISTICS,
            source_type=SourceType.OFFICIAL_PORTAL,
            collection_method=CollectionMethod.SCRAPE,
        )
        index_urls = ("https://portal.example/datasets",)
        dataset = "documents"
        follow_pattern = re.compile(r"/datasets/detail_data/\d+")
        max_followed = 5

    list(_Capped().collect(ScrapeContext()))

    opened = [u for u in http.requested if "detail_data" in u]
    assert len(opened) == 5


# ---- the page engine -----------------------------------------------------


class _Pages(PageSource):
    meta = SourceMeta(
        slug="test-pages",
        name="Test pages",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
    )
    dataset = "pages"
    urls = ("https://portal.example/2024/laporan", "https://portal.example/tentang")


def test_pages_land_as_documents_in_their_own_right(http: FakeHttp) -> None:
    artifacts = list(_Pages().collect(ScrapeContext()))

    assert [a.dataset for a in artifacts] == ["pages", "pages"]
    assert [a.media_type for a in artifacts] == ["text/html", "text/html"]
    assert artifacts[0].partition == ("year=2024",)
    assert artifacts[1].partition == ()


# ---- the refusal ---------------------------------------------------------


class _Gated(GatedSource):
    meta = SourceMeta(
        slug="test-gated",
        name="Test gated",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.MANUAL_UPLOAD,
        active=False,
    )
    access = "buy the subscription"


def test_a_gated_source_refuses_rather_than_collecting_nothing() -> None:
    """Zero artifacts and a green run would read as 'the portal published
    nothing this month', which is a different and wrong statement."""
    with pytest.raises(AccessNotProvisioned, match="buy the subscription"):
        list(_Gated().collect(ScrapeContext()))


# ---- what the agency sources declare -------------------------------------


@pytest.fixture(scope="module")
def registry() -> Registry:
    found = Registry()
    found.discover()
    return found


def test_every_gated_source_is_registered_inactive(registry: Registry) -> None:
    """An inactive source is skipped by the scheduler. A gated source that was
    left active would fail every night to say what its notes already say."""
    for source in registry.all():
        if issubclass(source, GatedSource):
            assert not source.meta.active, f"{source.meta.slug} is gated but active"
            assert source.meta.schedule is None, f"{source.meta.slug} is gated but scheduled"


def test_every_gated_source_says_what_would_open_it(registry: Registry) -> None:
    for source in registry.all():
        if issubclass(source, GatedSource):
            access = source.access
            assert access and access != GatedSource.access, (
                f"{source.meta.slug} does not say what access it needs"
            )


def test_every_active_source_names_its_publisher(registry: Registry) -> None:
    """The serving layer joins every dataset to its publisher, so a source
    without one leaves figures in the lake that nobody is attributed for."""
    for source in registry.all():
        meta = source.meta
        if not meta.active or meta.slug.startswith("example-"):
            continue
        assert meta.organization, f"{meta.slug} has no organization"


def test_every_portal_source_links_the_portal(registry: Registry) -> None:
    """A portal source is defined by the portal it reads; a reader following
    a figure back needs the address. Sources fed from a local drop directory
    have no such address and are not portal sources.
    """
    for source in registry.all():
        if not issubclass(source, (ApiSource, FileIndexSource, PageSource, GatedSource)):
            continue
        assert source.meta.base_url, f"{source.meta.slug} has no base_url"


def test_every_collecting_portal_source_is_scheduled(registry: Registry) -> None:
    """A portal source with no schedule only runs when someone remembers.

    Sources that do not fetch are exempt by construction: an imported corpus
    has nothing to schedule, and a gated one is checked above.
    """
    for source in registry.all():
        if not issubclass(source, (ApiSource, FileIndexSource, PageSource)):
            continue
        if not source.meta.active:
            continue
        assert source.meta.schedule, f"{source.meta.slug} is active but has no schedule"


def test_portal_sources_stay_polite(registry: Registry) -> None:
    """These are government file servers, several of them on one host."""
    for source in registry.all():
        if issubclass(source, (ApiSource, FileIndexSource, PageSource)):
            assert source.meta.max_requests_per_second <= 1.0, source.meta.slug


def test_slugs_and_their_packages_do_not_collide(registry: Registry) -> None:
    slugs = [source.meta.slug for source in registry.all()]
    assert len(set(slugs)) == len(slugs)


def test_the_engines_are_not_themselves_registered(registry: Registry) -> None:
    """`ApiSource` and its siblings carry no registry record, and a bug that
    registered one would run an abstract class on a schedule."""
    registered = {source for source in registry.all()}
    for engine in (ApiSource, FileIndexSource, PageSource, GatedSource):
        assert engine not in registered
        assert engine.abstract or issubclass(engine, Source)


# ---- optional, expensive parts of a source -------------------------------


def test_a_string_false_from_the_command_line_means_no() -> None:
    """`--param full=false` arrives as the string "false", which is truthy.

    A source reading it with `bool()` would start a gigabyte download for a run
    that asked it not to.
    """
    from terusan_pipelines.sources.portals import wants

    assert wants(ScrapeContext(params={"full": "true"}), "full")
    assert wants(ScrapeContext(params={"full": True}), "full")
    assert not wants(ScrapeContext(params={"full": "false"}), "full")
    assert not wants(ScrapeContext(params={"full": "0"}), "full")
    assert not wants(ScrapeContext(), "full")


def test_the_osm_extract_is_only_fetched_when_asked_for(http: FakeHttp) -> None:
    """A daily gigabyte of a file that re-hashes whole is not a backup."""
    from terusan_pipelines.sources.osm.geofabrik import IndonesiaExtract

    default = [a.dataset for a in IndonesiaExtract().collect(ScrapeContext())]
    asked = [a.dataset for a in IndonesiaExtract().collect(ScrapeContext(params={"full": "true"}))]

    assert set(default) == {"osm-editions"}
    assert "osm-extract" in asked
