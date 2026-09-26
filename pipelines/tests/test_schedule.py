"""Reading the cron a source declares.

The expressions in the registry are the cases that matter — `0 22 * * 1-5`
after the New York close, `7 * * * *` every hour, `0 5 * * 5` on Fridays — so
they are here verbatim rather than paraphrased. The rest guards the two rules
cron watchers get wrong: Sunday is 0 at one end of the week and 7 at the other,
and a day-of-month beside a day-of-week is an OR, not an AND.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from terusan_pipelines.sources.schedule import BadSchedule, due, parse_cron

# -- fields ------------------------------------------------------------------


def test_a_star_is_every_value_in_the_field() -> None:
    cron = parse_cron("* * * * *")

    assert len(cron.minutes) == 60
    assert len(cron.hours) == 24
    assert len(cron.months) == 12


def test_a_range_a_list_and_a_step_are_all_read() -> None:
    cron = parse_cron("0,30 9-17 * * *")

    assert cron.minutes == frozenset({0, 30})
    assert cron.hours == frozenset(range(9, 18))

    assert parse_cron("*/15 * * * *").minutes == frozenset({0, 15, 30, 45})
    assert parse_cron("0-30/10 * * * *").minutes == frozenset({0, 10, 20, 30})


@pytest.mark.parametrize(
    "expression",
    [
        "* * * *",  # four fields
        "* * * * * *",  # six, as though seconds were expected
        "60 * * * *",  # a minute that does not exist
        "* 24 * * *",  # an hour that does not exist
        "* * 0 * *",  # there is no zeroth day of the month
        "@daily",  # a dialect this reader does not accept
        "*/0 * * * *",  # a step of nothing
        "5-1 * * * *",  # a range that runs backwards
    ],
)
def test_an_expression_outside_the_dialect_raises(expression: str) -> None:
    """Rather than never matching. A schedule that cannot be read would leave
    its source uncollected in silence, which is the failure this ends."""
    with pytest.raises(BadSchedule):
        parse_cron(expression)


# -- the calendar ------------------------------------------------------------


def test_sunday_is_accepted_at_both_ends_of_the_week() -> None:
    """Cron writes Sunday as 0 or as 7 and means the same day."""
    assert parse_cron("0 0 * * 0").weekdays == parse_cron("0 0 * * 7").weekdays


def test_a_weekday_range_matches_the_right_days() -> None:
    """`1-5` is Monday to Friday. Python counts Monday as 0 and cron counts
    Sunday as 0, so an off-by-one here would shift every commodity price."""
    # 2026-09-21 is a Monday; the 26th and 27th are the weekend.
    weekdays = [datetime(2026, 9, day, 22, 0) for day in range(21, 28)]
    matched = [d.strftime("%a") for d in weekdays if due("0 22 * * 1-5", d, timedelta(minutes=1))]

    assert matched == ["Mon", "Tue", "Wed", "Thu", "Fri"]


def test_a_day_of_month_beside_a_day_of_week_is_an_or() -> None:
    """Cron's oddest rule: when both name specific days, either one fires."""
    schedule = "0 0 1 * 1"  # the first of the month, and every Monday

    # 2026-09-01 is a Tuesday: the day of the month matches, the weekday not.
    assert due(schedule, datetime(2026, 9, 1, 0, 0), timedelta(minutes=1))
    # 2026-09-07 is a Monday and not the first.
    assert due(schedule, datetime(2026, 9, 7, 0, 0), timedelta(minutes=1))
    # 2026-09-08 is a Tuesday and not the first.
    assert not due(schedule, datetime(2026, 9, 8, 0, 0), timedelta(minutes=1))


def test_a_day_of_month_alone_still_binds() -> None:
    """The OR applies only when both fields are specific."""
    assert due("0 0 1 * *", datetime(2026, 9, 1, 0, 0), timedelta(minutes=1))
    assert not due("0 0 1 * *", datetime(2026, 9, 2, 0, 0), timedelta(minutes=1))


# -- the window --------------------------------------------------------------


def test_an_agent_running_late_still_catches_the_firing() -> None:
    """The reason due is asked over a window. An agent a few minutes behind,
    or a laptop waking at 09:03 into the 09:00 it slept through, would be
    skipped by a matcher that demanded the exact minute — and say nothing."""
    assert due("0 9 * * *", datetime(2026, 9, 23, 9, 3), timedelta(hours=1))
    assert due("0 9 * * *", datetime(2026, 9, 23, 9, 59), timedelta(hours=1))


def test_the_hour_after_is_not_the_hour_of() -> None:
    """A window wide enough to forgive a late agent must still be narrow enough
    to not run yesterday's schedule again."""
    assert not due("0 9 * * *", datetime(2026, 9, 23, 10, 1), timedelta(hours=1))


def test_seconds_do_not_shift_the_minute_a_firing_falls_in() -> None:
    """An agent fires at 09:00:04, and a candidate minute carrying those four
    seconds matches nothing."""
    assert due("0 9 * * *", datetime(2026, 9, 23, 9, 0, 4), timedelta(minutes=1))


def test_hourly_fires_once_an_hour_and_not_more() -> None:
    """BMKG's earthquakes: `7 * * * *`, which an hourly agent must pick up
    exactly once however the minutes line up."""
    hits = [
        minute
        for minute in range(60)
        if due("7 * * * *", datetime(2026, 9, 23, 14, minute), timedelta(minutes=1))
    ]

    assert hits == [7]


def test_a_negative_window_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        due("* * * * *", datetime(2026, 9, 23, 9, 0), timedelta(minutes=-1))


# -- against the registry ----------------------------------------------------


def test_every_schedule_in_the_registry_can_be_read() -> None:
    """The reader's real contract. A record whose cron this cannot parse is a
    source that would never be collected once the agent trusts the field."""
    from terusan_pipelines.sources import registry

    registry.ensure_loaded()
    unreadable = []
    for source in registry.all():
        if not source.meta.schedule:
            continue
        try:
            parse_cron(source.meta.schedule)
        except BadSchedule as exc:
            unreadable.append((source.meta.slug, source.meta.schedule, str(exc)))

    assert unreadable == []


def test_the_registry_answers_which_sources_are_due() -> None:
    from terusan_pipelines.sources import registry

    registry.ensure_loaded()
    # 22:00 on a Friday, which is when the commodity futures are collected.
    slugs = {s.meta.slug for s in registry.due(datetime(2026, 9, 25, 22, 0), timedelta(minutes=1))}

    assert "yahoo-brent-crude" in slugs
    assert "yahoo-exchange-rates" not in slugs  # 23:00, an hour later


def test_no_scheduled_source_is_one_that_cannot_run() -> None:
    """An active source with a schedule will now be attempted by the agent, and
    `sources run` exits non-zero when any source fails — which under `set -e`
    takes the rest of that hour's collection down with it. SP2KP was exactly
    this: active, scheduled for 08:00, and unable to start without Playwright,
    so it would have failed every morning and stopped BMKG and GDELT being
    extracted. A source that cannot run belongs inactive until it can."""
    from terusan_pipelines.sources import registry

    registry.ensure_loaded()

    unrunnable = []
    for source in registry.scheduled():
        available = getattr(source, "available", None)
        if callable(available) and not available():
            unrunnable.append(source.meta.slug)

    assert unrunnable == []
