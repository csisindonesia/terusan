"""Short identifiers: stable between runs, distinct between publishers."""

from __future__ import annotations

from terusan_pipelines.identifiers import DEFAULT_LENGTH, short_id


def test_an_identifier_is_short_lowercase_and_alphanumeric() -> None:
    """It is a path segment, a URL parameter and a filename before anything."""
    identifier = short_id("fred", "NASDAQNQID55LMN")

    assert len(identifier) == DEFAULT_LENGTH
    assert identifier.isalnum()
    assert identifier == identifier.lower()


def test_the_same_key_gives_the_same_identifier() -> None:
    """Normalization rebuilds an indicator from scratch every run.

    An identifier drawn at random would orphan the previous run's partition and
    break every link anyone had saved.
    """
    assert short_id("fred", "WUIIDN") == short_id("fred", "WUIIDN")


def test_two_publishers_using_one_code_do_not_collide() -> None:
    """Which they do: a two-letter series code means nothing on its own."""
    assert short_id("fred", "GDP") != short_id("bps", "GDP")


def test_neighbouring_keys_do_not_produce_neighbouring_identifiers() -> None:
    """FRED's ids differ by one character across a hundred series."""
    assert short_id("fred", "NASDAQNQID55LM") != short_id("fred", "NASDAQNQID55LMN")


def test_a_thousand_keys_stay_distinct() -> None:
    """Eight base36 characters is 2.8 × 10¹² identifiers; a crawl is hundreds."""
    identifiers = {short_id("fred", f"SERIES{n:04d}") for n in range(1000)}
    assert len(identifiers) == 1000


def test_the_length_is_adjustable() -> None:
    assert len(short_id("fred", "WUIIDN", length=12)) == 12
