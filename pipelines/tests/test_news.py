"""News monitoring: the parts that decide what a figure means.

Everything tested here is a pure function over text or rows. The crawl, the
classifier and the warehouse are not — they need a network, an account and a
lake — and what they do is checked by running them. What is checked here is the
reasoning those three hang off: which article is about something, where it
happened, how many were hurt, and which reports are one incident.
"""

from __future__ import annotations

from datetime import date

import pytest

from terusan_pipelines.news import deep, dictionary, figures, places
from terusan_pipelines.news.cluster import (
    _event_id,
    _merge,
    cluster,
    same_incident,
)
from terusan_pipelines.news.codes import canonical
from terusan_pipelines.news.lexicon import Lexicon
from terusan_pipelines.news.outlets import load_outlets
from terusan_pipelines.news.profiles import VIOLENCE as VIOLENCE_PROFILE
from terusan_pipelines.news.profiles.base import Coding
from terusan_pipelines.news.profiles.violence import ambiguous
from terusan_pipelines.sources.news import search

# -- the outlet list -------------------------------------------------------


def test_outlet_list_is_readable_and_whole() -> None:
    outlets = load_outlets()
    assert len(outlets) >= 60
    # A duplicated host would crawl one paper twice and attribute one paper's
    # reporting to another's province.
    hosts = [outlet.host for outlet in outlets]
    assert len(hosts) == len(set(hosts))
    # Every outlet resolves to a place, or its figures cannot be counted.
    assert all(outlet.geo_id for outlet in outlets)


def test_retired_outlets_are_kept_and_explained() -> None:
    retired = [outlet for outlet in load_outlets() if not outlet.active]
    assert retired, "the list records at least one title that has gone"
    # Without the note a retired row looks like an outlet that simply published
    # nothing, which is a different fact about a province.
    assert all(outlet.note for outlet in retired)


# -- what counts as an article ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.co.id/2026/09/21/bentrok-warga-di-pasar-baru",
        "https://example.co.id/read/bentrok-antar-warga-pecah-di-pasar",
    ],
)
def test_article_urls_are_recognised(url: str) -> None:
    assert search.is_article(url, "example.co.id")


@pytest.mark.parametrize(
    "url",
    [
        # Navigation, which is most of what a search page links to.
        "https://example.co.id/kategori/hukum",
        "https://example.co.id/tag/bentrok",
        "https://example.co.id/penulis/budi",
        # Another title in the same publisher network. Following it would file
        # one paper's reporting under another paper's province.
        "https://other.co.id/2026/09/21/bentrok-warga-di-pasar-baru",
    ],
)
def test_non_articles_are_rejected(url: str) -> None:
    assert not search.is_article(url, "example.co.id")


# -- the gate --------------------------------------------------------------


def test_lexicon_matches_across_indonesian_affixes() -> None:
    lexicon = Lexicon(mined={}, seed=("keroyok massa", "bentrokan"))
    # `dikeroyok` should not have to be listed separately from `keroyok`.
    assert "bentrokan" in lexicon.matches("Terjadi bentrokan di pasar")
    assert lexicon.matches("Pelaku dikeroyok massa hingga babak belur")


def test_lexicon_ignores_an_unrelated_article() -> None:
    lexicon = Lexicon(mined={}, seed=("bentrokan", "tawuran"))
    assert lexicon.matches("Harga cabai naik menjelang akhir pekan") == ()


# -- the keyword dictionary ------------------------------------------------


def _dictionary() -> dictionary.Dictionary:
    return dictionary.violence_dictionary()


def test_the_dictionary_file_covers_every_weighted_category() -> None:
    # A category in the rule with no terms behind it is a rule that never
    # fires, and it fails silently: the crawl looks like it is working.
    loaded = _dictionary()
    assert set(loaded.categories) == set(dictionary.WEIGHTS)
    assert all(loaded.categories[name] for name in loaded.categories)


def test_an_act_by_a_crowd_is_a_candidate() -> None:
    found = _dictionary().match("Bentrokan antarwarga pecah di pasar. Massa saling lempar batu.")
    assert found.candidate
    assert found.verdict == "act+collective"
    assert "violence_action" in found.categories
    assert "collective_actor" in found.categories


