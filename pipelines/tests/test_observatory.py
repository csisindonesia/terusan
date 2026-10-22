"""The content restriction observatory, from landed report to observation.

The fixtures are cut from the published files, row for row: TikTok's 2025 H2
CSV, Google's government removals archive, and Komdigi's workbooks, each
reduced to the rows that exercise a rule — a country and a worldwide row, a
withheld small count, a missing marker, a published total that disagrees with
its rows. Nothing is invented except where a test says it alters a figure, to
stand for a revised release.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from terusan_pipelines.extract import ExtractionRunner, Landed
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.transparency import (
    GOOGLE_BREAKDOWN,
    GOOGLE_TOTALS,
    TIKTOK_COUNTRIES,
    TIKTOK_WORLDWIDE,
    GoogleRemovalsExtractor,
    KomdigiContentExtractor,
    TikTokRemovalsExtractor,
    google_period,
    stated_period,
    tiktok_period,
)
from terusan_pipelines.normalize import parse_value as silver_parse_value
from terusan_pipelines.normalize.values import ValueStatus
from terusan_pipelines.observatory import analytics
from terusan_pipelines.observatory import taxonomy as tx
from terusan_pipelines.observatory.adapters import (
    BronzeRow,
    GoogleAdapter,
    KomdigiAdapter,
    TikTokAdapter,
)
from terusan_pipelines.observatory.model import Observation, Quarantined
from terusan_pipelines.observatory.pipeline import ObservatoryBuilder, _revisions
from terusan_pipelines.observatory.quality import parse_value
from terusan_pipelines.sources import Artifact, Landing
from terusan_pipelines.sources.komdigi.content_handling import (
    KomdigiContentHandling,
    public_url,
    wanted,
)
from terusan_pipelines.sources.transparency.google import GoogleGovernmentRemovals
from terusan_pipelines.sources.transparency.tiktok import TikTokGovernmentRemovals
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import Warehouse

# -- fixtures ------------------------------------------------------------------

TIKTOK_HEADER = (
    "﻿replication_info,entity,aspect,metric_name,period,period_value,policy,policy_value,"
    "task,task_value,location,location_value,result,base,date\n"
)


def tiktok_row(metric: str, period: str, country: str, value: str, location="country_name") -> str:
    return (
        f",LIGR,GRFCR,{metric},half-year,{period},all,all,all,all,{location},{country},{value},,\n"
    )


def tiktok_csv(requests: str = "1113.0") -> bytes:
    """Indonesia, Jul–Dec 2025, as published; and 2019's worldwide total."""
    rows = [
        tiktok_row("Total requests received", "Jul-Dec 2025", "Indonesia", requests),
        tiktok_row("Total content received", "Jul-Dec 2025", "Indonesia", "3895.0"),
        tiktok_row(
            "Content actioned due to community guidelines violations",
            "Jul-Dec 2025",
            "Indonesia",
            "1359.0",
        ),
        tiktok_row(
            "Content actioned due to (local) law violations", "Jul-Dec 2025", "Indonesia", "1282.0"
        ),
        tiktok_row("Content not actioned", "Jul-Dec 2025", "Indonesia", "1254.0"),
        tiktok_row("Removal rate", "Jul-Dec 2025", "Indonesia", "0.624392783"),
        tiktok_row("Total requests received", "Jul-Dec 2025", "Malaysia", "6302.0"),
        tiktok_row("Total Government Requests", "Jan-Jun 2019", "all", "1.0", location="all"),
    ]
    return (TIKTOK_HEADER + "".join(rows)).encode("utf-8")


GOOGLE_CURRENT = (
    "time_period,country,cldr_territory,requestor,product,reason,number_of_requests,"
    "items_requested_removal,items_removed_legal,items_removed_policy,items_content_not_found,"
    "items_not_enough_info,items_no_action_taken,items_content_already_removed\n"
    "January - June 2025,Indonesia,ID,Consumer Protection Authority,Play Apps,Fraud,"
    "3,7,5,0,1,0,1,0\n"
    "January - June 2025,Indonesia,ID,Government Officials,YouTube,National security,"
    "1,1,0,0,0,1,0,0\n"
)

