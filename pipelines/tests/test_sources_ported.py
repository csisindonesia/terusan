"""Sources ported from the earlier lake warehouse.

None of these touch the network: the SEKI index is a captured fixture, and the
fetch paths are exercised through a stubbed transport. A parser that needs a
live site to test is a parser nobody tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from terusan_pipelines.sources import (
    ContentMismatch,
    Fetcher,
    Runner,
    ScrapeContext,
    describe,
    is_transient,
    looks_like_html,
    matches_extension,
    verify,
)
from terusan_pipelines.sources.bank_indonesia import (
    ALLOWED_HOSTS,
    MIN_SUCCESS_RATIO,
    PartialRelease,
    Seki,
    extract_tables,
)
from terusan_pipelines.sources.worldbank import WorldBankGDP
from terusan_pipelines.storage import StorageConfig, StorageResolver

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sources"
SEKI_INDEX = (FIXTURES / "seki_index.html").read_bytes()

#: A minimal but genuine OLE2 header — what a real BIFF .xls starts with.
XLS_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
HTML_ERROR_PAGE = b"<!DOCTYPE html><html><body>Service unavailable</body></html>"


@pytest.fixture
def resolver(tmp_path: Path) -> StorageResolver:
    return StorageResolver(
        StorageConfig(
            STORAGE_PROFILE="local",
            STORAGE_BACKEND="local",
            STORAGE_ROOT=str(tmp_path / "data"),
            SCRATCH_DIR=str(tmp_path / "cache"),
        )
    )


# ---- magic-byte sniffing --------------------------------------------------


def test_html_served_as_excel_is_detected():
    """The failure this exists to catch: a 200 carrying an error page."""
    assert looks_like_html(HTML_ERROR_PAGE)
    assert not matches_extension(HTML_ERROR_PAGE, "xls")
    assert describe(HTML_ERROR_PAGE) == "html"


@pytest.mark.parametrize(
    ("content", "extension"),
    [
        (XLS_BYTES, "xls"),
        (b"PK\x03\x04" + b"\x00" * 16, "xlsx"),
        (b"%PDF-1.4\n", "pdf"),
        (b"PAR1" + b"\x00" * 16, "parquet"),
        (b"\x89PNG\r\n\x1a\n", "png"),
    ],
)
def test_real_signatures_pass(content, extension):
    assert matches_extension(content, extension)
    verify(content, f"file.{extension}")


@pytest.mark.parametrize("extension", ["csv", "json", "txt", "html"])
def test_text_formats_have_no_signature_to_check(extension):
    """A CSV starting with a blank line is still a CSV."""
    verify(b"\n\nmonth,value\n", f"file.{extension}")


def test_verify_rejects_a_mislabelled_binary():
    with pytest.raises(ContentMismatch, match="expected xls, got html"):
        verify(HTML_ERROR_PAGE, "TABEL1_1.xls")


def test_verify_rejects_an_empty_binary():
    with pytest.raises(ContentMismatch, match="empty"):
        verify(b"", "TABEL1_1.xls")


def test_landing_refuses_a_mislabelled_file(resolver):
    """RAW is permanent, so the check belongs before the write, not after."""
    from terusan_pipelines.sources import (
        Artifact,
        Category,
        CollectionMethod,
        Landing,
        SourceMeta,
        SourceType,
    )

    meta = SourceMeta(
        slug="bi-seki",
        name="SEKI",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
    )
    with pytest.raises(ContentMismatch):
        Landing(resolver).land(
            meta,
            Artifact(content=HTML_ERROR_PAGE, filename="TABEL1_1.xls", dataset="tables"),
        )


def test_a_rejected_artifact_is_counted_not_fatal(resolver):
    """One bad table must not discard the other hundred, but must be visible."""
    from collections.abc import Iterator

    from terusan_pipelines.sources import (
        Artifact,
        Category,
        CollectionMethod,
        Source,
        SourceMeta,
        SourceType,
    )

    class Mixed(Source):
        meta = SourceMeta(
            slug="mixed",
            name="Mixed",
            category=Category.STATISTICS,
            source_type=SourceType.OFFICIAL_PORTAL,
            collection_method=CollectionMethod.SCRAPE,
        )

        def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
            yield Artifact(content=XLS_BYTES, filename="good.xls", dataset="tables")
            yield Artifact(content=HTML_ERROR_PAGE, filename="bad.xls", dataset="tables")
            yield Artifact(content=XLS_BYTES, filename="also-good.xls", dataset="tables")

    result = Runner(resolver).run_one(Mixed())
    assert result.succeeded
    assert result.artifacts_landed == 2
    assert result.artifacts_rejected == 1


# ---- retry policy ---------------------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_server_side_failures_are_retried(status):
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status, request=request)
    assert is_transient(httpx.HTTPStatusError("x", request=request, response=response))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_client_side_failures_are_not_retried(status):
    """These mean the source changed or our code is wrong. Retrying only hammers."""
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status, request=request)
    assert not is_transient(httpx.HTTPStatusError("x", request=request, response=response))


def test_transport_errors_are_retried():
    assert is_transient(httpx.ConnectTimeout("timed out"))
    assert is_transient(httpx.ReadError("reset"))


def test_fetcher_retries_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(client, attempts=5, backoff_seconds=0.001, max_backoff_seconds=0.01)
    assert fetcher.get("https://example.invalid").json() == {"ok": True}
    assert attempts["n"] == 3


def test_fetcher_gives_up_after_the_attempt_budget():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    fetcher = Fetcher(client, attempts=2, backoff_seconds=0.001, max_backoff_seconds=0.01)
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.get("https://example.invalid")


def test_try_get_returns_none_instead_of_raising():
    """For fanning out where one failure must not discard the rest."""
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    fetcher = Fetcher(client, attempts=1, backoff_seconds=0.001)
    assert fetcher.try_get("https://example.invalid") is None


def test_the_rate_limiter_is_consulted_before_each_request():
    seen: list[str] = []

    class Recording:
        def acquire(self, url, timeout=None):
            seen.append(url)
            return True

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    Fetcher(client, limiter=Recording()).get("https://example.invalid/a")
    assert seen == ["https://example.invalid/a"]


# ---- the SEKI index parser ------------------------------------------------


def test_every_excel_table_is_found():
    tables = extract_tables(SEKI_INDEX, "https://www.bi.go.id/id/statistik/seki/")
    assert [t.table_id for t in tables] == ["TABEL1_1", "TABEL1_2", "TABEL2_1"]


def test_a_table_inherits_the_section_above_it():
    tables = {t.table_id: t for t in extract_tables(SEKI_INDEX, "https://www.bi.go.id/")}
    assert tables["TABEL1_1"].section == "I. UANG DAN BANK"
    assert tables["TABEL2_1"].section == "II. NERACA PEMBAYARAN"


def test_titles_come_from_the_sibling_cell_not_the_link():
    """The anchors carry an icon, not text."""
    tables = {t.table_id: t for t in extract_tables(SEKI_INDEX, "https://www.bi.go.id/")}
    assert tables["TABEL1_1"].title.startswith("Uang Beredar")
    assert tables["TABEL1_1"].number == "I.1."


def test_html_entities_in_titles_are_decoded():
    tables = {t.table_id: t for t in extract_tables(SEKI_INDEX, "https://www.bi.go.id/")}
    assert tables["TABEL1_2"].title == "Posisi Uang Kartal & Giral"


def test_pdf_only_rows_are_skipped_not_guessed_at():
    tables = extract_tables(SEKI_INDEX, "https://www.bi.go.id/")
    assert "TABEL1_3" not in {t.table_id for t in tables}


def test_links_off_bank_indonesia_are_refused():
    """The index is the one input we do not control, and it decides what we fetch."""
    tables = extract_tables(SEKI_INDEX, "https://www.bi.go.id/")
    assert all(httpx.URL(t.url).host in ALLOWED_HOSTS for t in tables), [t.url for t in tables]
    assert not any("evil.example.invalid" in t.url for t in tables)


def test_relative_links_are_resolved_against_the_index():
    tables = {t.table_id: t for t in extract_tables(SEKI_INDEX, "https://www.bi.go.id/x/")}
    assert tables["TABEL1_1"].url == "https://www.bi.go.id/id/statistik/seki/TABEL1_1.xls"


def test_the_limit_stops_early():
    assert len(extract_tables(SEKI_INDEX, "https://www.bi.go.id/", limit=2)) == 2


def test_an_index_with_no_tables_yields_nothing():
    assert extract_tables(b"<html><body>Maintenance</body></html>", "https://www.bi.go.id/") == []


# ---- SEKI end to end, against a stubbed site ------------------------------


def build_index(count: int) -> bytes:
    """An index listing `count` tables.

    The committed fixture has three tables, which is right for the parser's edge
    cases but useless for the success ratio: at 90%, losing one of three is an
    outage. The real release carries around 108.
    """
    rows = "\n".join(
        f"""<tr>
            <td width="30">I.{i}.</td>
            <td style='text-align:left;'>Tabel {i}</td>
            <td><a href="/id/statistik/seki/TABEL{i}.xls"><img src="xls.gif"></a></td>
        </tr>"""
        for i in range(1, count + 1)
    )
    return (
        "<html><body><table>"
        '<tr><th colspan="4"><b>I. UANG DAN BANK</b></th></tr>'
        f"{rows}</table></body></html>"
    ).encode()


def _seki_transport(
    *, failing: set[str] | None = None, index: bytes = SEKI_INDEX
) -> httpx.MockTransport:
    """A stand-in for bi.go.id: the index, then tables, some of them broken."""
    broken = failing or set()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("Default.aspx"):
            return httpx.Response(200, content=index)
        table_id = path.rsplit("/", 1)[-1].removesuffix(".xls")
        if table_id in broken:
            # What the real site does: a 200 carrying an error page.
            return httpx.Response(200, content=HTML_ERROR_PAGE)
        return httpx.Response(200, content=XLS_BYTES)

    return httpx.MockTransport(handler)


@pytest.fixture
def stub_seki(monkeypatch):
    """Point the SEKI source's fetcher at a stubbed transport."""

    def install(*, failing: set[str] | None = None, index: bytes = SEKI_INDEX) -> None:
        from contextlib import contextmanager

        import terusan_pipelines.sources.bank_indonesia.seki as module

        @contextmanager
        def fake_fetcher(**kwargs):
            transport = _seki_transport(failing=failing, index=index)
            with httpx.Client(transport=transport) as client:
                yield Fetcher(client, attempts=1, backoff_seconds=0.001)

        monkeypatch.setattr(module, "fetcher", fake_fetcher)

    return install