def test_an_act_with_no_crowd_but_weapons_and_casualties_is_a_candidate() -> None:
    # The route that keeps an incident reported without the word for a crowd:
    # what it left behind says one was there.
    found = _dictionary().match(
        "Penyerangan terjadi Sabtu malam. Pelaku membawa celurit; satu orang "
        "tewas dan dua rumah dibakar."
    )
    assert found.candidate
    assert found.verdict == "act+evidence"


def test_a_crowd_without_an_act_is_not_a_candidate() -> None:
    # The whole reason for grouping the dictionary: `massa` on its own is a
    # concert, a prayer meeting and a football match.
    found = _dictionary().match("Ribuan massa menghadiri kampanye akbar di alun-alun.")
    assert not found.candidate
    assert found.verdict in ("no-act", "no-terms")


def test_a_death_alone_is_not_collective_violence() -> None:
    # A traffic report carries `tewas` and nothing else that matters.
    found = _dictionary().match(
        "Seorang pengendara tewas setelah motornya menabrak pembatas jalan."
    )
    assert not found.candidate


def test_a_price_report_matches_nothing() -> None:
    assert not _dictionary().match("Harga cabai naik menjelang akhir pekan").candidate


def test_dictionary_matches_across_indonesian_affixes() -> None:
    # The file says `pengeroyokan`; the paper writes `dikeroyok`.
    found = _dictionary().match("Pelaku dikeroyok massa hingga babak belur")
    assert found.candidate
    assert "violence_action" in found.categories


def test_a_word_is_not_matched_inside_a_longer_one() -> None:
    # `batu` is a weapon in the dictionary and `batubara` is an export figure.
    found = _dictionary().match("Produksi batubara naik tahun ini")
    assert found.terms == ()


# -- when a coding is worth a second reader --------------------------------


def _coding(**overrides: object) -> Coding:
    row = {
        "violence_form1": "SERANGAN TANPA SENJATA API",
        "weapon_type1": "SENJATA JARAK DEKAT",
        "issue_type1": "KONFLIK LAHAN",
        "actor1a": "WARGA",
        "actor1a_t": "AKTOR NON NEGARA",
        "actor2a": "PEMUDA",
        "actor2a_t": "AKTOR NON NEGARA",
        "escalation": "KEKERASAN TERBATAS",
    }
    row.update(overrides.pop("row", {}))  # type: ignore[arg-type]
    base = {
        "accepted": True,
        "gate_probability": 0.95,
        "row": row,
        "confidence": {field: 0.9 for field in row},
        "engine": "jev",
    }
    base.update(overrides)
    return Coding(**base)  # type: ignore[arg-type]


def test_a_confident_coding_is_never_escalated() -> None:
    assert ambiguous(_coding(), threshold=0.6, band=0.15, floor=0.55) is None


def test_a_confident_rejection_is_never_escalated() -> None:
    # The commonest outcome by far. Paying a large model for it would be the
    # cheap classifier deleted and replaced with an expensive one.
    rejected = Coding(accepted=False, gate_probability=0.05, engine="jev")
    assert ambiguous(rejected, threshold=0.6, band=0.15, floor=0.55) is None


def test_a_gate_on_the_threshold_is_escalated_either_way_it_fell() -> None:
    just_under = Coding(accepted=False, gate_probability=0.55, engine="jev")
    just_over = _coding(gate_probability=0.62)
    assert ambiguous(just_under, threshold=0.6, band=0.15, floor=0.55) == "gate-borderline"
    assert ambiguous(just_over, threshold=0.6, band=0.15, floor=0.55) == "gate-borderline"


def test_a_field_the_classifier_declined_is_escalated() -> None:
    reason = ambiguous(
        _coding(row={"actor2a": "TIDAK JELAS"}), threshold=0.6, band=0.15, floor=0.55
    )
    assert reason is not None and reason.startswith("missing:actor2a")


def test_a_field_answered_by_guessing_is_escalated() -> None:
    coding = _coding()
    thin = dict(coding.confidence) | {"violence_form1": 0.31}
    reason = ambiguous(
        Coding(
            accepted=True,
            gate_probability=0.95,
            row=coding.row,
            confidence=thin,
            engine="jev",
        ),
        threshold=0.6,
        band=0.15,
        floor=0.55,
    )
    assert reason is not None and reason.startswith("low-confidence:violence_form1")


