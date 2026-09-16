"""Extraction: RAW into Bronze."""

from __future__ import annotations

from pathlib import Path

import pytest

from terusan_pipelines.extract import (
    CsvExtractor,
    ExtractionRunner,
    HtmlExtractor,
    JsonExtractor,
    Landed,
    PdfExtractor,
    TextExtractor,
    collapse,
    decode,
    walk_raw,
)
from terusan_pipelines.sources import (
    Artifact,
    Category,
    CollectionMethod,
    Landing,
    SourceMeta,
    SourceType,
)
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import Warehouse


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


@pytest.fixture
def meta() -> SourceMeta:
    return SourceMeta(
        slug="bps",
        name="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
    )


def land(resolver, meta, content: bytes, filename: str, **kw):
    """Put a file in RAW the way a source would."""
    return Landing(resolver).land(
        meta,
        Artifact(content=content, filename=filename, dataset=kw.pop("dataset", "test"), **kw),
    )


def landed_for(resolver, meta, content: bytes, filename: str, **kw) -> Landed:
    result = land(resolver, meta, content, filename, **kw)
    return Landed.from_metadata(Path(result.path).parent / "metadata.json")


# ---- reading provenance back ---------------------------------------------


def test_landed_reads_the_sidecar_written_at_landing(resolver, meta):
    """Extraction inherits provenance rather than re-deriving it."""
    landed = landed_for(
        resolver, meta, b"a,b\n1,2\n", "data.csv", source_url="https://example.invalid/x"
    )
    assert landed.source_slug == "bps"
    assert landed.source_url == "https://example.invalid/x"
    assert landed.content_hash
    assert landed.path.exists()


def test_provenance_records_a_relative_raw_path(resolver, meta):
    """An absolute path would pin Bronze to one backend."""
    landed = landed_for(resolver, meta, b"x", "doc.txt")
    provenance = landed.provenance(resolver.resolve(Layer.RAW), "1")
    assert not provenance["raw_path"].startswith("/")
    assert provenance["raw_path"].startswith("statistics/bps/")
    assert provenance["parser_version"]
    assert provenance["processed_at"]


def test_walk_raw_finds_every_landed_artifact(resolver, meta):
    for i in range(3):
        land(resolver, meta, f"row-{i}".encode(), f"f{i}.txt")
    assert len(list(walk_raw(resolver))) == 3


def test_walk_raw_on_an_empty_lake_is_harmless(resolver):
    assert list(walk_raw(resolver)) == []


def test_walk_raw_ignores_files_without_provenance(resolver, meta):
    """A document with no recorded origin cannot be admitted to Bronze."""
    land(resolver, meta, b"legitimate", "good.txt")
    orphan = Path(resolver.resolve(Layer.RAW, "statistics", "bps")) / "orphan"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "stray.txt").write_text("no sidecar")
    assert len(list(walk_raw(resolver))) == 1


# ---- tabular extraction ---------------------------------------------------


def test_csv_becomes_one_record_per_row(resolver, meta):
    landed = landed_for(resolver, meta, b"month,value\n2026-01,1.2\n2026-02,1.4\n", "cpi.csv")
    rows = list(CsvExtractor().extract(landed))
    assert len(rows) == 2
    assert rows[0]["columns"] == {"month": "2026-01", "value": "1.2"}
    assert rows[1]["row_number"] == 2


def test_csv_values_stay_text(resolver, meta):
    """Bronze does not decide types; a footnote marker next month would flip them."""
    landed = landed_for(resolver, meta, b"value\n1\n", "n.csv")
    assert list(CsvExtractor().extract(landed))[0]["columns"]["value"] == "1"


def test_semicolon_delimiters_are_detected(resolver, meta):
    landed = landed_for(resolver, meta, b"month;value\n2026-01;1.2\n", "eu.csv")
    assert list(CsvExtractor().extract(landed))[0]["columns"]["month"] == "2026-01"


def test_byte_order_mark_does_not_corrupt_the_first_column(resolver, meta):
    landed = landed_for(resolver, meta, "month,value\n2026-01,1.2\n".encode("utf-8-sig"), "b.csv")
    assert "month" in list(CsvExtractor().extract(landed))[0]["columns"]


def test_empty_csv_yields_nothing(resolver, meta):
    landed = landed_for(resolver, meta, b"", "empty.csv")
    assert list(CsvExtractor().extract(landed)) == []


def test_json_list_becomes_one_record_per_element(resolver, meta):
    landed = landed_for(resolver, meta, b'[{"a":1},{"a":2}]', "d.json")
    rows = list(JsonExtractor().extract(landed))
    assert [r["columns"]["a"] for r in rows] == ["1", "2"]


