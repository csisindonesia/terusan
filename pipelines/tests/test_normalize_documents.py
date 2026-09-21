"""The document catalogue: classification, titles, and the row it produces."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from terusan_pipelines.extract.base import Landed
from terusan_pipelines.identifiers import dataset_code
from terusan_pipelines.normalize.documents import (
    classify,
    humanize,
    is_opaque,
    is_readable,
    row,
    subtitle_for,
    title_for,
)


def landed(**kw) -> Landed:
    defaults = {
        "path": Path("/lake/raw/statistics/esdm-heesi/handbook/edition=2025/doc_abc/file.pdf"),
        "document_id": "doc_abc",
        "content_hash": "abc123",
        "source_slug": "esdm-heesi",
        "media_type": "application/pdf",
        "original_filename": "file.pdf",
        "dataset": "handbook",
    }
    defaults.update(kw)
    return Landed(**defaults)


# ---- classification -------------------------------------------------------


def test_a_source_that_states_its_type_is_believed():
    document = landed(media_type="text/html", extra={"document_type": "publication"})
    assert classify(document) == "publication"


def test_a_stated_type_outside_the_vocabulary_is_ignored():
    """Otherwise a typo in one scraper puts an unfilterable value in the facet."""
    document = landed(media_type="text/html", extra={"document_type": "handbook"})
    assert classify(document) == "web_page"


def test_an_extractor_beats_the_media_type_but_not_the_source():
    assert classify(landed(media_type="text/html"), "news") == "news"
    assert classify(landed(extra={"document_type": "report"}), "news") == "report"


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("application/pdf", "publication"),
        ("text/html", "web_page"),
        ("text/html; charset=utf-8", "web_page"),
        ("text/csv", "data_file"),
        ("application/vnd.ms-excel", "data_file"),
        (None, "data_file"),
    ],
)
def test_the_media_type_decides_when_nothing_else_does(media_type, expected):
    assert classify(landed(media_type=media_type)) == expected


def test_an_unread_download_is_a_data_file_rather_than_a_report():
    """Promoting a spreadsheet to "report" would be the catalogue lying."""
    assert classify(landed(media_type="application/zip")) == "data_file"


@pytest.mark.parametrize(
    "media_type",
    [
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.oasis.opendocument.text",
        "application/rtf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
)
def test_word_processor_formats_are_documents(media_type):
    """Nobody issues a .docx for a machine to parse."""
    assert is_readable(classify(landed(media_type=media_type)))


@pytest.mark.parametrize(
    "media_type",
    [
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "text/html",
        "application/json",
    ],
)
def test_spreadsheets_and_pages_are_not_documents(media_type):
    """A .xlsx is how an agency ships a table, not something anyone reads.

    The distinction is what keeps the Documents page from burying the one
    handbook under fifteen hundred rows of machinery.
    """
    assert not is_readable(classify(landed(media_type=media_type)))


def test_the_handbook_is_readable_and_the_crawl_behind_it_is_not():
    assert is_readable(classify(landed(media_type="application/pdf")))
    assert not is_readable(classify(landed(media_type="text/html")))


# ---- titles ---------------------------------------------------------------


def test_a_stated_title_wins():
    document = landed(extra={"title": "Handbook of Energy 2025"})
    assert title_for(document, "Something the parser read") == "Handbook of Energy 2025"


def test_a_publishers_resource_name_is_used_without_its_extension():
    document = landed(extra={"resource_name": "Movement Distribution_2026-07-13.csv"})
    # The dates survive: humanising them would turn 2026-07-13 into three words.
    assert title_for(document) == "Movement Distribution_2026-07-13"


def test_an_extracted_title_is_used_when_the_sidecar_states_none():
    assert title_for(landed(extra={}), "Statistik Ekonomi") == "Statistik Ekonomi"


def test_a_filename_is_humanised_as_a_last_resort():
    document = landed(original_filename="handbook-of-energy-and-economic-statistics.pdf", extra={})
    assert title_for(document) == "Handbook of Energy and Economic Statistics"


def test_an_identifier_filename_is_not_dressed_up_as_a_title():
    """A UUID humanised looks like a title, so a reader stops asking what it is."""
    document = landed(original_filename="7321e08f-eac6-46e8-ba43-98b2bcce04b4.csv", extra={})
    assert title_for(document) == "Handbook (doc_abc)"


def test_a_document_with_nothing_to_go_on_is_still_named():
    document = landed(original_filename=None, dataset=None, extra={})
    assert title_for(document) == "Document (doc_abc)"


@pytest.mark.parametrize(
    ("stem", "opaque"),
    [
        ("7321e08f-eac6-46e8-ba43-98b2bcce04b4", True),
        ("d41d8cd98f00b204e9800998ecf8427e", True),
        ("handbook-of-energy-2025", False),
        ("table-1a", False),
        ("", True),
    ],
)
def test_opaque_stems_are_told_from_real_names(stem, opaque):
    assert is_opaque(stem) is opaque


def test_minor_words_and_acronyms_survive_title_casing():
    assert humanize("handbook-of-energy-and-esdm-data") == "Handbook of Energy and ESDM Data"


# ---- the row --------------------------------------------------------------


def test_the_partition_becomes_a_readable_subtitle():
    assert subtitle_for(landed(partition=("edition=2025",))) == "edition 2025"
    assert subtitle_for(landed(partition=("year=2026", "month=07"))) == "year 2026, month 07"
    assert subtitle_for(landed(partition=())) is None


def test_the_row_carries_what_the_sidecar_recorded():
    document = landed(
        partition=("edition=2025",),
        size_bytes=10_962_809,
        published_at=date(2025, 12, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=UTC),
        source_url="https://esdm.go.id/handbook.pdf",
        source_organization="Kementerian ESDM",
        extra={"title": "Handbook of Energy 2025"},
    )
    built = row(
        document,
        raw_root="/lake/raw",
        pipeline_version="1",
        publisher=document.source_organization,
        links=(666, 1595),
    )

    assert built["document_id"] == "doc_abc"
    assert built["document_type"] == "publication"
    assert built["title"] == "Handbook of Energy 2025"
    assert built["subtitle"] == "edition 2025"
    assert built["publisher"] == "Kementerian ESDM"
    assert built["size_bytes"] == 10_962_809
    assert built["indicator_count"] == 666
    assert built["observation_count"] == 1595
    assert built["dataset_slug"] == "handbook"
    assert built["dataset_id"] == dataset_code("handbook")
    assert built["partition"] == ["edition=2025"]


def test_the_raw_path_is_stored_relative_to_the_lake_root():
    """So the catalogue survives the lake moving between a mount and a bucket."""
    built = row(landed(), raw_root="/lake/raw", pipeline_version="1")
    assert built["raw_path"] == "statistics/esdm-heesi/handbook/edition=2025/doc_abc/file.pdf"


def test_a_document_nothing_was_read_from_counts_zero_rather_than_null():
    """A zero is a real answer, and a null would make the column unfilterable."""
    built = row(landed(), raw_root="/lake/raw", pipeline_version="1")
    assert built["indicator_count"] == 0
    assert built["observation_count"] == 0


def test_a_pdfs_page_count_is_read_from_the_file(tmp_path):
    """Extraction never sees HEESI's PDF — the handbook's own table parser
    claims it — so the count comes from the file or the catalogue shows a dash
    beside a viewer displaying 172 pages."""
    pypdf = pytest.importorskip("pypdf")

    pdf = tmp_path / "three-pages.pdf"
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as fh:
        writer.write(fh)

    built = row(
        landed(path=pdf, media_type="application/pdf", extra={}),
        raw_root=str(tmp_path),
        pipeline_version="1",
    )
    assert built["page_count"] == 3


def test_an_unreadable_pdf_is_catalogued_without_a_page_count(tmp_path):
    """A missing count is a small gap; refusing to catalogue the document over
    it would be a large one."""
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-1.4 and then nothing that parses")

    built = row(
        landed(path=pdf, media_type="application/pdf", extra={}),
        raw_root=str(tmp_path),
        pipeline_version="1",
    )
    assert built["page_count"] is None
    assert built["document_id"] == "doc_abc"


def test_a_non_pdf_is_not_opened_for_a_page_count():
    """The path does not exist, so opening it would raise rather than warn."""
    built = row(
        landed(media_type="text/csv", path=Path("/nowhere/at/all.csv"), extra={}),
        raw_root="/nowhere",
        pipeline_version="1",
    )
    assert built["page_count"] is None


def test_an_extractors_findings_reach_the_row():
    built = row(
        landed(extra={}),
        raw_root="/lake/raw",
        pipeline_version="1",
        extracted={"title": "Read from the file", "language": "id", "page_count": 412},
    )
    assert built["title"] == "Read from the file"
    assert built["language"] == "id"
    assert built["page_count"] == 412