# -- what the second reader is allowed to answer ---------------------------


def _questions() -> tuple[deep.Question, ...]:
    return (
        deep.Question(field="gate", instruction="apakah kekerasan kolektif?"),
        deep.Question(
            field="violence_form1",
            instruction="bentuk kekerasan",
            options=("SERANGAN BERSENJATA", "TIDAK JELAS"),
        ),
    )


def test_a_second_reading_is_read_back_with_its_confidence() -> None:
    answers = deep._parse(
        '{"fields": {"gate": {"value": true, "confidence": 0.88}, '
        '"violence_form1": {"value": "SERANGAN BERSENJATA", "confidence": 0.7}}, '
        '"notes": "Bentrok dua kelompok pemuda."}',
        _questions(),
    )
    assert answers["gate"].value is True
    assert answers["violence_form1"].value == "SERANGAN BERSENJATA"
    assert answers["violence_form1"].probability == 0.7
    assert "dua kelompok" in str(answers["_notes"].value)


def test_an_invented_category_is_discarded_rather_than_recorded() -> None:
    # The reason the vocabulary rides with the question: a large model asked
    # about an Indonesian brawl writes a plausible category no coder used, and
    # a column holding both cannot be counted.
    answers = deep._parse(
        '{"fields": {"violence_form1": {"value": "TAWURAN ANTAR PELAJAR"}}}', _questions()
    )
    assert "violence_form1" not in answers


def test_a_bare_answer_is_accepted_without_its_envelope() -> None:
    answers = deep._parse('{"fields": {"gate": "tidak"}}', _questions())
    assert answers["gate"].value is False


def test_an_unparseable_reply_is_not_a_coding() -> None:
    with pytest.raises(deep.DeepUnavailable):
        deep._parse("maaf, saya tidak bisa menjawab", _questions())


# -- where it happened -----------------------------------------------------


def test_the_longer_place_name_wins() -> None:
    # `Sorong` sits inside `Sorong Selatan`, and a scan taking the first match
    # files the incident in the neighbouring regency.
    _, regency = places.resolve("Bentrokan di Kabupaten Sorong Selatan, Papua Barat Daya")
    assert regency is not None
    assert regency.name == "Sorong Selatan"


def test_a_regency_implies_its_province() -> None:
    province, regency = places.resolve("Kericuhan pecah di Kota Makassar pada Sabtu malam")
    assert regency is not None and regency.name == "Kota Makassar"
    assert province is not None and province.name == "Sulawesi Selatan"


def test_no_place_named_resolves_to_nothing() -> None:
    assert places.resolve("Dua kelompok terlibat bentrok pada Sabtu malam") == (None, None)


# -- how many were hurt ----------------------------------------------------


def test_figures_are_read_from_words_and_digits() -> None:
    counted = figures.casualties("Dua orang tewas dan 5 orang luka-luka dalam bentrokan itu.")
    assert counted["num_death"] == 2
    assert counted["num_injured"] == 5
    assert counted["death_injured"] == 7


def test_nobody_hurt_is_zero_and_unreported_is_missing() -> None:
    # The distinction Silver's counting rests on: blank sums as zero, the
    # missing marker sums as nothing at all.
    stated = figures.casualties("Tidak ada korban jiwa dalam kerusuhan tersebut.")
    assert stated["num_death"] == 0

    silent = figures.casualties("Bentrokan pecah di pasar. Polisi membubarkan massa.")
    assert silent["num_death"] == figures.NUM_MISSING
    assert silent["num_injured"] == figures.NUM_MISSING


def test_a_total_is_not_taken_over_a_missing_part() -> None:
    # Adding a figure to the missing marker would publish a total smaller than
    # one of its own parts.
    counted = figures.casualties("Dua orang tewas dalam bentrokan itu.")
    assert counted["num_death"] == 2
    assert counted["num_injured"] == figures.NUM_MISSING
    assert counted["death_injured"] == figures.NUM_MISSING


def test_the_fuller_figure_wins_within_one_report() -> None:
    # Reports open with a partial count and correct it further down.
    text = (
        "Satu orang tewas. Belakangan, 3 orang tewas setelah korban lain meninggal di rumah sakit."
    )
    assert figures.casualties(text)["num_death"] == 3