GOOGLE_HIST_TOTALS = (
    "Period Ending,Country/Region,CLDR Territory Code,All Requests: Number of Requests,"
    "All Requests: % Fully Or Partially Complied With,All Requests: Items Requested To Be Removed,"
    "Court Orders: Number of Requests,Court Orders: % Fully Or Partially Complied With,"
    "Court Orders: Items Requested To Be Removed,"
    '"Other Requests (Executive, Police, etc.): Number of Requests",'
    '"Other Requests (Executive, Police, etc.): % Fully Or Partially Complied With",'
    '"Other Requests (Executive, Police, etc.): Items Requested To Be Removed"\n'
    "2009-12-31,Indonesia,ID,<10,?,,,,,,,\n"
    "2018-06-30,Indonesia,ID,70,,1744,,,,70,,\n"
)


def google_zip(current: str = GOOGLE_CURRENT) -> bytes:
    buffer = io.BytesIO()
    root = "google-government-removals-new-csv"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{root}/README.txt", "the reporting format we used through 2018")
        archive.writestr(f"{root}/google-government-removal-requests.csv", current)
        archive.writestr(
            f"{root}/historical/google-government-removal-requests-historical.csv",
            GOOGLE_HIST_TOTALS,
        )
    return buffer.getvalue()


def komdigi_workbook(rows: list[tuple]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


WEBSITES = [
    ("No.", "Kategori Konten", "Jumlah Pemblokiran"),
    (1, "Pornografi", 1107547),
    (2, "Perjudian", 423453),
    (3, "Penipuan", 14757),
    ("Total Pemblokiran Konten Negatif", None, 2679352),
]

SOCIAL_2024 = [
    ("Penanganan Konten Internet Negatif Pada Sosial Media 2024", None),
    ("META", 416078),
    ("X", 46023),
    ("HELLO-APP", "-"),
]


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


def landed(resolver, meta, content: bytes, filename: str, **kw) -> Landed:
    result = Landing(resolver).land(
        meta,
        Artifact(
            content=content,
            filename=filename,
            dataset=kw.pop("dataset", "test"),
            retrieved_at=kw.pop("retrieved_at", datetime(2026, 10, 22, tzinfo=UTC)),
            **kw,
        ),
    )
    return Landed.from_metadata(Path(result.path).parent / "metadata.json")


def records(extractor, item: Landed) -> list[dict]:
    return list(extractor.extract(item))


def bronze(
    record: dict, document_id: str = "doc_1", retrieved: datetime | None = None
) -> BronzeRow:
    return BronzeRow(
        source_id="test",
        dataset=record["dataset"],
        columns=record["columns"],
        document_id=document_id,
        content_hash="0" * 64,
        raw_path="raw/x",
        source_url="https://example.invalid",
        retrieved_at=retrieved or datetime(2026, 10, 22, tzinfo=UTC),
        parser_version="26",
        source_report_id=f"report-{document_id}",
    )


# -- periods -------------------------------------------------------------------


def test_half_years_are_read_from_each_publishers_label() -> None:
    assert google_period("January - June 2025")["period"] == "2025-S1"
    assert google_period("July - December 2024")["period_end"] == "2024-12-31"
    assert google_period("2011-06-30")["period"] == "2011-S1"
    assert tiktok_period("Jul-Dec 2025")["period"] == "2025-S2"


def test_a_date_that_ends_no_half_year_is_not_guessed_at() -> None:
    assert google_period("2011-03-31") is None


def test_komdigi_states_a_range_or_a_year_in_its_filenames() -> None:
    ranged = stated_period(
        "penanganan-konten-negatif-pada-situs-internet-periode-januari-2018-oktober-2021.xlsx"
    )
    assert ranged == {"period": "", "period_start": "2018-01-01", "period_end": "2021-10-31"}
    assert (
        stated_period("Penanganan-Konten-Internet-Negatif-Pada-Sosial-Media-2024.xlsx")["period"]
        == "2024"
    )


def test_two_bare_years_are_not_a_period() -> None:
    assert stated_period("Perkembangan Pendapatan 2017-2020") is None


# -- values --------------------------------------------------------------------


def test_a_withheld_small_count_is_neither_a_number_nor_missing() -> None:
    value, status, problem = parse_value("<10", tx.Unit.COUNT)
    assert (value, status, problem) == (None, "below_threshold", None)


def test_a_missing_marker_is_unavailable_and_never_zero() -> None:
    assert parse_value("-", tx.Unit.COUNT) == (None, "not_available", None)
    assert parse_value("?", tx.Unit.PERCENT) == (None, "not_available", None)


@pytest.mark.parametrize(
    ("raw", "unit"),
    [
        ("-5", tx.Unit.COUNT),
        ("2.5", tx.Unit.COUNT),
        ("101", tx.Unit.PERCENT),
        ("1.2", tx.Unit.RATIO),
        ("abc", tx.Unit.COUNT),
    ],
)
def test_figures_that_cannot_be_vouched_for_are_refused(raw: str, unit: tx.Unit) -> None:
    _, _, problem = parse_value(raw, unit)
    assert problem


def test_silver_reads_a_withheld_count_as_suppressed() -> None:
    assert silver_parse_value("<10").status is ValueStatus.SUPPRESSED
    assert silver_parse_value("?").status is ValueStatus.MISSING


# -- sources -------------------------------------------------------------------


def test_komdigis_internal_ckan_address_becomes_the_portals_public_one() -> None:
    internal = "http://172.30.103.58:5000/dataset/abc/resource/def/download/file.xlsx"
    assert public_url(internal) == (
        "https://data.komdigi.go.id/warehouse/dataset/abc/resource/def/download/file.xlsx"
    )


def test_only_the_supervising_directorates_content_datasets_are_wanted() -> None:
    org = {"title": "DIREKTORAT JENDERAL PENGAWASAN RUANG DIGITAL"}
    assert wanted({"name": "jumlah-konten-negatif-yang-diblokir", "organization": org})
    assert not wanted({"name": "daftar-sistem-elektronik-2021", "organization": org})
    assert not wanted(
        {
            "name": "layanan-pengaduan-konten-siaran",
            "organization": {"title": "SEKRETARIAT JENDERAL"},
        }
    )


def test_tiktoks_link_is_matched_for_the_report_and_nothing_else() -> None:
    pattern = TikTokGovernmentRemovals.file_pattern
    assert pattern.search(
        "https://sf16-va.tiktokcdn.com/obj/eden-va2/x[/y/2025H2_CLIGR/Final/Downloads/2025H2_GRFCR_English.csv"
    )
    assert not pattern.search("https://sf16-va.tiktokcdn.com/obj/eden-va2/x/2025H2_CGE_English.csv")


# -- extraction ----------------------------------------------------------------


def test_tiktok_figures_are_read_one_per_measure_with_the_country_resolved(resolver) -> None:
    item = landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    rows = records(TikTokRemovalsExtractor(), item)

    indonesia = [r["columns"] for r in rows if r["columns"]["country_raw"] == "Indonesia"]
    assert {r["country_code"] for r in indonesia} == {"ID"}
    requests = next(r for r in indonesia if r["measure"] == "Total requests received")
    # `1113.0` is pandas, not precision.
    assert requests["value"] == "1113"
    assert requests["indicator"] == "tiktok_gov_requests_received"
    assert requests["period"] == "2025-S2"


def test_tiktoks_worldwide_total_is_kept_but_is_no_countrys_series(resolver) -> None:
    item = landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    rows = records(TikTokRemovalsExtractor(), item)

    worldwide = [r for r in rows if r["dataset"] == TIKTOK_WORLDWIDE]
    assert len(worldwide) == 1
    assert worldwide[0]["columns"]["indicator"] == ""
    assert all(r["dataset"] == TIKTOK_COUNTRIES for r in rows if r not in worldwide)


def test_googles_totals_and_its_breakdown_are_both_silver_bound(resolver) -> None:
    item = landed(
        resolver, GoogleGovernmentRemovals.meta, google_zip(), "google-government-removals.zip"
    )
    rows = records(GoogleRemovalsExtractor(), item)

    totals = [r["columns"] for r in rows if r["dataset"] == GOOGLE_TOTALS]
    breakdown = [r["columns"] for r in rows if r["dataset"] == GOOGLE_BREAKDOWN]
    assert totals and all(r["indicator"] for r in totals)
    # The 2019-onward rows as published: one figure per requester, product and
    # reason, each under a breakdown series and its category — never summed.
    assert breakdown and all(r["indicator"].startswith("google_gov_breakdown_") for r in breakdown)
    requests = next(
        r
        for r in breakdown
        if r["measure"] == "number_of_requests" and r["product_raw"] == "YouTube"
    )
    assert requests["category"] == "Government Officials · YouTube · National security"
    assert requests["period"] == "2025-S1"
    # An empty cell is no record; a withheld count is one, as written.
    withheld = [r for r in totals if r["period"] == "2009-S2"]
    assert {r["value"] for r in withheld} == {"<10", "?"}


def test_a_google_archive_that_lost_a_column_is_refused(resolver) -> None:
    reshaped = GOOGLE_CURRENT.replace("items_removed_legal,", "")
    item = landed(
        resolver,
        GoogleGovernmentRemovals.meta,
        google_zip(reshaped),
        "google-government-removals.zip",
    )
    with pytest.raises(ExtractionError, match="items_removed_legal"):
        records(GoogleRemovalsExtractor(), item)


def test_komdigis_workbook_reads_its_period_from_the_filename(resolver) -> None:
    item = landed(
        resolver,
        KomdigiContentHandling.meta,
        komdigi_workbook(SOCIAL_2024),
        "penanganan-konten-internet-negatif-pada-sosial-media-2024.xlsx",
    )
    rows = [r["columns"] for r in records(KomdigiContentExtractor(), item)]

    assert [r["platform"] for r in rows] == ["meta", "x", "hello_app"]
    assert {r["period"] for r in rows} == {"2024"}
    assert rows[2]["value"] == "-"
    assert rows[1]["series_name"] == "Komdigi: negative content handled on X (Twitter)"


def test_komdigis_website_total_is_marked_as_a_total(resolver) -> None:
    item = landed(
        resolver,
        KomdigiContentHandling.meta,
        komdigi_workbook(WEBSITES),
        "penanganan-konten-negatif-pada-situs-internet-periode-januari-2018-oktober-2021.xlsx",
    )
    rows = [r["columns"] for r in records(KomdigiContentExtractor(), item)]

    assert [r["row_role"] for r in rows] == ["component"] * 3 + ["total"]
    assert not any(r["indicator"] for r in rows)


# -- adapters ------------------------------------------------------------------


def test_tiktoks_parts_are_checked_against_its_total(resolver) -> None:
    item = landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "f.csv")
    adapter = TikTokAdapter()
    observations = [adapter.normalize(bronze(r)) for r in records(TikTokRemovalsExtractor(), item)]
    kept, _ = adapter.validate([o for o in observations if isinstance(o, Observation)])

    # 1359 + 1282 + 1254 = 3895: TikTok's file agrees with itself.
    assert not any(o.quality_flags and "components" in " ".join(o.quality_flags) for o in kept)