def test_seki_lands_the_index_catalogue_and_tables(resolver, stub_seki):
    stub_seki()
    result = Runner(resolver, keep_paths=True).run_one(Seki())

    assert result.succeeded
    # index.html + catalogue.json + three tables
    assert result.artifacts_landed == 5
    assert any(p.endswith("index.html") for p in result.landed_paths)
    assert any(p.endswith("catalogue.json") for p in result.landed_paths)
    assert sum(1 for p in result.landed_paths if p.endswith(".xls")) == 3


def test_the_catalogue_carries_titles_the_xls_files_do_not(resolver, stub_seki):
    stub_seki()
    result = Runner(resolver, keep_paths=True).run_one(Seki())
    catalogue_path = next(p for p in result.landed_paths if p.endswith("catalogue.json"))
    catalogue = json.loads(Path(catalogue_path).read_text())

    assert len(catalogue) == 3
    assert catalogue[0]["title"].startswith("Uang Beredar")
    assert catalogue[0]["section"] == "I. UANG DAN BANK"


def test_one_broken_table_does_not_fail_the_month(resolver, stub_seki):
    """Above the floor: 19 of 20 is a flaky endpoint, not an outage."""
    stub_seki(index=build_index(20), failing={"TABEL7"})
    result = Runner(resolver, keep_paths=True).run_one(Seki())

    assert result.succeeded
    assert sum(1 for p in result.landed_paths if p.endswith(".xls")) == 19