def test_a_beating_is_an_injury() -> None:
    assert figures.casualties("Seorang warga babak belur dihajar massa.")["num_injured"] == 1


def test_who_was_hurt_is_read_only_beside_a_figure() -> None:
    # Every one of these appeared in the corpus, on articles whose deaths were
    # counted in a different sentence: a capital city, a police chief's name
    # and a link in the newspaper's own footer.
    capital = figures.casualties(
        "Konflik antarsuku terjadi di Wamena, ibu kota Provinsi Papua Pegunungan. "
        "Bentrokan mengakibatkan dua korban meninggal dunia."
    )
    assert capital["num_death"] == 2
    assert capital["fem_death"] == figures.NUM_MISSING

    named = figures.casualties(
        "Kapolres AKBP Anak Agung Made mengatakan dua korban meninggal dunia."
    )
    assert named["child_death"] == figures.NUM_MISSING

    footer = figures.casualties(
        "Seorang remaja tewas dalam perkelahian itu.\nRamah Anak | Susunan Redaksi"
    )
    assert footer["num_death"] == 1
    assert footer["child_death"] == figures.NUM_MISSING


def test_who_was_hurt_is_read_when_the_figure_names_them() -> None:
    counted = figures.casualties("Dua anak tewas dalam kerusuhan itu.")
    assert counted["num_death"] == 2
    assert counted["child_death"] == 2

    # Nobody hurt means nobody of any description was, which is a fact and not
    # a silence — the one case where zero is the honest answer.
    none = figures.casualties("Tidak ada korban jiwa. Seorang ibu menyaksikan kejadian.")
    assert none["num_death"] == 0
    assert none["fem_death"] == 0


def test_the_victim_is_named_not_counted_as_orang() -> None:
    # Indonesian reporting says who: "satu remaja tewas", not "satu orang
    # tewas". Read only after "orang" and "warga", a stated death becomes a
    # silence on exactly the articles that state it most plainly.
    title = figures.casualties("Satu Remaja Tewas Usai Perkelahian Kelompok Pemuda di Pantai Libuo")
    assert title["num_death"] == 1

    body = figures.casualties(
        "penganiayaan berat hingga menyebabkan seorang remaja meninggal dunia"
    )
    assert body["num_death"] == 1

    assert figures.casualties("Dua nelayan tewas diterjang ombak.")["num_death"] == 2
    assert figures.casualties("Tiga pelaku dikeroyok warga.")["num_injured"] == 3


# -- one incident, several papers ------------------------------------------


def _report(**overrides: str) -> dict[str, str]:
    row = {
        "district_city": "Kota Makassar",
        "district_city_id": "73.71",
        "date": "2026-05-04",
        "violence_form1": "SERANGAN TANPA SENJATA API",
        "actor1a": "WARGA",
        "actor2a": "PEMUDA",
        "num_death": "-99",
        "num_injured": "-99",
    }
    row.update(overrides)
    return row


def test_two_papers_a_day_apart_are_one_incident() -> None:
    assert same_incident(_report(), _report(date="2026-05-05"))


def test_a_different_district_is_a_different_incident() -> None:
    assert not same_incident(
        _report(), _report(district_city="Kota Bandung", district_city_id="32.73")
    )


def test_a_different_form_is_a_different_incident() -> None:
    assert not same_incident(_report(), _report(violence_form1="PENGEBOMAN/LEDAKAN"))


def test_five_reports_of_one_brawl_become_one_event() -> None:
    reports = [_report(url=f"https://paper{n}.id/a") for n in range(5)]
    events = cluster(reports)
    assert len(events) == 1
    assert len(events[0].reports) == 5


def test_the_worst_escalation_reported_decides_the_event() -> None:
    # One paper files while the crowd is still gathering, another after the
    # market burned. Taking the first named answer would make an event's
    # escalation a fact about which newsroom was fastest.
    merged = _merge(
        [
            _report(escalation="ANCAMAN/KETEGANGAN"),
            _report(escalation="KERUSUHAN"),
            _report(escalation="MEREDA"),
        ]
    )
    assert merged["escalation"] == "KERUSUHAN"


def test_an_unranked_escalation_never_outranks_a_real_one() -> None:
    merged = _merge([_report(escalation="TIDAK JELAS"), _report(escalation="MEREDA")])
    assert merged["escalation"] == "MEREDA"


