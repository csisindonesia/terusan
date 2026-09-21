"""SEKI: reading the declared mapping, and reading a table with it.

The workbooks are legacy BIFF and no library writes them, so the sheet is a
stub carrying the cells that matter — which is also the honest shape of these
tests: what is being checked is the header logic, and a header row is a list
of labels however it was stored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from terusan_pipelines.extract import Landed, SekiExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.seki import (
    SpecError,
    VariableSpec,
    column_periods,
    load_specs,
    row_of,
)

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "seki" / "variables.csv"


class Sheet:
    """Enough of an xlrd sheet for the header logic: a grid of cells."""

    def __init__(self, rows: list[list[Any]]) -> None:
        width = max((len(row) for row in rows), default=0)
        self._rows = [list(row) + [""] * (width - len(row)) for row in rows]
        self.nrows = len(self._rows)
        self.ncols = width

    def cell_value(self, row: int, column: int) -> Any:
        return self._rows[row][column]


class Workbook:
    def __init__(self, sheets: dict[str, Sheet]) -> None:
        self._sheets = sheets
        self.released = False

    def sheet_by_name(self, name: str) -> Sheet:
        try:
            return self._sheets[name]
        except KeyError:
            raise RuntimeError(f"no sheet named {name}") from None

    def release_resources(self) -> None:
        self.released = True


# -- the declared mapping ---------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("D31:T31", 30), ("E41:CC41", 40), ("e41:cc41", 40), (" D5:T5 ", 4)],
)
def test_a_range_names_the_row_it_sits_on(cell: str, expected: int) -> None:
    assert row_of(cell) == expected


def test_a_range_spanning_rows_is_not_a_series() -> None:
    """It describes a block, and there is no single figure to take from it."""
    with pytest.raises(SpecError, match="more than one row"):
        row_of("D31:T33")


def test_something_that_is_not_a_range_is_refused() -> None:
    with pytest.raises(SpecError, match="not a cell range"):
        row_of("row 31")


def test_the_committed_mapping_loads() -> None:
    """The reference file is the authority: if it stops parsing, no SEKI table
    can be read at all, so it is checked here rather than discovered at 3am."""
    specs = load_specs(REFERENCE)

    assert len(specs) > 200
    assert {spec.time_freq for spec in specs} == {"Monthly", "Quarterly", "Yearly"}
    assert all(spec.table.startswith("TABEL") for spec in specs)


def test_every_series_in_the_mapping_has_its_own_identifier() -> None:
    """Two series under one identifier overwrite each other in Silver, and the
    catalogue this came from did exactly that for two GDP lines."""
    specs = load_specs(REFERENCE)
    identifiers = [spec.indicator_id for spec in specs]

    assert len(set(identifiers)) == len(identifiers)


def test_an_identifier_is_the_slug_the_publisher_uses() -> None:
    spec = VariableSpec(
        slug="CPI_General",
        description="Composite CPI",
        unit="Index",
        time_freq="Monthly",
        table="TABEL8_1",
        sheet="8.1",
        row_index=40,
        period_row_index=5,
    )

    assert spec.indicator_id == "seki_cpi_general"


def test_a_malformed_row_does_not_cost_the_others(tmp_path: Path) -> None:
    path = tmp_path / "variables.csv"
    path.write_text(
        "# a comment the reader skips\n"
        "no,slug,description,unit,time_freq,update_freq,table,sheet,cell,month_cell,"
        "note,technical_notes\n"
        "1,good,A series,Index,Monthly,Monthly,TABEL8_1,8.1,E41:CC41,E6:CC6,,\n"
        "2,bad,Broken,Index,Monthly,Monthly,TABEL8_1,8.1,not-a-range,E6:CC6,,\n"
    )

    specs = load_specs(path)

    assert [spec.slug for spec in specs] == ["good"]


def test_a_mapping_with_no_usable_row_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "variables.csv"
    path.write_text("no,slug,cell,month_cell\n1,bad,nope,nope\n")

    with pytest.raises(ExtractionError, match="no usable rows"):
        load_specs(path)


# -- header rows to periods -------------------------------------------------


def test_a_monthly_table_reads_the_year_off_the_block_above() -> None:
    """SEKI writes the year once per twelve columns, not once per column."""
    sheet = Sheet(
        [
            ["", 2025, "", "", 2026, "", ""],
            ["", "Jan", "Feb", "Mar", "Jan", "Feb", "Mar"],
        ]
    )

    periods = column_periods(sheet, 1, "Monthly")

    assert [periods[column][0] for column in sorted(periods)] == [
        "2025-01",
        "2025-02",
        "2025-03",
        "2026-01",
        "2026-02",
        "2026-03",
    ]


def test_a_block_with_no_year_follows_the_one_before_it() -> None:
    """Which is how SEKI writes a year it has not got round to labelling."""
    sheet = Sheet(
        [
            ["", 2025, "", "", "", ""],
            ["", "Jan", "Feb", "Jan", "Feb", ""],
        ]
    )

    periods = column_periods(sheet, 1, "Monthly")

    assert [periods[column][0] for column in sorted(periods)] == [
        "2025-01",
        "2025-02",
        "2026-01",
        "2026-02",
    ]


def test_a_block_before_the_first_anchor_counts_backwards() -> None:
    sheet = Sheet(
        [
            ["", "", "", 2026, ""],
            ["", "Jan", "Feb", "Jan", "Feb"],
        ]
    )

    periods = column_periods(sheet, 1, "Monthly")

    assert [periods[column][0] for column in sorted(periods)] == [
        "2025-01",
        "2025-02",
        "2026-01",
        "2026-02",
    ]


def test_a_quarterly_table_reads_quarters() -> None:
    sheet = Sheet([[2010, "", "", ""], ["Q1", "Q2", "Q3", "Q4*"]])

    periods = column_periods(sheet, 1, "Quarterly")

    assert [periods[column] for column in sorted(periods)] == [
        ("2010-Q1", "Q1"),
        ("2010-Q2", "Q2"),
        ("2010-Q3", "Q3"),
        # The provisional marker is kept as written, so a provisional figure
        # can still be told from a final one after the period is normalized.
        ("2010-Q4", "Q4*"),
    ]


def test_a_yearly_table_takes_the_year_off_the_header_row_itself() -> None:
    sheet = Sheet([[2008, 2009, 2010], ["", "", ""]])

    periods = column_periods(sheet, 0, "Yearly")

    assert [periods[column][0] for column in sorted(periods)] == ["2008", "2009", "2010"]


def test_indonesian_month_names_are_read() -> None:
    """The workbooks are bilingual and switch between editions: `Mei`, `Ags`,
    `Okt`, `Des` are the same months as May, Aug, Oct, Dec."""
    sheet = Sheet([[2025, "", "", "", ""], ["Jan", "Mei", "Ags", "Okt", "Des"]])

    periods = column_periods(sheet, 1, "Monthly")

    assert [periods[column][0] for column in sorted(periods)] == [
        "2025-01",
        "2025-05",
        "2025-08",
        "2025-10",
        "2025-12",
    ]


# -- the extractor ----------------------------------------------------------

MAPPING = (
    "no,slug,description,unit,time_freq,update_freq,table,sheet,cell,month_cell,"
    "note,technical_notes\n"
    "1,CPI_General,Composite CPI,Index,Monthly,Monthly,TABEL8_1,8.1,A3:C3,A2:C2,,BPS\n"
)


@pytest.fixture
def mapping(tmp_path: Path) -> Path:
    path = tmp_path / "variables.csv"
    path.write_text(MAPPING)
    return path


@pytest.fixture
def table() -> Workbook:
    return Workbook(
        {
            "8.1": Sheet(
                [
                    [2025, "", ""],
                    ["Jan", "Feb", "Mar"],
                    [104.33, "-", 105.2],
                ]
            )
        }
    )


def landed(tmp_path: Path, table_id: str = "TABEL8_1") -> Landed:
    path = tmp_path / f"{table_id}.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 not really BIFF")
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="bi-seki",
        dataset="seki-tables",
        extra={"table_id": table_id, "title": "Indeks Harga Konsumen"},
    )


def rows_of(
    extractor: SekiExtractor, workbook: Workbook, landed_table: Landed, monkeypatch: Any
) -> list[dict]:
    monkeypatch.setattr(SekiExtractor, "_open", staticmethod(lambda _: workbook))
    return list(extractor.extract(landed_table))


def test_a_mapped_row_becomes_one_record_per_period(
    mapping: Path, table: Workbook, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = rows_of(SekiExtractor(mapping), table, landed(tmp_path), monkeypatch)

    assert [row["columns"]["period"] for row in rows] == ["2025-01", "2025-03"]
    assert [row["columns"]["value"] for row in rows] == ["104.33", "105.2"]
    assert [row["row_number"] for row in rows] == [1, 2]


def test_what_the_mapping_says_rides_onto_every_figure(
    mapping: Path, table: Workbook, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SEKI cell states no unit, no frequency and no series name. Without
    the mapping beside it, a figure in Bronze cannot be normalized at all."""
    rows = rows_of(SekiExtractor(mapping), table, landed(tmp_path), monkeypatch)
    columns = rows[0]["columns"]

    assert columns["indicator"] == "seki_cpi_general"
    assert columns["series_name"] == "Composite CPI"
    assert columns["unit"] == "Index"
    assert columns["frequency"] == "Monthly"
    assert columns["table"] == "TABEL8_1"
    assert columns["sheet"] == "8.1"
    assert columns["country"] == "Indonesia"
    assert columns["table_title"] == "Indeks Harga Konsumen"