def test_json_api_envelope_is_unwrapped(resolver, meta):
    """Most APIs wrap a result set in a single list-valued key."""
    landed = landed_for(resolver, meta, b'{"status":"ok","data":[{"a":1},{"a":2}]}', "d.json")
    assert len(list(JsonExtractor().extract(landed))) == 2


def test_nested_json_is_flattened_to_dotted_keys(resolver, meta):
    landed = landed_for(resolver, meta, b'[{"period":{"year":2026}}]', "d.json")
    assert list(JsonExtractor().extract(landed))[0]["columns"]["period.year"] == "2026"


def test_jsonl_is_read_line_by_line(resolver, meta):
    landed = landed_for(resolver, meta, b'{"a":1}\n{"a":2}\n', "d.jsonl")
    assert len(list(JsonExtractor().extract(landed))) == 2


def test_invalid_json_fails_with_the_path_attached(resolver, meta):
    landed = landed_for(resolver, meta, b"{not json", "bad.json")
    with pytest.raises(Exception, match="bad.json"):
        list(JsonExtractor().extract(landed))


@pytest.mark.parametrize("raw", [b"caf\xc3\xa9", b"caf\xe9", "menu".encode("utf-8-sig")])
def test_decode_survives_the_usual_encodings(raw):
    assert isinstance(decode(raw), str)


# ---- document extraction --------------------------------------------------


def test_html_yields_text_and_keeps_the_markup(resolver, meta):
    html = b"<html><head><title>Laporan</title></head><body><p>Isi</p></body></html>"
    landed = landed_for(resolver, meta, html, "page.html")
    row = list(HtmlExtractor().extract(landed))[0]
    assert row["title"] == "Laporan"
    assert "Isi" in row["raw_text"]
    assert row["raw_html"] == html.decode()


def test_html_drops_script_and_style_content(resolver, meta):
    html = b"<html><style>.x{color:red}</style><script>var a=1</script><p>Real</p></html>"
    row = list(HtmlExtractor().extract(landed_for(resolver, meta, html, "p.html")))[0]
    assert "color:red" not in row["raw_text"]
    assert "var a" not in row["raw_text"]
    assert "Real" in row["raw_text"]


def test_html_entities_are_unescaped(resolver, meta):
    html = b"<p>PT Antam &amp; Co</p>"
    row = list(HtmlExtractor().extract(landed_for(resolver, meta, html, "e.html")))[0]
    assert "PT Antam & Co" in row["raw_text"]


def test_collapse_keeps_paragraph_breaks():
    """Silver's article splitting depends on them."""
    assert collapse("a\n\n\n\nb") == "a\n\nb"
    assert collapse("a   \t  b") == "a b"


def test_plain_text_titles_from_the_first_line(resolver, meta):
    landed = landed_for(resolver, meta, b"Peraturan Menteri\n\nPasal 1\n", "doc.txt")
    row = list(TextExtractor().extract(landed))[0]
    assert row["title"] == "Peraturan Menteri"


def test_pdf_extractor_claims_pdfs_by_suffix_and_media_type(resolver, meta):
    by_suffix = landed_for(resolver, meta, b"%PDF-1.4", "doc.pdf")
    assert PdfExtractor().handles(by_suffix)


def test_pdf_without_pypdf_reports_the_missing_dependency(resolver, meta):
    """A missing library must leave a visible gap, not a crash."""
    if PdfExtractor.available():
        pytest.skip("pypdf is installed")
    landed = landed_for(resolver, meta, b"%PDF-1.4 broken", "doc.pdf")
    with pytest.raises(Exception, match="pypdf"):
        list(PdfExtractor().extract(landed))


# ---- the runner -----------------------------------------------------------


def test_run_extracts_raw_into_bronze(resolver, meta):
    land(resolver, meta, b"month,value\n2026-01,1.2\n", "cpi.csv")
    land(resolver, meta, b"<html><title>T</title><p>Body</p></html>", "page.html")

    result = ExtractionRunner(resolver).run()
    assert result.documents_seen == 2
    assert result.documents_extracted == 2
    assert result.documents_failed == 0
    assert result.files_written == 2  # documents and records tables


def test_bronze_is_queryable_after_extraction(resolver, meta):
    """The point of the whole layer: RAW in, SQL out."""
    for i in range(3):
        land(resolver, meta, f"month,value\n2026-0{i + 1},1.{i}\n".encode(), f"cpi{i}.csv")
    ExtractionRunner(resolver).run()

    with Warehouse(resolver) as wh:
        wh.view("records", Layer.BRONZE, "records")
        assert wh.query("SELECT count(*) FROM records").fetchone()[0] == 3
        assert wh.query("SELECT count(DISTINCT source_id) FROM records").fetchone()[0] == 1