def test_the_fuller_report_decides_the_merged_row() -> None:
    merged = _merge(
        [
            _report(num_death="-99", weapon_type1="TIDAK JELAS"),
            _report(num_death="2", weapon_type1="SENJATA JARAK DEKAT"),
        ]
    )
    # A figure beats the missing marker, and a named category beats "the
    # reporting did not say" — papers report an incident at different stages.
    assert merged["num_death"] == "2"
    assert merged["weapon_type1"] == "SENJATA JARAK DEKAT"


def test_an_event_id_survives_a_recompute() -> None:
    # Clustering is recomputed from Bronze every run rather than merged into,
    # so an id that moved each time would break every reference to it.
    assert _event_id(_report()) == _event_id(_report())
    assert _event_id(_report()) != _event_id(_report(date="2026-06-01"))


# -- the vocabulary --------------------------------------------------------


def test_a_parenthetical_does_not_make_a_second_category() -> None:
    # VEWS writes both spellings in the same year, in different columns.
    assert canonical("SENJATA API (TERMASUK SENAPAN ANGIN, SENJATA RAKITAN, DSB)") == "SENJATA API"


def test_a_renamed_category_folds_onto_the_one_in_use() -> None:
    assert canonical("SERANGAN TANPA SENJATA") == "SERANGAN TANPA SENJATA API"


def test_dates_inside_the_window() -> None:
    from terusan_pipelines.sources.news.article import Article, in_window

    since = date(2026, 9, 16)
    assert in_window(Article(url="u", status=200, published_at=date(2026, 9, 20)), since)
    assert not in_window(Article(url="u", status=200, published_at=date(2026, 1, 1)), since)
    # No date readable: admitted rather than dropped. Several outlets state one
    # nowhere, and dropping them loses those outlets entirely.
    assert in_window(Article(url="u", status=200, published_at=None), since)


# -- read everything, keep the issue ---------------------------------------


def test_the_gate_needs_the_dictionary_first() -> None:
    """An article the dictionary rejects never reaches the classifier.

    Not an optimisation to be tidied away: the crawl reads every article a
    paper published, and putting all of them to a model would be the cost of
    the whole feature rather than a fraction of it.
    """
    from terusan_pipelines.sources.news.monitoring import NewsMonitoring

    class Article:
        text = "Harga cabai naik menjelang akhir pekan"
        url = "https://example.id/a"

    found = VIOLENCE_PROFILE.dictionary().match(Article.text)
    kept, probability, engine = NewsMonitoring()._gate(VIOLENCE_PROFILE, Article(), found)
    assert kept is False
    assert probability is None
    assert engine == "dictionary"


def test_a_tally_counts_what_was_read_not_what_was_kept() -> None:
    from terusan_pipelines.extract.news import TALLIES_DATASET, NewsTallyExtractor

    record = {
        "outlet": "POS KOTA",
        "outlet_host": "poskota.co.id",
        "outlet_province": "DKI JAKARTA",
        "profile": "violence",
        "days": {"2026-09-22": {"scanned": 5, "matched": 1, "recorded": 0}},
    }
    rows = list(_tally_rows(record))
    assert len(rows) == 1
    columns = rows[0]["columns"]
    assert rows[0]["dataset"] == TALLIES_DATASET
    # Read five, matched one, kept none: three different numbers, and a rate
    # cannot be taken without all three.
    assert (columns["scanned"], columns["matched"], columns["recorded"]) == ("5", "1", "0")
    assert NewsTallyExtractor().target == "records"


def _tally_rows(record: dict):
    """Run the tally extractor over a record written to a temp file."""
    import json
    import tempfile
    from pathlib import Path

    from terusan_pipelines.extract.base import Landed
    from terusan_pipelines.extract.news import NewsTallyExtractor

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tally.json"
        path.write_text(json.dumps(record))
        landed = Landed(
            path=path,
            document_id="doc_test",
            content_hash="0" * 64,
            source_slug="news-monitoring",
            dataset="daily-tallies",
        )
        assert NewsTallyExtractor().handles(landed)
        yield from NewsTallyExtractor().extract(landed)


