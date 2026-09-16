"""The agency scrapers vendored from an earlier collection.

These check the wiring — that each source is registered, declares a coherent
registry record, and reaches its vendored module — not the scraping itself,
which needs the agency's server. The vendored parsing is covered by the fixtures
it will get when an extractor reads the landed bytes.
"""

from __future__ import annotations

import pytest

from terusan_pipelines.sources import Registry, Source, SourceMeta

AGENCY_SLUGS = [
    "bi-consumer-survey",
    "bi-retail-sales-survey",
    "bi-pihps-food-prices",
    "djpk-apbd",
    "ojk-banking-spi",
    "ojk-fintech-p2p",
    "kemendag-sp2kp-prices",
]


@pytest.fixture(scope="module")
def registry() -> Registry:
    found = Registry()
    found.discover()
    return found


@pytest.mark.parametrize("slug", AGENCY_SLUGS)
def test_every_vendored_source_is_registered(registry, slug):
    assert registry.get(slug) is not None


@pytest.mark.parametrize("slug", AGENCY_SLUGS)
def test_each_declares_a_usable_registry_record(registry, slug):
    meta = registry.get(slug).meta
    assert isinstance(meta, SourceMeta)
    assert meta.name and meta.organization
    # Every one of these publishes on a cycle, so a missing schedule means the
    # source would only ever run when someone remembered.
    assert meta.schedule, f"{slug} has no schedule"
    assert meta.base_url, f"{slug} has no base_url"
    # A government server answering a few hundred requests deserves a slower
    # ceiling than the default.
    assert meta.max_requests_per_second <= 2.0


@pytest.mark.parametrize("slug", AGENCY_SLUGS)
def test_each_is_a_source_and_not_abstract(registry, slug):
    source = registry.get(slug)
    assert issubclass(source, Source)
    assert not source.abstract
    # Instantiable without arguments, which is what the runner does.
    assert source() is not None


def test_slugs_are_unique_across_every_source(registry):
    """Slugs key the sources table and the RAW path."""
    slugs = [source.meta.slug for source in registry.all()]
    assert len(set(slugs)) == len(slugs)


def test_vendored_modules_carry_their_provenance():
    """Each keeps the docstring saying where it came from and what it works
    around — the part that took someone a while to find."""
    from terusan_pipelines.sources.bank_indonesia.legacy import consumer_survey
    from terusan_pipelines.sources.kemenkeu.legacy import djpk_apbd
    from terusan_pipelines.sources.ojk.legacy import banking_spi

    for module in (consumer_survey, djpk_apbd, banking_spi):
        assert module.__doc__, f"{module.__name__} lost its docstring"
        assert "http" in module.__doc__, f"{module.__name__} no longer names its source"


def test_vendored_modules_do_not_run_on_import():
    """They arrived as scripts; a module that scrapes when imported would make
    `terusan sources list` hit four agencies."""
    import pathlib

    import terusan_pipelines.sources as sources

    root = pathlib.Path(sources.__file__).parent
    for path in root.glob("*/legacy/*.py"):
        assert '__name__ == "__main__"' not in path.read_text(), path.name


def test_vendored_modules_write_nowhere():
    """Landing is the platform's job. An output path in here would put files
    outside the lake, where nothing can find or trace them."""
    import pathlib

    import terusan_pipelines.sources as sources

    root = pathlib.Path(sources.__file__).parent
    for path in root.glob("*/legacy/*.py"):
        text = path.read_text()
        assert "/Users/" not in text, f"{path.name} still names someone's home directory"


def test_the_browser_source_says_so_rather_than_failing_obscurely():
    from terusan_pipelines.sources.kemendag import FoodPriceMonitoring

    source = FoodPriceMonitoring()
    if source.available():
        pytest.skip("playwright is installed")

    with pytest.raises(RuntimeError, match="playwright"):
        list(
            source.collect(
                __import__("terusan_pipelines.sources", fromlist=["ScrapeContext"]).ScrapeContext()
            )
        )
