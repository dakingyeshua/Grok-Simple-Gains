"""America/Chicago session calendar. Premarket is scan-only; entries end at 11:00."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from simple_gains.config import (
    ENTRY_CUTOFF,
    PREMARKET_SCAN_START,
    REGULAR_CLOSE,
    REGULAR_OPEN,
    SESSION_TZ,
)

CHICAGO = ZoneInfo(SESSION_TZ)

# Observed US equity holidays (NYSE) used to skip session days.
# Extend as needed; weekends are always closed.
US_EQUITY_HOLIDAYS = {
    date(2024, 1, 1),
    date(2024, 1, 15),
    date(2024, 2, 19),
    date(2024, 3, 29),
    date(2024, 5, 27),
    date(2024, 6, 19),
    date(2024, 7, 4),
    date(2024, 9, 2),
    date(2024, 11, 28),
    date(2024, 12, 25),
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
}


def as_chicago(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=CHICAGO)
    return ts.astimezone(CHICAGO)


def session_date(ts: datetime) -> date:
    return as_chicago(ts).date()


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_session_day(d: date) -> bool:
    return not is_weekend(d) and d not in US_EQUITY_HOLIDAYS


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=CHICAGO)


def regular_open(d: date) -> datetime:
    return combine(d, REGULAR_OPEN)


def entry_cutoff(d: date) -> datetime:
    return combine(d, ENTRY_CUTOFF)


def regular_close(d: date) -> datetime:
    return combine(d, REGULAR_CLOSE)


def premarket_scan_start(d: date) -> datetime:
    return combine(d, PREMARKET_SCAN_START)


def opening_range_end(d: date) -> datetime:
    return regular_open(d) + timedelta(minutes=15)


def is_premarket(ts: datetime) -> bool:
    local = as_chicago(ts)
    d = local.date()
    return premarket_scan_start(d) <= local < regular_open(d)


def is_regular_session(ts: datetime) -> bool:
    local = as_chicago(ts)
    d = local.date()
    return regular_open(d) <= local < regular_close(d)


def can_enter_new(ts: datetime) -> bool:
    """New paper entries: regular session, before 11:00 America/Chicago."""
    local = as_chicago(ts)
    d = local.date()
    if not is_session_day(d):
        return False
    return regular_open(d) <= local < entry_cutoff(d)


def can_place_order(ts: datetime) -> bool:
    """Any order other than managing an open stop. Premarket never orders."""
    return can_enter_new(ts)


def first_15m_complete(ts: datetime, session: date) -> bool:
    return as_chicago(ts) >= opening_range_end(session)


def prior_session_day(d: date) -> date:
    cursor = d - timedelta(days=1)
    while not is_session_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


@dataclass
class Clock:
    """Injectable clock. Production uses wall time; tests freeze `now`."""

    frozen: datetime | None = None

    def now(self) -> datetime:
        if self.frozen is not None:
            return as_chicago(self.frozen)
        return datetime.now(tz=CHICAGO)

    def freeze(self, ts: datetime) -> None:
        self.frozen = as_chicago(ts)
