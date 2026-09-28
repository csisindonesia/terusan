"""Tags: what a record is found by, derived from what it already says."""

from __future__ import annotations

import pytest

from terusan_pipelines import datasets as dataset_registry
from terusan_pipelines.tagging import (
    MAX_TAGS,
    MIN_TAGS,
    SourceFacts,
    dataset_tags,
    indicator_tags,
    normalize_tag,
    retopic,
    source_tags,
    topics_of,
)

BPS = SourceFacts(
    source_id="bi-consumer-survey",
    name="Bank Indonesia — Survei Konsumen",
    organization="Bank Indonesia",
    category="statistics",
    source_type="official_portal",
    collection_method="bulk_download",
    country="ID",
    update_frequency="monthly",
)


def test_every_record_reaches_the_minimum() -> None:
    """The minimum is the point: a filter cannot rely on one that sometimes holds."""
    bare = indicator_tags(slug=None, name=None)
    assert len(bare) >= MIN_TAGS

    assert len(source_tags(SourceFacts(source_id="x"))) >= MIN_TAGS
    assert len(dataset_tags(slug="x")) >= MIN_TAGS


def test_tags_are_capped_and_ordered_by_what_identifies() -> None:
    tags = indicator_tags(
        slug="consumer_confidence_index",
        name="Consumer confidence index for Indonesia",
        unit="Index 2015=100",
        frequency="MONTHLY",
        dataset_slug="consumer-survey",
        source=BPS,
    )
    assert len(tags) <= MAX_TAGS
    assert tags[0] == "bi-consumer-survey"
    assert "consumer-survey" in tags
    assert "monthly" in tags
    assert "sentiment" in tags


def test_tags_are_deterministic() -> None:
    """Rebuilt with the record, so an unchanged record must not look changed."""
    once = indicator_tags(slug="gdp_current_usd", name="GDP, current US$", source=BPS)
    twice = indicator_tags(slug="gdp_current_usd", name="GDP, current US$", source=BPS)
    assert once == twice


def test_a_unit_is_tagged_by_its_first_clause() -> None:
    """FRED states units as a sentence; the chip is the unit, not the caveat."""
    tags = indicator_tags(
        slug="x", name="Something", unit="Millions of US Dollars, Seasonally Adjusted"
    )
    assert "millions-of-us-dollars" in tags
    assert not any("seasonally" in tag for tag in tags)


@pytest.mark.parametrize(
    ("text", "expected", "unexpected"),
    [
        ("Brent crude oil price", "oil", "agriculture"),
        ("Palm oil price", "palm-oil", None),
        ("Military expenditure, share of GDP", "defence", None),
        ("Realisasi belanja APBD", "fiscal", None),
        ("Rice production", "agriculture", None),
    ],
)
def test_topics_are_read_off_the_title(text: str, expected: str, unexpected: str | None) -> None:
    topics = topics_of(text)
    assert expected in topics
    if unexpected:
        assert unexpected not in topics


def test_a_word_inside_another_word_is_not_a_topic() -> None:
    """`price` contains `rice`, and a price series is not agriculture."""
    assert "agriculture" not in topics_of("Copper price close")
    # …and `continuing` contains `tin`.
    assert "metals" not in topics_of("Continuing claims")


def test_a_dataset_takes_only_what_its_series_agree_on() -> None:
    """A tag one series carries describes that series, not the collection."""
    tags = dataset_tags(
        slug="consumer-survey",
        title="Consumer survey",
        source=BPS,
        indicator_tags_seen=[
            ["sentiment", "surveys", "jakarta"],
            ["sentiment", "surveys", "bandung"],
        ],
    )
    assert "sentiment" in tags
    assert "jakarta" not in tags
    # Structural tags say what kind of record carries them, and do not travel.
    assert "indicator" not in tags


def test_normalize_tag_rejects_what_is_not_a_tag() -> None:
    assert normalize_tag("Bank Indonesia") == "bank-indonesia"
    assert normalize_tag("  ") is None
    assert normalize_tag(None) is None
    assert normalize_tag("unknown") is None


def test_every_declared_dataset_is_tagged_and_coded() -> None:
    """The registry is hand-written, so the invariant is worth asserting."""
    codes = {meta.dataset_id for meta in dataset_registry.DATASETS}
    assert len(codes) == len(dataset_registry.DATASETS)
    for meta in dataset_registry.DATASETS:
        tags = dataset_tags(
            slug=meta.slug,
            title=meta.title,
            description=meta.description,
            declared=meta.tags,
            source=SourceFacts(source_id=meta.source),
        )
        assert len(tags) >= MIN_TAGS, meta.slug


