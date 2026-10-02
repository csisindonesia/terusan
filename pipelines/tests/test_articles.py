"""The article figures: dry seasons against the normal, prices against July."""

from terusan_pipelines.articles import rice_dry_season as article


def _rain(geo_id: str, normal_mm: float, dry_mm: float) -> list[tuple[str, int, int, float]]:
    """Five months a year for the baseline at `normal_mm`, and 2023 and 2019 at `dry_mm`."""
    rows = []
    for year in range(1991, 2021):
        rows += [(geo_id, year, month, normal_mm / 5) for month in range(6, 11)]
    for year in (2019, 2023):
        rows += [(geo_id, year, month, dry_mm / 5) for month in range(6, 11)]
    return rows


def _prices(geo_id: str, july: float, march: float) -> list[tuple[str, str, str, float]]:
    rows = []
    for year in (2019, 2023):
        rows += [
            (article.RICE, geo_id, f"{year}-07", july),
            (article.RICE, geo_id, f"{year + 1}-03", march),
            (article.CHILI, geo_id, f"{year}-07", 100.0),
            (article.CHILI, geo_id, f"{year}-10", 100.0),
        ]
    return rows


def _figures():
    rain = _rain("ID-11", 1000, 500) + _rain("ID-12", 1000, 900) + _rain("ID-13", 1000, 700)
    prices = (
        _prices("ID-11", 10000, 13000)
        + _prices("ID-12", 10000, 10500)
        + _prices("ID-13", 10000, 11500)
    )
    # Papua is left out: its price rows and its rainfall rows name different
    # provinces since the 2022 split.
    rain += _rain("ID-94", 1000, 100)
    prices += _prices("ID-94", 10000, 50000)
    return article.compute(rain, prices, {"ID-11": "Aceh"}, 0.04, "2024-03-31")


def test_the_deficit_is_against_the_baseline_mean() -> None:
    by_geo = {p["geo_id"]: p for p in _figures()["provinces"]}
    assert by_geo["ID-11"]["rain_change"] == -50.0
    assert by_geo["ID-11"]["rice_change"] == 30.0
    assert by_geo["ID-11"]["name"] == "Aceh"


def test_papua_is_left_out_of_the_cross_section() -> None:
    assert "ID-94" not in {p["geo_id"] for p in _figures()["provinces"]}


def test_drier_provinces_rising_more_is_a_negative_rank_correlation() -> None:
    assert _figures()["y2023"]["rho"] == -1.0


def test_a_season_missing_a_month_is_not_a_drought() -> None:
    rain = _rain("ID-11", 1000, 500)
    rain = [row for row in rain if not (row[1] == 2023 and row[2] == 10)]
    figures = article.compute(rain, _prices("ID-11", 10000, 13000), {}, 0.0, "2024-03-31")
    assert figures["provinces"] == []


def test_ties_share_their_mean_rank() -> None:
    assert article._ranks([3.0, 1.0, 3.0]) == [1.5, 0.0, 1.5]
