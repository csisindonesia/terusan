"""Reading the cron a source declares.

Every registry record may carry a `schedule`, and until now nothing read it:
one agent fired at 05:00 and ran a list of slugs somebody had typed. The list
went stale the moment a source was added — which is how thirteen of fifteen
daily sources came to be collected only when a person remembered them.

So the schedule becomes the instruction rather than the documentation. An agent
runs every hour, asks which sources are due, and runs those. IHSG goes at 18:00
after the Jakarta close because its record says so, the commodities at 22:00
after New York, BMKG's earthquakes every hour, and adding a source with a cron
needs no second edit anywhere.

This is a reader for the five-field cron those records use — minute, hour, day
of month, month, day of week — and nothing more. No seconds field, no `@daily`,
no `L` or `#`: a dialect nobody writes here is a dialect that cannot be wrong,
and an expression outside it raises rather than matching silently.

Times are whatever the caller passes, and the agent passes the machine's local
wall clock — the same reading cron and launchd give a schedule. `0 18 * * 1-5`
is 18:00 where the server stands, which for these records is Jakarta, after the
close at 16:00. Handing this UTC would put that at one in the morning.

Due is asked over a window, not at an instant. An agent fires a few seconds
late, a laptop wakes at 09:03 into the 09:00 it slept through, and a matcher
that demanded the exact minute would skip the run and say nothing. Asking
"did a firing time fall in the last hour" makes a late agent harmless and a
missed hour visible — it is caught by the next one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: Lower and upper bound of each field, in the order cron writes them.
BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),  # day of week, where both 0 and 7 are Sunday
)

#: How far back `due` looks by default: one agent interval.
DEFAULT_WINDOW = timedelta(hours=1)


class BadSchedule(ValueError):
    """A cron expression this reader does not accept.

    Raised rather than treated as never-due: a source whose schedule cannot be
    read would otherwise go uncollected in silence, which is the failure this
    module exists to end.
    """


@dataclass(frozen=True, slots=True)
class Cron:
    """One parsed expression."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]

    #: Whether the day-of-month and day-of-week fields were written as `*`.
    #: Cron's oddest rule needs to know: when both name specific days the two
    #: are OR-ed, so `0 0 1 * 1` is the first of the month *and* every Monday.
    #: When only one is specific, that one alone decides.
    day_is_any: bool
    weekday_is_any: bool

    def matches(self, when: datetime) -> bool:
        """Whether a firing falls on this exact minute."""
        if when.minute not in self.minutes:
            return False
        if when.hour not in self.hours:
            return False
        if when.month not in self.months:
            return False

        # Python counts Monday as 0 and Sunday as 6; cron counts Sunday as 0.
        weekday = (when.weekday() + 1) % 7

        if self.day_is_any and self.weekday_is_any:
            return True
        if self.day_is_any:
            return weekday in self.weekdays
        if self.weekday_is_any:
            return when.day in self.days
        return when.day in self.days or weekday in self.weekdays


def _field(spec: str, low: int, high: int) -> frozenset[int]:
    """One comma-separated field: `*`, `5`, `1-5`, `*/15`, `0-30/10`."""
    values: set[int] = set()

    for part in spec.split(","):
        term = part.strip()
        if not term:
            raise BadSchedule(f"empty term in {spec!r}")

        step = 1
        if "/" in term:
            term, _, step_text = term.partition("/")
            try:
                step = int(step_text)
            except ValueError:
                raise BadSchedule(f"step {step_text!r} is not a number") from None
            if step < 1:
                raise BadSchedule(f"step {step} is not positive")

        if term == "*":
            start, end = low, high
        elif "-" in term:
            start_text, _, end_text = term.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                raise BadSchedule(f"range {term!r} is not two numbers") from None
        else:
            try:
                start = end = int(term)
            except ValueError:
                raise BadSchedule(f"{term!r} is not a number") from None

        if start < low or end > high or start > end:
            raise BadSchedule(f"{term!r} is outside {low}-{high}")

        values.update(range(start, end + 1, step))

    return frozenset(values)


def parse_cron(expression: str) -> Cron:
    """Read a five-field cron expression."""
    fields = expression.split()
    if len(fields) != 5:
        raise BadSchedule(
            f"{expression!r} has {len(fields)} fields; five are expected "
            "— minute hour day-of-month month day-of-week"
        )

    minutes, hours, days, months, weekdays = (
        _field(field, low, high) for field, (low, high) in zip(fields, BOUNDS, strict=True)
    )

    # Cron lets Sunday be written either way, and a matcher comparing against
    # Python's calendar has to pick one.
    if 7 in weekdays:
        weekdays = frozenset(weekdays - {7} | {0})

    return Cron(
        minutes=minutes,
        hours=hours,
        days=days,
        months=months,
        weekdays=weekdays,
        day_is_any=fields[2].strip() == "*",
        weekday_is_any=fields[4].strip() == "*",
    )


def due(expression: str, now: datetime, window: timedelta = DEFAULT_WINDOW) -> bool:
    """Whether a firing fell in the window ending at `now`, inclusive.

    The window is walked a minute at a time, which for an hourly agent is sixty
    comparisons against a handful of integer sets — cheaper than parsing the
    expression was.
    """
    if window < timedelta(0):
        raise ValueError("window cannot be negative")

    cron = parse_cron(expression)
    # Seconds and microseconds would make the first candidate a partial minute
    # that can never match.
    moment = now.replace(second=0, microsecond=0)
    earliest = now - window

    while moment > earliest:
        if cron.matches(moment):
            return True
        moment -= timedelta(minutes=1)

    return False