def test_tiktok_parts_that_do_not_add_up_are_flagged_not_changed(resolver) -> None:
    content = tiktok_csv().replace(b"3895.0", b"3900.0")
    item = landed(resolver, TikTokGovernmentRemovals.meta, content, "f.csv")
    adapter = TikTokAdapter()
    observations = [adapter.normalize(bronze(r)) for r in records(TikTokRemovalsExtractor(), item)]
    kept, _ = adapter.validate([o for o in observations if isinstance(o, Observation)])

    total = next(o for o in kept if o.source_label == "Total content received")
    assert total.metric_value == Decimal("3900")
    assert "components_do_not_sum_to_total_content_received" in total.quality_flags


def test_actioned_is_not_called_removed() -> None:
    measure = next(
        m for m in tx.TIKTOK_MEASURES if m.label.startswith("Content actioned due to (local)")
    )
    assert measure.metric is tx.Metric.ITEMS_ACTIONED


def test_a_google_requester_nobody_has_mapped_is_quarantined(resolver) -> None:
    current = GOOGLE_CURRENT.replace("Government Officials", "Ministry Of Truth")
    item = landed(resolver, GoogleGovernmentRemovals.meta, google_zip(current), "g.zip")
    outcomes = [
        GoogleAdapter().normalize(bronze(r)) for r in records(GoogleRemovalsExtractor(), item)
    ]

    refused = [o for o in outcomes if isinstance(o, Quarantined)]
    assert refused and all("Ministry Of Truth" in q.reason for q in refused)