def test_a_dash_is_not_a_figure(
    mapping: Path, table: Workbook, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """February is a dash — SEKI's way of saying it has not published yet.
    Carried through, every series would hold a decade of empty observations."""
    rows = rows_of(SekiExtractor(mapping), table, landed(tmp_path), monkeypatch)

    assert "2025-02" not in [row["columns"]["period"] for row in rows]


def test_a_renumbered_sheet_is_skipped_rather_than_misread(
    mapping: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the wrong sheet would file one series' figures under another."""
    workbook = Workbook({"Th 2010-2019": Sheet([[2025], ["Jan"], [104.33]])})

    rows = rows_of(SekiExtractor(mapping), workbook, landed(tmp_path), monkeypatch)

    assert rows == []


def test_a_row_past_the_end_of_the_sheet_is_skipped(
    mapping: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release that drops rows above the series shifts it, and reading past
    the sheet would be an exception in the middle of a hundred tables."""
    workbook = Workbook({"8.1": Sheet([[2025], ["Jan"]])})

    rows = rows_of(SekiExtractor(mapping), workbook, landed(tmp_path), monkeypatch)

    assert rows == []


def test_a_table_nobody_has_mapped_yields_nothing(
    mapping: Path, table: Workbook, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most of SEKI's hundred-odd tables carry no mapped series yet. They are
    still landed, and a mapping added later replays over the same bytes."""
    rows = rows_of(SekiExtractor(mapping), table, landed(tmp_path, "TABEL2_7"), monkeypatch)

    assert rows == []


def test_the_index_page_is_left_to_the_html_reader(tmp_path: Path) -> None:
    page = Landed(
        path=tmp_path / "index.html",
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="bi-seki",
        dataset="index",
    )

    assert not SekiExtractor().handles(page)
    assert SekiExtractor().handles(landed(tmp_path))


def test_only_this_source_is_claimed(tmp_path: Path) -> None:
    other = Landed(
        path=tmp_path / "TABEL8_1.xls",
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="ojk-banking-spi",
        dataset="seki-tables",
    )

    assert not SekiExtractor().handles(other)


def test_an_unreadable_workbook_fails_with_its_path(
    mapping: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_: Landed) -> Workbook:
        raise ExtractionError(str(tmp_path / "TABEL8_1.xls"), "unreadable workbook: not BIFF")

    monkeypatch.setattr(SekiExtractor, "_open", staticmethod(explode))

    with pytest.raises(ExtractionError, match="unreadable workbook"):
        list(SekiExtractor(mapping).extract(landed(tmp_path)))