def test_enough_broken_tables_fail_the_run(resolver, stub_seki):
    """Below the floor: a partial month must not masquerade as a complete one."""
    stub_seki(index=build_index(20), failing={f"TABEL{i}" for i in range(1, 6)})
    result = Runner(resolver).run_one(Seki())

    assert not result.succeeded
    assert "PartialRelease" in (result.error_message or "")
    assert "15/20" in (result.error_message or "")


def test_a_site_wide_outage_fails_the_run(resolver, stub_seki):
    stub_seki(index=build_index(20), failing={f"TABEL{i}" for i in range(1, 21)})
    result = Runner(resolver).run_one(Seki())

    assert not result.succeeded
    assert "0%" in (result.error_message or "")


def test_the_ratio_is_the_only_thing_separating_the_two_cases(resolver, stub_seki):
    """Two failures either side of the floor, same code path."""
    tolerable = 2  # 18/20 = 90%, exactly at the floor
    stub_seki(index=build_index(20), failing={f"TABEL{i}" for i in range(1, tolerable + 1)})
    assert Runner(resolver).run_one(Seki()).succeeded

    stub_seki(index=build_index(20), failing={f"TABEL{i}" for i in range(1, tolerable + 2)})
    assert not Runner(resolver).run_one(Seki()).succeeded