def test_googles_requesters_are_never_named_as_an_agency(resolver) -> None:
    item = landed(resolver, GoogleGovernmentRemovals.meta, google_zip(), "g.zip")
    outcomes = [
        GoogleAdapter().normalize(bronze(r)) for r in records(GoogleRemovalsExtractor(), item)
    ]

    observations = [o for o in outcomes if isinstance(o, Observation)]
    assert observations and all(o.requester_agency is None for o in observations)


def test_komdigis_total_that_disagrees_with_its_rows_is_quarantined(resolver) -> None:
    item = landed(
        resolver,
        KomdigiContentHandling.meta,
        komdigi_workbook(WEBSITES),
        "penanganan-konten-negatif-pada-situs-internet-periode-januari-2018-oktober-2021.xlsx",
    )
    adapter = KomdigiAdapter()
    outcomes = [adapter.normalize(bronze(r)) for r in records(KomdigiContentExtractor(), item)]
    kept, refused = adapter.validate([o for o in outcomes if isinstance(o, Observation)])

    assert len(kept) == 3
    assert len(refused) == 1 and "2679352" in refused[0].reason and "1545757" in refused[0].reason
    assert all("publisher_total_disagrees" in o.quality_flags for o in kept)
    assert all(o.requester_agency for o in kept)


