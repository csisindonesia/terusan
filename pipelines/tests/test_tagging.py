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
