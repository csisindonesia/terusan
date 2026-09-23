"""HEESI: finding an edition, and landing it.

Only the acquisition is tested, because only the acquisition exists. The
handbook is collected as a document — it is the reference an energy question
is answered from — and nothing reads its tables: the parser for those was
taken out unfinished, so no figures are published from this and none are
tested here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest

from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.esdm import heesi as source_module
from terusan_pipelines.sources.esdm.heesi import Handbook, editions

#: How ESDM links the editions: one per year in the body, the newest repeated
#: in a sidebar, and the filenames spelled three different ways.
_HREF = "/assets/media/content/content-handbook-of-energy"
LINKS = [
    f"{_HREF}-and-economic-statistics-of-indonesia-2025.pdf",
    f"{_HREF}-and-economic-statistics-of-indonesia-2024.pdf",
    f"{_HREF}-and-economic-statistics-of-indonesia-2018-final-edition.pdf",
    f"{_HREF}-economic-statistics-of-indonesia-2010-c19rfkq.pdf",
    "/assets/media/content/some-other-publication.pdf",
    f"{_HREF}-and-economic-statistics-of-indonesia-2025.pdf",
]
PAGE = "<html><body>{}</body></html>".format(
    "".join(f'<a href="{link}">edition</a>' for link in LINKS)
)


# -- finding the editions ---------------------------------------------------


def test_every_edition_on_the_page_is_found_newest_first() -> None:
    assert [edition.year for edition in editions(PAGE)] == [2025, 2024, 2018, 2010]


def test_an_edition_linked_twice_is_one_edition() -> None:
    """ESDM links the newest edition in the body and again in a sidebar."""
    assert [edition.year for edition in editions(PAGE)].count(2025) == 1


def test_another_publication_is_not_a_handbook() -> None:
    assert all("handbook-of-energy" in edition.url for edition in editions(PAGE))


def test_the_year_is_read_before_a_filename_suffix() -> None:
    """`...-of-indonesia-2018-final-edition.pdf` states its year mid-name."""
    found = {edition.year: edition.filename for edition in editions(PAGE)}

    assert found[2018].endswith("2018-final-edition.pdf")


def test_a_page_without_handbooks_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rather than a guessed URL, which either 404s or lands last year's
    figures under this year's edition."""
    fetch(monkeypatch, "<html><body>Under maintenance</body></html>")

    with pytest.raises(ValueError, match="markup has changed"):
        list(Handbook().collect(ScrapeContext()))


# -- collecting -------------------------------------------------------------


class FakeResponse:
    def __init__(self, url: str, body: bytes) -> None:
        self.url = url
        self.content = body
        self.text = body.decode()
        self.headers: dict[str, str] = {}


class FakeHttp:
    def __init__(self, page: str) -> None:
        self.page = page
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        if url.endswith(".pdf"):
            return FakeResponse(url, b"%PDF-1.7 pretend handbook")
        return FakeResponse(url, self.page.encode())


def fetch(monkeypatch: pytest.MonkeyPatch, page: str = PAGE) -> FakeHttp:
    http = FakeHttp(page)

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(source_module, "fetcher", fake_fetcher)
    return http


def test_a_run_takes_the_newest_edition_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fifteen editions of ten-megabyte PDFs is a deliberate pull, not a
    default one."""
    fetch(monkeypatch)

    artifacts = list(Handbook().collect(ScrapeContext()))

    assert len(artifacts) == 1
    assert artifacts[0].partition == ("edition=2025",)
    assert artifacts[0].metadata["edition"] == "2025"
    assert artifacts[0].published_at == date(2025, 12, 31)
    assert artifacts[0].media_type == "application/pdf"


def test_limit_asks_for_more_editions(monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts = list(Handbook().collect(ScrapeContext(limit=3)))

    assert [artifact.metadata["edition"] for artifact in artifacts] == ["2025", "2024", "2018"]


def test_since_leaves_the_older_editions_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch(monkeypatch)

    artifacts = list(Handbook().collect(ScrapeContext(since=date(2025, 1, 1), limit=10)))

    assert [artifact.metadata["edition"] for artifact in artifacts] == ["2025"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here reaches ESDM."""
    fetch(monkeypatch)