def test_a_rerun_does_not_double_a_day() -> None:
    """Two tallies for one outlet and day are one day, not two.

    A tally is landed per run, so an outlet read twice in a day — two shards,
    or a manual run beside the scheduled one — lands two of them. Summed as
    they stand, the paper would look twice as productive.
    """
    from terusan_pipelines.news.cluster import _reading_rows
    from terusan_pipelines.news.profiles import VIOLENCE

    tally = {
        "date": "2026-09-22",
        "outlet_host": "poskota.co.id",
        "outlet_province": "DKI JAKARTA",
        # Carried on every real tally, and what decides whether the outlet has
        # a province at all — see the national-paper case below.
        "outlet_geo_id": "ID-31",
        "scanned": "5",
        "matched": "1",
        "recorded": "0",
    }
    rows = _reading_rows([tally, dict(tally)], VIOLENCE)
    read = [
        row["columns"]
        for row in rows
        if row["columns"]["indicator"] == "news_articles_read"
        and row["columns"]["geo_level"] == "province"
    ]
    assert len(read) == 1
    assert read[0]["value"] == "5"


# -- reading a sitemap and a feed ------------------------------------------
#
# These failed silently for every outlet: the first version parsed XML with an
# HTML parser, which treats `<link>` as a void element and infers a nesting the
# document does not have, so the whole sitemap and feed route returned an empty
# list and said nothing. The outlets that still yielded articles were the ones
# whose section page is ordinary HTML, which made it look like it worked.

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.co.id/2026/09/21/bentrok-warga-di-pasar-baru</loc>
    <lastmod>2026-09-21</lastmod>
  </url>
  <url>
    <loc>https://example.co.id/2026/01/02/kabar-lama-tentang-sesuatu-yang-lain</loc>
    <lastmod>2026-01-02</lastmod>
  </url>
</urlset>"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.co.id/sitemap_news.xml</loc><lastmod>2026-09-23</lastmod></sitemap>
</sitemapindex>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example</title>
  <item>
    <title>Bentrok warga</title>
    <link>https://example.co.id/2026/09/21/bentrok-warga-di-pasar-baru</link>
    <pubDate>Mon, 21 Sep 2026 10:00:00 +0700</pubDate>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Bentrok warga</title>
    <link href="https://example.co.id/2026/09/21/bentrok-warga-di-pasar-baru"/>
    <updated>2026-09-21T10:00:00+07:00</updated>
  </entry>
