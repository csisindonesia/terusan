"""What the fetch and extract layers refuse to do with hostile input.

Three things, each a way untrusted content from a portal could reach further
than it should: an API key carried in a URL ending up in a log, a redirect or a
sitemap steering the pipelines container at a host on its own network, and an
archive that unpacks to more memory than the NAS has.
"""

from __future__ import annotations

import io
import socket
import struct
import zipfile
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
import structlog

from terusan_pipelines.extract.archives import read_member
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.sources import http as http_module
from terusan_pipelines.sources.http import (
    Fetcher,
    UnsafeURLError,
    ensure_public_url,
    fetcher,
    redact,
)
from terusan_pipelines.sources.news import discover

BPS_KEY = "0123456789abcdef0123456789abcdef"
BPS_URL = f"https://webapi.bps.go.id/v1/api/domain/type/all/key/{BPS_KEY}/"


# --- redaction ---------------------------------------------------------------


def test_redact_blanks_a_key_path_segment_and_secret_query_params():
    assert BPS_KEY not in redact(BPS_URL)
    assert redact(BPS_URL).endswith("/key/REDACTED/")

    url = "https://api.example.org/series?series_id=GDP&api_key=s3cret&access_token=t0k&keyword=gdp"
    shown = redact(url)
    assert "s3cret" not in shown and "t0k" not in shown
    # A parameter that only contains the word keeps its value: it is what makes
    # the log line useful.
    assert "keyword=gdp" in shown and "series_id=GDP" in shown


def test_a_retry_log_line_does_not_carry_the_bps_key():
    calls = iter([httpx.Response(503), httpx.Response(404)])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(calls)))
    http = Fetcher(client, attempts=2, backoff_seconds=0.001, max_backoff_seconds=0.01)

    with structlog.testing.capture_logs() as logs:
        assert http.try_get(BPS_URL) is None

    events = {entry["event"] for entry in logs}
    assert {"http.retry", "http.failed"} <= events
    for entry in logs:
        # httpx quotes the full URL in its status error, which is exactly how
        # the key used to get into the log.
        assert BPS_KEY not in repr(entry)
    retry = next(entry for entry in logs if entry["event"] == "http.retry")
    assert "/key/REDACTED/" in retry["error"]


# --- public hosts only -------------------------------------------------------


@pytest.fixture
def resolves(monkeypatch):
    """Answer DNS from a table instead of the network."""
    table: dict[str, list[str]] = {}

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in table:
            raise socket.gaierror(f"no such host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in table[host]]

    monkeypatch.setattr(http_module.socket, "getaddrinfo", getaddrinfo)
    http_module._resolved_public.cache_clear()
    yield table
    http_module._resolved_public.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/v1/datasets",
        "http://10.1.2.3/",
        "http://192.168.1.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://postgres:5432/",
        "http://api:8080/",
        "http://localhost/",
        "ftp://files.example.org/data.csv",
    ],
)
def test_a_non_public_target_is_refused(url, resolves):
    with pytest.raises(UnsafeURLError):
        ensure_public_url(url)


def test_a_name_that_resolves_privately_is_refused(resolves):
    resolves["intranet.example.org"] = ["203.0.113.9", "10.0.0.5"]
    with pytest.raises(UnsafeURLError, match="non-public"):
        ensure_public_url("https://intranet.example.org/")


def test_a_public_name_is_allowed(resolves):
    resolves["webapi.bps.go.id"] = ["203.123.60.70"]
    ensure_public_url(BPS_URL)


def test_a_name_that_does_not_resolve_is_left_to_fail_as_before(resolves):
    # Refusing it here would turn a DNS blip into a permanent refusal; let
    # through, httpx raises its usual connection error and the retry policy
    # treats it as transient, as it always has.
    ensure_public_url("https://unresolvable.example.org/")


def test_the_refusal_message_does_not_carry_the_key(resolves):
    resolves["webapi.bps.go.id"] = ["10.0.0.1"]
    with pytest.raises(UnsafeURLError) as refused:
        ensure_public_url(BPS_URL)
    assert BPS_KEY not in str(refused.value)


def test_fetcher_refuses_a_redirect_to_a_private_host(resolves):
    resolves["portal.example.org"] = ["93.184.215.14"]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with fetcher(transport=httpx.MockTransport(handler), attempts=1) as http:
        with pytest.raises(UnsafeURLError):
            http.get("https://portal.example.org/download")
        # Through `try_get` it is one more failed URL, not a crashed run.
        assert http.try_get("https://portal.example.org/download") is None

    assert all("169.254" not in url for url in seen)