def test_identity_ignores_the_value_and_the_record_hash_does_not(resolver) -> None:
    adapter = TikTokAdapter()
    one = [
        r
        for r in records(
            TikTokRemovalsExtractor(),
            landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "a.csv"),
        )
    ]
    two = [
        r
        for r in records(
            TikTokRemovalsExtractor(),
            landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv("1200.0"), "b.csv"),
        )
    ]
    first = adapter.normalize(bronze(one[0]))
    revised = adapter.normalize(bronze(two[0], document_id="doc_2"))

    assert first.identity_hash == revised.identity_hash
    assert first.record_hash != revised.record_hash


def test_a_revision_is_kept_beside_the_figure_it_revises() -> None:
    def observation(value: str, when: datetime) -> Observation:
        row = bronze(
            {
                "dataset": TIKTOK_COUNTRIES,
                "columns": {
                    "measure": "Total requests received",
                    "value": value,
                    "period": "2025-S2",
                    "period_start": "2025-07-01",
                    "period_end": "2025-12-31",
                    "country_raw": "Indonesia",
                    "country_code": "ID",
                    "policy": "all",
                    "task": "all",
                },
            },
            document_id=f"doc_{value}",
            retrieved=when,
        )
        result = TikTokAdapter().normalize(row)
        assert isinstance(result, Observation)
        return result

    early = observation("1113", datetime(2026, 1, 1, tzinfo=UTC))
    late = observation("1120", datetime(2026, 6, 1, tzinfo=UTC))
    rows, revised = _revisions([late, early])

    assert revised == 1
    by_value = {o.raw_value: (number, current) for o, number, current, _ in rows}
    assert by_value == {"1113": (1, False), "1120": (2, True)}