def test_an_undeclared_dataset_is_still_described() -> None:
    """A new scraper's collection belongs in the catalogue before anyone names it."""
    meta = dataset_registry.describe("brand-new-thing", source="some-source")
    assert meta.title == "Brand new thing"
    assert meta.source == "some-source"
    assert dataset_registry.get("brand-new-thing") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # BPS titles its tables in Indonesian; each of these was tagged with
        # nothing but its unit.
        ("Penyediaan dan Penggunaan Fisik untuk Emisi GRK Indonesia — Total", "emissions"),
        ("Penggunaan Fisik untuk Energi Indonesia", "energy"),
        (
            "Produksi Kayu Bulat Perusahaan Hak Pengusahaan Hutan (HPH) Menurut Jenis Kayu",
            "forestry",
        ),
        ("Capaian Luas Perhutanan Sosial per Skema (1.000 Ha)", "forestry"),
        ("Tree cover and tree cover loss by province (Hansen)", "forestry"),
        ("Luas Kebakaran Hutan dan Lahan (Ha)", "fire"),
        ("Indeks Pembangunan Manusia (IPM) menurut Jenis Kelamin", "human-development"),
        ("Umur Harapan Hidup saat lahir menurut Jenis Kelamin", "human-development"),
        ("Prevalensi Balita Stunting", "health"),
        ("Persentase Rumah Tangga dengan Akses Air Minum Layak", "water"),
        ("Produksi Perikanan Tangkap menurut Provinsi", "fisheries"),
        ("Jumlah Penumpang Angkutan Udara", "transport"),
        ("Persentase Penduduk yang Mengakses Internet", "digital"),
        ("Jumlah Kejadian Bencana Alam menurut Jenis Bencana", "disasters"),
        ("Produksi Barang Tambang Mineral", "mining"),
        (
            "NTPR (Nilai Tukar Petani Tanaman Perkebunan) Menurut Subsektor",
            "farmers-terms-of-trade",
        ),
    ],
)
def test_indonesian_titles_reach_their_topic(text: str, expected: str) -> None:
    assert expected in topics_of(text)


@pytest.mark.parametrize(
    ("text", "unexpected"),
    [
        # A farmer's terms of trade is not a currency.
        ("NTPR (Nilai Tukar Petani Tanaman Perkebunan) Menurut Subsektor", "exchange-rate"),
        ("Indeks Nilai Tukar Nelayan", "exchange-rate"),
        # "hutan" sits inside "hutang", and debt is not forestry.
        ("Posisi Hutang Luar Negeri Pemerintah", "forestry"),
        # "low" sits inside "flow", and a cash flow is not a price.
        ("Net cash flow of the central government", "prices"),
        ("Air teh kemasan, minuman bersoda/mengandung CO2", "emissions"),
        ("Deaths in interstate conflict, high estimate", "prices"),
        ("Each pixel flagged as fire at low, nominal or high confidence", "prices"),
        ("Land cover: open shrubland (MODIS IGBP)", "prices"),
        ("Kemisikinan rumah tangga", "emissions"),
        # "kurs" sits inside "kursus".
        ("Jumlah Peserta Kursus Keterampilan", "exchange-rate"),
    ],
)
def test_a_word_inside_another_is_not_its_topic(text: str, unexpected: str) -> None:
    assert unexpected not in topics_of(text)


def test_a_quote_is_still_a_price() -> None:
    for text in ("US dollar / rupiah exchange rate, high", "IHSG close", "Gold price open"):
        assert "prices" in topics_of(text), text


def test_the_exchange_rate_is_still_an_exchange_rate() -> None:
    assert "exchange-rate" in topics_of("Kurs Tengah Rupiah terhadap Dolar AS")
    assert "exchange-rate" in topics_of("Nilai Tukar Rupiah terhadap USD")


def test_retopic_replaces_the_topics_and_keeps_the_facets() -> None:
    """A published series is re-read with today's rules, and nothing else moves."""
    held = [
        "bps-indicators",
        "statistics",
        "badan-pusat-statistik",
        "monthly",
        "exchange-rate",
        "monetary",
        "labour",
        "employment",
        "indicator",
    ]
    tags = retopic(held, "NTPR (Nilai Tukar Petani Tanaman Perkebunan) — f) Upah Buruh Tani")
    assert tags[:4] == ["bps-indicators", "statistics", "badan-pusat-statistik", "monthly"]
    assert "exchange-rate" not in tags and "monetary" not in tags
    assert {"farmers-terms-of-trade", "labour"} <= set(tags)
    assert tags[-1] == "indicator"
    assert (
        retopic(tags, "NTPR (Nilai Tukar Petani Tanaman Perkebunan) — f) Upah Buruh Tani") == tags
    )