def test_fetcher_refuses_a_private_host_before_sending(resolves):
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200)

    with (
        fetcher(transport=httpx.MockTransport(handler), attempts=3) as http,
        pytest.raises(UnsafeURLError),
    ):
        http.get("http://redis:6379/")
    # Refused once and not retried: a refusal is not a transient fault.
    assert sent == []


def test_fetcher_still_follows_a_redirect_between_public_hosts(resolves):
    resolves["portal.example.org"] = ["93.184.215.14"]
    resolves["cdn.example.net"] = ["93.184.215.15"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "portal.example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.net/file.csv"})
        return httpx.Response(200, text="a,b\n1,2\n")

    with fetcher(transport=httpx.MockTransport(handler), attempts=1) as http:
        response = http.get("https://portal.example.org/download")
    assert response.text == "a,b\n1,2\n"
    assert str(response.url) == "https://cdn.example.net/file.csv"


# --- child sitemaps stay on the outlet's site --------------------------------


@pytest.mark.parametrize(
    ("url", "host", "same"),
    [
        ("https://aceh.tribunnews.com/sitemap-news.xml", "aceh.tribunnews.com", True),
        ("https://www.tribunnews.com/sitemap/aceh.xml", "aceh.tribunnews.com", True),
        ("https://www.radarlombok.co.id/sitemap-1.xml", "radarlombok.co.id", True),
        ("https://other.co.id/sitemap.xml", "radarlombok.co.id", False),
        ("http://169.254.169.254/sitemap.xml", "radarlombok.co.id", False),
        ("http://postgres/sitemap.xml", "radarlombok.co.id", False),
        ("file:///etc/passwd.xml", "radarlombok.co.id", False),
    ],
)
def test_same_site(url, host, same):
    assert discover._same_site(url, host) is same


def test_a_sitemap_index_cannot_send_the_crawl_off_site():
    index = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>http://internal.example.net/news-sitemap.xml</loc></sitemap>
      <sitemap><loc>https://www.radarlombok.co.id/news-sitemap.xml</loc></sitemap>
    </sitemapindex>"""
    child = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://radarlombok.co.id/2026/10/01/bentrok-warga-di-mataram</loc></url>
    </urlset>"""
    asked: list[str] = []

    class Pages:
        def get(self, url: str):
            asked.append(url)
            if url.endswith("/sitemap-news.xml"):
                return SimpleNamespace(text=index)
            if url == "https://www.radarlombok.co.id/news-sitemap.xml":
                return SimpleNamespace(text=child)
            raise httpx.ConnectError("unreachable")

    outlet = SimpleNamespace(
        host="radarlombok.co.id", base_url="https://radarlombok.co.id", section_url=""
    )
    hits = discover.by_listing(outlet, Pages(), since=date(2026, 1, 1))

    assert "http://internal.example.net/news-sitemap.xml" not in asked
    assert "https://www.radarlombok.co.id/news-sitemap.xml" in asked
    assert [hit.url for hit in hits] == [
        "https://radarlombok.co.id/2026/10/01/bentrok-warga-di-mataram"
    ]


# --- bounded archive reads ---------------------------------------------------


def _archive(name: str, body: bytes) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def test_read_member_returns_a_member_within_the_limit():
    archive = _archive("table.csv", b"a,b\n1,2\n")
    assert read_member(archive, "table.csv", "raw/doc", limit=1024) == b"a,b\n1,2\n"


def test_read_member_refuses_a_member_declared_over_the_limit():
    archive = _archive("bomb.csv", b"0" * 10_000)
    with pytest.raises(ExtractionError, match="declares 10000 bytes"):
        read_member(archive, "bomb.csv", "raw/doc", limit=1024)


def test_read_member_refuses_a_member_whose_header_understates_it():
    # Ten thousand zeros that the headers say are ten bytes. The declared size
    # passes the first check; the bounded read is what catches it.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.csv", b"0" * 10_000)
    raw = bytearray(buffer.getvalue())
    struct.pack_into("<I", raw, raw.find(b"PK\x03\x04") + 22, 10)
    struct.pack_into("<I", raw, raw.find(b"PK\x01\x02") + 24, 10)
    archive = zipfile.ZipFile(io.BytesIO(bytes(raw)))
    assert archive.getinfo("bomb.csv").file_size == 10

    with pytest.raises(ExtractionError):
        read_member(archive, "bomb.csv", "raw/doc", limit=1024)


def test_an_extractor_reports_an_oversized_member_as_a_refusal(monkeypatch, tmp_path):
    from terusan_pipelines.extract import archives, gdelt

    path = tmp_path / "20261001.export.CSV.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("20261001.export.CSV", b"0\t" * 5_000)
    path.write_bytes(buffer.getvalue())

    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 1024)
    with pytest.raises(ExtractionError, match="limit"):
        gdelt._read_archive(path)