# -- end to end ----------------------------------------------------------------


def build(resolver) -> dict:
    ExtractionRunner(resolver).run()
    return ObservatoryBuilder(resolver).build().as_dict()


def observations(resolver) -> list[dict]:
    with Warehouse(resolver) as w:
        relation = w.query(
            f"SELECT id, provider, metric, raw_value, revision, is_current, source_report_id "
            f"FROM {w.source(Layer.SILVER, 'restriction_observations')} ORDER BY id"
        )
        return [dict(zip(relation.columns, r, strict=True)) for r in relation.fetchall()]


def test_a_landed_report_becomes_traceable_observations(resolver) -> None:
    landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    landed(resolver, GoogleGovernmentRemovals.meta, google_zip(), "google-government-removals.zip")

    result = build(resolver)
    rows = observations(resolver)

    assert result["providers"]["tiktok"]["accepted"] == 8
    assert result["providers"]["google"]["quarantined"] == 0
    with Warehouse(resolver) as w:
        reports = {
            r[0]
            for r in w.query(
                f"SELECT id FROM {w.source(Layer.SILVER, 'restriction_source_reports')}"
            ).fetchall()
        }
    assert rows and {r["source_report_id"] for r in rows} <= reports


def test_ingesting_the_same_report_again_duplicates_nothing(resolver) -> None:
    landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    build(resolver)
    first = observations(resolver)

    # Landing the same bytes again resolves to the same RAW path, and a
    # rebuild from the same Bronze gives the same record hashes.
    landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    build(resolver)

    assert observations(resolver) == first


def test_a_revised_release_creates_an_auditable_revision(resolver) -> None:
    landed(
        resolver,
        TikTokGovernmentRemovals.meta,
        tiktok_csv(),
        "2025h2_grfcr_english.csv",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    build(resolver)
    # A later release restating Indonesia's requests; every other figure
    # repeats unchanged.
    landed(
        resolver,
        TikTokGovernmentRemovals.meta,
        tiktok_csv("1120.0"),
        "2026h1_grfcr_english.csv",
        retrieved_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    result = build(resolver)

    rows = [
        r
        for r in observations(resolver)
        if r["metric"] == "requests_received" and r["raw_value"] in {"1113", "1120"}
    ]
    assert {(r["raw_value"], r["revision"], r["is_current"]) for r in rows} == {
        ("1113", 1, False),
        ("1120", 2, True),
    }
    assert result["revised_identities"] == 1
    assert result["providers"]["tiktok"]["reports"] == 2


def test_a_comparison_across_definitions_is_refused(resolver) -> None:
    landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    build(resolver)

    with Warehouse(resolver) as w:
        rows = analytics.compare_countries(w, provider="tiktok", metric="requests_received")
        assert {r["country_code"] for r in rows} == {"ID", "MY"}
        with pytest.raises(analytics.IncomparableSelection):
            analytics.compare_countries(w, provider="tiktok", metric="items_actioned")


def test_a_rate_is_computed_only_from_figures_that_share_a_footing(resolver) -> None:
    landed(resolver, TikTokGovernmentRemovals.meta, tiktok_csv(), "2025h2_grfcr_english.csv")
    build(resolver)

    with Warehouse(resolver) as w:
        rates = [r for r in analytics.restriction_rates(w) if r["provider"] == "tiktok"]
    content = next(r for r in rates if r["rate"].startswith("content"))
    assert content["value"] == "0.3291"  # 1282 / 3895
    assert content["period_start"] == date(2025, 7, 1)