</feed>"""


def test_a_sitemap_is_read_with_its_dates() -> None:
    from terusan_pipelines.sources.news.discover import _locs

    found = _locs(SITEMAP)
    assert len(found) == 2
    assert found[0] == (
        "https://example.co.id/2026/09/21/bentrok-warga-di-pasar-baru",
        date(2026, 9, 21),
    )


def test_a_sitemap_index_is_read() -> None:
    from terusan_pipelines.sources.news.discover import _locs

    found = _locs(SITEMAP_INDEX)
    assert found == [("https://example.co.id/sitemap_news.xml", date(2026, 9, 23))]


def test_rss_puts_the_url_in_the_text_and_atom_in_an_attribute() -> None:
    from terusan_pipelines.sources.news.discover import _locs

    article = "https://example.co.id/2026/09/21/bentrok-warga-di-pasar-baru"
    assert _locs(RSS)[0] == (article, date(2026, 9, 21))
    assert _locs(ATOM)[0] == (article, date(2026, 9, 21))


def test_a_page_that_is_not_xml_reads_as_nothing() -> None:
    from terusan_pipelines.sources.news.discover import _locs

    # An outlet answering a missing sitemap with its 404 page, which is common.
    assert _locs("<!DOCTYPE html><html><body>Not found</body></html>") == []


def test_a_date_in_the_future_is_not_a_publication_date() -> None:
    """A page claiming next month was not published next month.

    Taken at face value it files the article in a month that has not happened
    and skews that month's counts — and, before this, made an outlet report
    having last been collected in the future.
    """
    from datetime import timedelta

    from terusan_pipelines.sources.news.article import parse, today

    ahead = (today() + timedelta(days=30)).isoformat()
    html = f'<html><head><meta property="article:published_time" content="{ahead}">'.encode()
    assert parse(html, "https://example.co.id/a-b-c-d").published_at is None

    # A day ahead is a timezone, not a fault: Jakarta is seven hours ahead of
    # UTC, so this evening's piece is legitimately tomorrow by a UTC clock.
    tomorrow = (today() + timedelta(days=1)).isoformat()
    html = f'<html><head><meta property="article:published_time" content="{tomorrow}">'.encode()
    assert parse(html, "https://example.co.id/a-b-c-d").published_at is not None


def test_a_national_paper_does_not_become_a_province() -> None:
    """`NASIONAL` is not a place, and treating it as one breaks the series.

    The geography registry resolves that word to Indonesia, so a province row
    for a national paper and the country total collapse onto one observation
    holding two different figures — which Silver refuses, taking the whole
    reading series with it.
    """
    from terusan_pipelines.news.cluster import _reading_rows
    from terusan_pipelines.news.profiles import VIOLENCE

    national = {
        "date": "2026-09-22",
        "outlet_host": "kompas.id",
        "outlet_province": "NASIONAL",
        "outlet_geo_id": "IDN",
        "scanned": "60",
        "matched": "2",
        "recorded": "1",
    }
    geos = {
        row["columns"]["geo"]
        for row in _reading_rows([national], VIOLENCE)
        if row["columns"]["indicator"] == "news_articles_read"
    }
    assert geos == {"Indonesia"}

    # A provincial paper still contributes to both its province and the total.
    provincial = national | {
        "outlet_host": "gopos.id",
        "outlet_province": "GORONTALO",
        "outlet_geo_id": "ID-75",
    }
    geos = {
        row["columns"]["geo"]
        for row in _reading_rows([provincial], VIOLENCE)
        if row["columns"]["indicator"] == "news_articles_read"
    }
    assert geos == {"Indonesia", "Provinsi GORONTALO"}


# -- the register is not always right --------------------------------------
#
# Every regency of the six Papua provinces is filed in
# `reference/geography/indonesia-regencies.csv` under Papua Barat or Papua
# Barat Daya: the four created in 2022 have no regencies assigned to them at
# all, and the file's own header warns those codes are unverified. Following a
# regency to its parent therefore put Nabire, Mimika, Jayawijaya and Asmat in
# Papua Barat, which is how a small province came to hold two-fifths of the
# country's coded collective violence.


@pytest.mark.parametrize(
    ("text", "province", "regency"),
    [
        ("Bentrokan di Kabupaten Nabire, Papua Tengah, Rabu malam.", "Papua Tengah", "Nabire"),
        ("Kericuhan di Jayawijaya, Papua Pegunungan.", "Papua Pegunungan", "Jayawijaya"),
        ("Warga Asmat, Papua Selatan, terlibat bentrok.", "Papua Selatan", "Asmat"),
    ],
)
def test_a_named_province_beats_a_wrong_parent(text: str, province: str, regency: str) -> None:
    found_province, found_regency = places.resolve(text)
    assert found_province is not None and found_province.name == province
    assert found_regency is not None and found_regency.name == regency


def test_a_shorter_place_name_inside_a_longer_one_does_not_also_match() -> None:
    """`Papua Tengah` contains `Papua`, which is itself a province.

    Both matching made every report from the new Papua provinces look like it
    named two places, which is the case where the rule above stands down and
    takes the register's answer — so the correction never fired.
    """
    province, _ = places.resolve("Bentrok di Kabupaten Mimika, Papua Tengah.")
    assert province is not None and province.name == "Papua Tengah"


def test_the_register_still_decides_where_the_report_says_nothing() -> None:
    # No province named: the regency's parent is all there is, wrong or not.
    province, regency = places.resolve("Kericuhan pecah di Kota Makassar pada Sabtu malam.")
    assert regency is not None and regency.name == "Kota Makassar"
    assert province is not None and province.name == "Sulawesi Selatan"


def test_a_known_wrong_parent_is_not_asserted() -> None:
    """Better no province than the wrong one.

    Mimika is in Papua Tengah; the reference files it under Papua Barat. With
    nothing in the report to correct that, the province is left unresolved —
    an unresolved province costs a row in the provincial counts, while the
    wrong one puts a killing a thousand kilometres away and is not recoverable
    by anybody reading the figures.
    """
    province, regency = places.resolve("Bentrok di Kabupaten Mimika pecah Rabu.")
    assert regency is not None and regency.name == "Mimika"
    assert province is None

    # Named in the report, so it resolves.
    province, _ = places.resolve("Bentrok di Mimika, Papua Tengah.")
    assert province is not None and province.name == "Papua Tengah"


def test_clustering_reads_only_the_current_parser_version() -> None:
    """Re-extraction appends; clustering must not add the old rows back.

    An article re-read under an improved parser keeps its earlier rows, which
    is what makes a Bronze partition traceable to the code that produced it.
    Clustering without that filter counted the same article once per parser
    version it had ever been read under — a hundred and three codings where
    there were twenty-two — and the oldest, most wrong ones outvoted the
    correction in the merge.
    """
    import inspect

    from terusan_pipelines.extract.base import PARSER_VERSION
    from terusan_pipelines.news import cluster

    for reader in (cluster.read_codings, cluster.read_tallies):
        source = inspect.getsource(reader)
        assert "parser_version" in source, f"{reader.__name__} must filter by parser version"
    # And the filter must be the running version, not a pinned string.
    assert PARSER_VERSION in (cluster.PARSER_VERSION,)


# -- what counts as an article, revisited ----------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # `ajnn.net` serves every article as a directory with an index file.
        # The skip list matched the `index` inside the filename, so the outlet
        # was crawled, found thirty-five pages a day and contributed none of
        # them — a silence indistinguishable from a paper with nothing to say.
        "https://www.ajnn.net/news/negeri-yang-tak-belajar-dari-bencana/index.html",
        "https://example.co.id/read/bentrok-antar-warga-pecah-di-pasar/index.php",
    ],
)
def test_a_directory_style_article_is_an_article(url: str) -> None:
    host = url.split("/")[2].removeprefix("www.")
    assert search.is_article(url, host)


@pytest.mark.parametrize(
    "url",
    [
        # The words still reject a listing, as whole segments.
        "https://www.ajnn.net/category/hukum/index.html",
        "https://example.co.id/indeks",
        "https://example.co.id/tag/bentrok-warga-di-pasar",
    ],
)
def test_a_listing_is_still_not_an_article(url: str) -> None:
    host = url.split("/")[2].removeprefix("www.")
    assert not search.is_article(url, host)


def test_a_speculative_probe_never_costs_a_browser_render() -> None:
    """Guessing at sitemap paths must not spend an outlet's goodwill.

    `korankaltim.com` answers 403 to a robot, so each of the nine sitemap and
    feed paths guessed at was being retried through a headless browser. By the
    time the crawl asked for the section page it actually wanted, the site had
    stopped serving the session: the same URL that returns a quarter of a
    megabyte when asked once returned the block page when asked tenth, and the
    outlet contributed nothing at all.
    """
    import httpx

    from terusan_pipelines.sources.news import discover

    rendered: list[str] = []

    class Blocked:
        def get(self, url: str):
            request = httpx.Request("GET", url)
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("blocked", request=request, response=response)

    def render(url: str):
        rendered.append(url)
        return 200, "<html><body>rendered</body></html>"

    # A guess: no browser, nothing returned.
    assert discover._get("https://x.co.id/sitemap.xml", Blocked(), render, speculative=True) == ""
    assert rendered == []

    # A page we actually want: the browser is used.
    assert discover._get("https://x.co.id/c/patroli", Blocked(), render) != ""
    assert rendered == ["https://x.co.id/c/patroli"]


def test_a_recompute_with_nothing_to_write_writes_no_file(tmp_path, monkeypatch):
    """An empty write partitioned by source lands one part at the root of
    `records`, and DuckDB then refuses every hive-partitioned Bronze read —
    which took down every normalization after it on 2026-09-25."""
    from pathlib import Path

    from terusan_pipelines.news import cluster as clustering
    from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver

    resolver = StorageResolver(StorageConfig(STORAGE_BACKEND="local", STORAGE_ROOT=str(tmp_path)))
    monkeypatch.setattr(clustering, "read_codings", lambda *a, **k: [])
    monkeypatch.setattr(clustering, "read_tallies", lambda *a, **k: [])

    result = clustering.recluster("violence", resolver=resolver)

    records = Path(resolver.resolve(Layer.BRONZE, "records"))
    assert result["events"] == 0
    assert not records.exists() or not list(records.glob("*.parquet"))