def test_the_success_floor_is_explicit():
    assert MIN_SUCCESS_RATIO == 0.90
    assert issubclass(PartialRelease, RuntimeError)


# ---- World Bank -----------------------------------------------------------


def _worldbank_transport(pages: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        return httpx.Response(
            200,
            json=[
                {"page": page, "pages": pages, "per_page": 2, "total": pages * 2},
                [
                    {
                        "countryiso3code": "IDN",
                        "country": {"value": "Indonesia"},
                        "date": str(2020 + page),
                        "value": 1_000_000.0 * page,
                        "indicator": {"id": "NY.GDP.MKTP.CD"},
                    }
                ],
            ],
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def stub_worldbank(monkeypatch):
    def install(pages: int = 3) -> None:
        from contextlib import contextmanager

        import terusan_pipelines.sources.worldbank.gdp as module

        @contextmanager
        def fake_fetcher(**kwargs):
            with httpx.Client(transport=_worldbank_transport(pages)) as client:
                yield Fetcher(client, attempts=1, backoff_seconds=0.001)

        monkeypatch.setattr(module, "fetcher", fake_fetcher)

    return install


def test_worldbank_lands_one_artifact_per_page(resolver, stub_worldbank):
    stub_worldbank(pages=3)
    result = Runner(resolver, keep_paths=True).run_one(WorldBankGDP())

    assert result.succeeded
    assert result.artifacts_landed == 3
    assert sorted(Path(p).name for p in result.landed_paths) == [
        "page-001.json",
        "page-002.json",
        "page-003.json",
    ]


def test_worldbank_pages_are_landed_as_returned(resolver, stub_worldbank):
    """RAW stays faithful to the response so a better parser can replay it."""
    stub_worldbank(pages=1)
    result = Runner(resolver, keep_paths=True).run_one(WorldBankGDP())
    body = json.loads(Path(result.landed_paths[0]).read_text())

    assert isinstance(body, list)
    assert body[0]["pages"] == 1
    assert body[1][0]["countryiso3code"] == "IDN"


def test_an_unexpected_envelope_stops_the_run(resolver, monkeypatch):
    """A changed API shape must not land rubbish under a familiar name."""
    from contextlib import contextmanager

    import terusan_pipelines.sources.worldbank.gdp as module

    @contextmanager
    def fake_fetcher(**kwargs):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": "deprecated"})
        )
        with httpx.Client(transport=transport) as client:
            yield Fetcher(client, attempts=1, backoff_seconds=0.001)

    monkeypatch.setattr(module, "fetcher", fake_fetcher)
    result = Runner(resolver).run_one(WorldBankGDP())

    assert not result.succeeded
    assert "unexpected API envelope" in (result.error_message or "")


def test_worldbank_re_run_deduplicates(resolver, stub_worldbank):
    """The API returns identical bytes, so a second run should write nothing."""
    stub_worldbank(pages=2)
    runner = Runner(resolver)
    runner.run_one(WorldBankGDP())
    second = runner.run_one(WorldBankGDP())

    assert second.artifacts_deduplicated == 2
    assert second.artifacts_landed == 0


# ---- the World Bank extractor --------------------------------------------


def _worldbank_page(rows: list[dict]) -> bytes:
    return json.dumps([{"page": 1, "pages": 1}, rows], separators=(",", ":")).encode()


def _landed_worldbank(resolver, content: bytes):
    from terusan_pipelines.extract import Landed
    from terusan_pipelines.sources import (
        Artifact,
        Category,
        CollectionMethod,
        Landing,
        SourceMeta,
        SourceType,
    )

    meta = SourceMeta(
        slug="worldbank-gdp",
        name="World Bank",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
    )
    result = Landing(resolver).land(
        meta, Artifact(content=content, filename="page-001.json", dataset="gdp")
    )
    return Landed.from_metadata(Path(result.path).parent / "metadata.json")


def test_the_generic_json_reader_cannot_read_this_envelope(resolver):
    """Why a source-specific extractor exists at all.

    `[metadata, [records...]]` is a two-element list, so a generic reader yields
    two rows: the metadata, and the whole payload flattened into one string.
    """
    from terusan_pipelines.extract import JsonExtractor

    content = _worldbank_page(
        [
            {
                "countryiso3code": "IDN",
                "country": {"value": "Indonesia"},
                "date": "2023",
                "value": 1.0,
            }
        ]
    )
    landed = _landed_worldbank(resolver, content)
    assert len(list(JsonExtractor().extract(landed))) == 2


def test_the_worldbank_extractor_yields_one_row_per_observation(resolver):
    from terusan_pipelines.extract import WorldBankExtractor

    content = _worldbank_page(
        [
            {
                "countryiso3code": "IDN",
                "country": {"value": "Indonesia"},
                "date": "2023",
                "value": 1371166925749.88,
                "indicator": {"id": "NY.GDP.MKTP.CD"},
            },
            {
                "countryiso3code": "MYS",
                "country": {"value": "Malaysia"},
                "date": "2023",
                "value": 399648846857.0,
                "indicator": {"id": "NY.GDP.MKTP.CD"},
            },
        ]
    )
    landed = _landed_worldbank(resolver, content)
    rows = list(WorldBankExtractor().extract(landed))

    assert [r["columns"]["country_iso3"] for r in rows] == ["IDN", "MYS"]
    assert rows[0]["columns"]["year"] == "2023"
    # The value column is named generically: one extractor serves every World
    # Bank series, so it cannot be called gdp_usd.
    assert rows[0]["columns"]["value"] == "1371166925749.88"
    assert rows[0]["columns"]["country_name"] == "Indonesia"


def test_aggregates_are_dropped_to_avoid_double_counting(resolver):
    """ "World" and "Euro area" are sums of the rows beside them."""
    from terusan_pipelines.extract import WorldBankExtractor

    content = _worldbank_page(
        [
            {"countryiso3code": "", "country": {"value": "World"}, "date": "2023", "value": 9.0},
            {
                "countryiso3code": "IDN",
                "country": {"value": "Indonesia"},
                "date": "2023",
                "value": 1.0,
            },
        ]
    )
    rows = list(WorldBankExtractor().extract(_landed_worldbank(resolver, content)))
    assert [r["columns"]["country_iso3"] for r in rows] == ["IDN"]


def test_a_null_value_becomes_an_empty_cell_not_a_zero(resolver):
    """Silver reads the empty string as missing; a zero would be a fabrication."""
    from terusan_pipelines.extract import WorldBankExtractor

    content = _worldbank_page(
        [
            {
                "countryiso3code": "IDN",
                "country": {"value": "Indonesia"},
                "date": "1960",
                "value": None,
            }
        ]
    )
    rows = list(WorldBankExtractor().extract(_landed_worldbank(resolver, content)))
    assert rows[0]["columns"]["value"] == ""


def test_an_empty_page_yields_nothing(resolver):
    """Normal at the end of a paginated pull."""
    from terusan_pipelines.extract import WorldBankExtractor

    content = json.dumps([{"page": 9, "pages": 9}, None]).encode()
    assert list(WorldBankExtractor().extract(_landed_worldbank(resolver, content))) == []


def test_the_extractor_claims_only_its_own_source(resolver):
    from terusan_pipelines.extract import WorldBankExtractor

    landed = _landed_worldbank(resolver, _worldbank_page([]))
    assert WorldBankExtractor().handles(landed)

    other = _landed_worldbank(resolver, _worldbank_page([]))
    object.__setattr__(other, "source_slug", "bps")
    assert not WorldBankExtractor().handles(other)


def test_source_specific_extractors_run_before_the_generic_ones():
    """Otherwise the generic JSON reader would claim the artifact first."""
    from terusan_pipelines.extract import DEFAULT_EXTRACTORS, JsonExtractor, WorldBankExtractor

    kinds = [type(e) for e in DEFAULT_EXTRACTORS]
    assert kinds.index(WorldBankExtractor) < kinds.index(JsonExtractor)


# ---- one shape, many series ----------------------------------------------


def test_every_worldbank_series_shares_one_extractor(resolver):
    """The API publishes thousands of indicators through one envelope."""
    from terusan_pipelines.extract import Landed as LandedDoc
    from terusan_pipelines.extract import WorldBankExtractor
    from terusan_pipelines.sources import (
        Artifact,
        Category,
        CollectionMethod,
        Landing,
        SourceMeta,
        SourceType,
    )

    extractor = WorldBankExtractor()
    for slug, dataset in (("worldbank-gdp", "gdp"), ("worldbank-population", "population")):
        meta = SourceMeta(
            slug=slug,
            name=slug,
            category=Category.STATISTICS,
            source_type=SourceType.GOVERNMENT_API,
            collection_method=CollectionMethod.API,
        )
        content = _worldbank_page(
            [
                {
                    "countryiso3code": "IDN",
                    "country": {"value": "Indonesia"},
                    "date": "2023",
                    "value": 1.0,
                }
            ]
        )
        result = Landing(resolver).land(
            meta, Artifact(content=content, filename="page-001.json", dataset=dataset)
        )
        landed = LandedDoc.from_metadata(Path(result.path).parent / "metadata.json")

        assert extractor.handles(landed), slug
        rows = list(extractor.extract(landed))
        # The Bronze dataset comes from the artifact, so a normalization run
        # reads one series rather than filtering all of them.
        assert rows[0]["dataset"] == dataset


def test_a_series_declares_only_its_code_and_registry_record():
    from terusan_pipelines.sources.worldbank import (
        WorldBankGDP,
        WorldBankIndicator,
        WorldBankPopulation,
    )

    assert issubclass(WorldBankGDP, WorldBankIndicator)
    assert issubclass(WorldBankPopulation, WorldBankIndicator)
    assert WorldBankGDP.indicator_code == "NY.GDP.MKTP.CD"
    assert WorldBankPopulation.indicator_code == "SP.POP.TOTL"
    assert WorldBankGDP.dataset != WorldBankPopulation.dataset


def test_the_base_class_is_not_itself_registrable():
    from terusan_pipelines.sources.worldbank import WorldBankIndicator

    assert WorldBankIndicator.abstract