def test_provenance_survives_into_bronze(resolver, meta):
    """Four layers down, "where did this come from" must still answer."""
    land(resolver, meta, b"a\n1\n", "x.csv", source_url="https://bps.go.id/x")
    ExtractionRunner(resolver).run()

    with Warehouse(resolver) as wh:
        wh.view("records", Layer.BRONZE, "records")
        row = wh.query(
            "SELECT source_id, source_url, content_hash, raw_path FROM records"
        ).fetchone()
    assert row[0] == "bps"
    assert row[1] == "https://bps.go.id/x"
    assert row[2] and row[3]


def test_one_bad_document_does_not_stop_the_corpus(resolver, meta):
    land(resolver, meta, b"a,b\n1,2\n", "good.csv")
    land(resolver, meta, b"{not json at all", "bad.json")

    result = ExtractionRunner(resolver).run()
    assert result.documents_extracted == 1
    assert result.documents_failed == 1
    assert any("bad.json" in path for path in result.failures)


def test_unhandled_formats_are_reported_not_dropped(resolver, meta):
    land(resolver, meta, b"\x00\x01binary", "archive.bin")
    result = ExtractionRunner(resolver).run()
    assert result.documents_skipped == 1
    assert result.documents_extracted == 0
    assert any("archive.bin" in path for path in result.unhandled)


def test_many_small_documents_produce_few_files(resolver, meta):
    """The §47 failure mode: one Parquet file per document."""
    for i in range(50):
        land(resolver, meta, f"a\n{i}\n".encode(), f"f{i}.csv")

    result = ExtractionRunner(resolver).run()
    assert result.documents_extracted == 50
    assert result.files_written == 1


def test_dry_run_extracts_without_writing(resolver, meta):
    land(resolver, meta, b"a\n1\n", "x.csv")
    result = ExtractionRunner(resolver).run(dry_run=True)
    assert result.documents_extracted == 1
    assert result.files_written == 0
    assert not Path(resolver.resolve(Layer.BRONZE, "records")).exists()


def test_limit_stops_early(resolver, meta):
    for i in range(5):
        land(resolver, meta, f"a\n{i}\n".encode(), f"f{i}.csv")
    assert ExtractionRunner(resolver).run(limit=2).documents_extracted == 2


def test_bronze_partitions_by_source(resolver, meta):
    land(resolver, meta, b"a\n1\n", "x.csv")
    ExtractionRunner(resolver).run()
    partitions = list(Path(resolver.resolve(Layer.BRONZE, "records")).glob("source_id=*"))
    assert [p.name for p in partitions] == ["source_id=bps"]


# ---- idempotence ----------------------------------------------------------


def test_re_running_extraction_writes_nothing_new(resolver, meta):
    """Without this, every run appends another copy of the whole corpus."""
    for i in range(3):
        land(resolver, meta, f"a\n{i}\n".encode(), f"f{i}.csv")

    first = ExtractionRunner(resolver).run()
    assert first.documents_extracted == 3

    second = ExtractionRunner(resolver).run()
    assert second.documents_seen == 3
    assert second.documents_unchanged == 3
    assert second.documents_extracted == 0
    assert second.files_written == 0


def test_bronze_row_count_is_stable_across_runs(resolver, meta):
    for i in range(3):
        land(resolver, meta, f"a\n{i}\n".encode(), f"f{i}.csv")
    for _ in range(4):
        ExtractionRunner(resolver).run()

    with Warehouse(resolver) as wh:
        wh.view("records", Layer.BRONZE, "records")
        assert wh.query("SELECT count(*) FROM records").fetchone()[0] == 3


def test_new_documents_are_picked_up_on_a_later_run(resolver, meta):
    land(resolver, meta, b"a\n1\n", "first.csv")
    ExtractionRunner(resolver).run()

    land(resolver, meta, b"a\n2\n", "second.csv")
    second = ExtractionRunner(resolver).run()

    assert second.documents_unchanged == 1
    assert second.documents_extracted == 1

    with Warehouse(resolver) as wh:
        wh.view("records", Layer.BRONZE, "records")
        assert wh.query("SELECT count(*) FROM records").fetchone()[0] == 2


def test_reprocess_forces_re_extraction(resolver, meta):
    """What to use after improving a parser."""
    land(resolver, meta, b"a\n1\n", "x.csv")
    ExtractionRunner(resolver).run()

    forced = ExtractionRunner(resolver).run(reprocess=True)
    assert forced.documents_extracted == 1
    assert forced.documents_unchanged == 0
