"""BPS web API: every catalogue page walked, and the key never landed.

The response shapes are BPS's own, trimmed: a `data` pair of a paging header
and a page of rows, or `"status": "Error"` with a message on a 200.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.bps import webapi
from terusan_pipelines.sources.bps.webapi import BpsError, WebApi

KEY = "0123456789abcdef0123456789abcdef"

#: Pages each catalogue runs to, as BPS reports them.
PAGES = {"subject": 3, "var": 2}


def listing(page: int, pages: int) -> dict[str, Any]:
    return {
        "status": "OK",
        "data-availability": "available",
        "data": [{"page": page, "pages": pages, "per_page": 10}, [{"id": page}]],
    }


class FakeHttp:
    def __init__(self) -> None:
        self.requested: list[str] = []
        self.error_on: str | None = None

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        self.requested.append(url)
        if self.error_on and self.error_on in url:
            body: dict[str, Any] = {"status": "Error", "message": "re-check your key"}
        elif "/domain/type/all/" in url:
            body = {"status": "OK", "data": [{"page": 1, "pages": 1}, [{"domain_id": "0000"}]]}
        else:
            model = url.split("/list/model/")[1].split("/")[0]
            page = int(url.split("/page/")[1].split("/")[0])
            body = listing(page, PAGES[model])
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


class FakeCredentials:
    bps_api_key = SecretStr(KEY)


@pytest.fixture
def bps(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp()

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(webapi, "fetcher", fake_fetcher)
    monkeypatch.setattr(webapi, "credentials", FakeCredentials)
    return http


def test_every_page_of_every_catalogue_is_landed(bps: FakeHttp) -> None:
    """Page one of the variable list is ten of 1,753 variables."""
    artifacts = list(WebApi().collect(ScrapeContext()))

    assert [a.filename for a in artifacts] == [
        "domain.json",
        "subject-p1.json",
        "subject-p2.json",
        "subject-p3.json",
        "var-p1.json",
        "var-p2.json",
    ]
    assert len(bps.requested) == len(artifacts)


def test_domains_come_from_their_own_route(bps: FakeHttp) -> None:
    """`list/model/domain` answers "Model domain is not recognized"."""
    list(WebApi().collect(ScrapeContext()))

    assert bps.requested[0].endswith(f"/domain/type/all/key/{KEY}/")
    assert not any("/list/model/domain/" in url for url in bps.requested)


def test_the_key_is_not_in_what_lands(bps: FakeHttp) -> None:
    """A sidecar is read by anyone with the lake."""
    for artifact in WebApi().collect(ScrapeContext()):
        assert KEY not in (artifact.source_url or "")
        assert "{key}" in (artifact.source_url or "")
        assert KEY not in json.dumps(artifact.metadata)


def test_an_error_body_on_a_200_stops_the_run(bps: FakeHttp) -> None:
    """A revoked key answers 200; landing its message would record an empty
    catalogue as a collected one."""
    bps.error_on = "/list/model/var/"

    with pytest.raises(BpsError, match="re-check your key"):
        list(WebApi().collect(ScrapeContext()))


def test_limit_counts_pages(bps: FakeHttp) -> None:
    artifacts = list(WebApi().collect(ScrapeContext(limit=2)))

    assert len(artifacts) == 2
    assert len(bps.requested) == 2


def test_a_null_body_is_asked_for_again(bps: FakeHttp, monkeypatch: pytest.MonkeyPatch) -> None:
    """BPS sometimes answers 200 with `null` for a table that answers properly
    a moment later."""
    answers = iter([None, None])
    real = bps.get

    def flaky(url: str, **kwargs: object) -> httpx.Response:
        body = next(answers, "real")
        if body == "real":
            return real(url)
        bps.requested.append(url)
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(bps, "get", flaky)
    artifacts = list(WebApi().collect(ScrapeContext(limit=1)))

    assert len(artifacts) == 1
    assert len(bps.requested) == 3


def test_a_body_that_stays_null_is_a_bps_error(
    bps: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    def null(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json=None, request=httpx.Request("GET", url))

    monkeypatch.setattr(bps, "get", null)
    with pytest.raises(BpsError, match="not a JSON object"):
        list(WebApi().collect(ScrapeContext()))
