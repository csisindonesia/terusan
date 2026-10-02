"""KPK's enforcement statistics page: the tables read out one figure per row."""

from __future__ import annotations

from pathlib import Path

import pytest

from terusan_pipelines.extract import ExtractionError, KpkPenindakanExtractor, Landed
from terusan_pipelines.extract.kpk_penindakan import FIGURES_DATASET, as_of

PAGE = """
<html><body>
<p><strong>Updated : Per-Maret 2026 | TW I</strong></p>
<table><tbody>
<tr><td></td><td>2024</td><td>2025</td><td>2026</td><td>Total</td></tr>
<tr><td>Penyelidikan</td><td>73</td><td>70</td><td>35</td><td>178</td></tr>
<tr><td>Penyidikan</td><td>154</td><td>116</td><td>45</td><td>315</td></tr>
<tr><td>Penuntutan</td><td>90</td><td>115</td><td>29</td><td>234</td></tr>
<tr><td>Inkracht</td><td>91</td><td>87</td><td>20</td><td>198</td></tr>
<tr><td>Eksekusi</td><td>108</td><td>78</td><td>18</td><td>204</td></tr>
</tbody></table>
<p>TPK Instansi</p>
<table><tbody>
<tr><td><strong>NO</strong></td><td><strong>INSTANSI</strong></td>
    <td>2024</td><td>2025</td><td>2026</td><td>JUMLAH</td></tr>
<tr><td>1</td><td>DPR RI</td><td>10</td><td>5</td><td>&nbsp;</td><td>15</td></tr>
<tr><td>2</td><td>Kementerian/Lembaga</td><td>40</td><td>31</td><td>22</td><td>93</td></tr>
<tr><td></td><td><strong>JUMLAH</strong></td><td>50</td><td>36</td><td>22</td><td>108</td></tr>
</tbody></table>
<table><tbody>
<tr><td>Inkracht</td><td>2019</td><td>2020</td><td>2025</td><td>Jumlah</td></tr>
<tr><td>Mahkamah Agung</td><td>18</td><td>14</td><td>16</td><td>48</td></tr>
<tr><td>Jumlah</td><td>18</td><td>14</td><td>16</td><td>48</td></tr>
</tbody></table>
<table><tbody><tr><td>Menu</td><td>Link</td></tr></tbody></table>
</body></html>
"""


def _landed(tmp_path: Path, page: str = PAGE) -> Landed:
    # Landed under the URL's last segment, which has no suffix.
    path = tmp_path / "penindakan-2"
    path.write_text(page, encoding="utf-8")
    return Landed(
        path=path,
        document_id="doc_kpk",
        content_hash="0" * 64,
        source_slug="kpk-penindakan",
        dataset="kpk-penindakan",
        original_filename=path.name,
    )


def _figures(tmp_path: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    extractor = KpkPenindakanExtractor()
    landed = _landed(tmp_path)
    assert extractor.handles(landed)
    rows = list(extractor.extract(landed))
    assert {row["dataset"] for row in rows} == {FIGURES_DATASET}
    return {
        (row["columns"]["indicator"], row["columns"]["category"], row["columns"]["period"]): row[
            "columns"
        ]
        for row in rows
    }


def test_each_table_is_known_by_its_label_or_its_rows(tmp_path: Path) -> None:
    got = _figures(tmp_path)

    assert got[("kpk_cases_by_stage", "Eksekusi", "2024")]["value"] == "108"
    assert got[("kpk_cases_by_agency", "Kementerian/Lembaga", "2026")]["value"] == "22"
    assert got[("kpk_final_verdicts_by_court", "Mahkamah Agung", "2025")]["value"] == "16"
    assert {indicator for indicator, _, _ in got} == {
        "kpk_cases_by_stage",
        "kpk_cases_by_agency",
        "kpk_final_verdicts_by_court",
    }


def test_the_totals_are_not_read(tmp_path: Path) -> None:
    got = _figures(tmp_path)

    assert not {category for _, category, _ in got} & {"JUMLAH", "Jumlah"}
    assert not {period for _, _, period in got} - {"2019", "2020", "2024", "2025", "2026"}


def test_a_table_reads_only_the_years_it_holds(tmp_path: Path) -> None:
    got = _figures(tmp_path)

    court = {period for indicator, _, period in got if indicator == "kpk_final_verdicts_by_court"}
    assert court == {"2019", "2020", "2025"}


def test_a_blank_cell_stays_blank_rather_than_zero(tmp_path: Path) -> None:
    got = _figures(tmp_path)

    assert got[("kpk_cases_by_agency", "DPR RI", "2026")]["value"] == ""


def test_every_figure_carries_how_far_the_page_runs(tmp_path: Path) -> None:
    got = _figures(tmp_path)

    assert {figure["release"] for figure in got.values()} == {"Per-Maret 2026"}
    assert {figure["geo"] for figure in got.values()} == {"Indonesia"}
    assert as_of("no date here") == ""


def test_a_page_without_the_tables_is_an_error(tmp_path: Path) -> None:
    landed = _landed(tmp_path, "<html><body><p>Maintenance</p></body></html>")
    with pytest.raises(ExtractionError):
        list(KpkPenindakanExtractor().extract(landed))
