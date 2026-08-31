"""Session clock vs locked Risk Constitution v1.1.

NYSE regular hours are 9:30–16:00 America/New_York. Product desk times are
America/Chicago. Cover both CDT (UTC−5) and CST (UTC−6) so a DST flip cannot
silently move 9:30 ET onto 9:30 Chicago.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from simple_gains.clock import (
    CHICAGO,
    NEW_YORK,
    can_enter_new,
    can_place_order,
    entry_cutoff,
    first_15m_complete,
    hunt_cutoff,
    is_premarket,
    is_regular_session,
    opening_range_end,
    regular_close,
    regular_open,
)
from simple_gains.config import (
    ENTRY_CUTOFF,
    HUNT_CUTOFF,
    NYSE_REGULAR_CLOSE,
    NYSE_REGULAR_OPEN,
    OPENING_RANGE_MINUTES,
    REGULAR_CLOSE,
    REGULAR_OPEN,
)

# Tuesday in CST (UTC−6) and CDT (UTC−5). Both are US equity session days.
CST_DAY = date(2026, 1, 13)  # UTC−6
CDT_DAY = date(2026, 8, 31)  # UTC−5 — Monday paper-session date in the bug report
NYSE_OPEN = time(9, 30)
NYSE_CLOSE = time(16, 0)


@pytest.mark.parametrize(
    "session,utc_offset_hours",
    [
        (CST_DAY, -6),
        (CDT_DAY, -5),
    ],
    ids=["CST", "CDT"],
)
def test_regular_open_is_830_chicago_equal_to_930_et(session: date, utc_offset_hours: int) -> None:
    open_ct = regular_open(session)
    ny_open = datetime.combine(session, NYSE_OPEN, tzinfo=NEW_YORK)

    assert open_ct.tzinfo == CHICAGO
    assert open_ct.hour == 8 and open_ct.minute == 30
    assert open_ct.utcoffset() == timedelta(hours=utc_offset_hours)
    assert open_ct == ny_open.astimezone(CHICAGO)
    assert open_ct.astimezone(NEW_YORK).time() == NYSE_REGULAR_OPEN
    assert REGULAR_OPEN == time(8, 30)

    # The original bug: naive 9:30 treated as Chicago instead of Eastern.
    wrong_chicago_open = datetime.combine(session, time(9, 30), tzinfo=CHICAGO)
    assert open_ct != wrong_chicago_open
    assert open_ct == wrong_chicago_open - timedelta(hours=1)


@pytest.mark.parametrize(
    "session,utc_offset_hours",
    [
        (CST_DAY, -6),
        (CDT_DAY, -5),
    ],
    ids=["CST", "CDT"],
)
def test_opening_range_completes_at_845_chicago(session: date, utc_offset_hours: int) -> None:
    or_end = opening_range_end(session)
    ny_or_end = datetime.combine(session, time(9, 45), tzinfo=NEW_YORK)

    assert or_end.hour == 8 and or_end.minute == 45
    assert or_end.utcoffset() == timedelta(hours=utc_offset_hours)
    assert or_end == regular_open(session) + timedelta(minutes=OPENING_RANGE_MINUTES)
    assert or_end == ny_or_end.astimezone(CHICAGO)

    just_before = or_end - timedelta(seconds=1)
    assert not first_15m_complete(just_before, session)
    assert first_15m_complete(or_end, session)

    wrong_chicago_or = datetime.combine(session, time(9, 45), tzinfo=CHICAGO)
    assert or_end != wrong_chicago_or
    assert not first_15m_complete(
        datetime.combine(session, time(8, 44), tzinfo=CHICAGO), session
    )


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_entry_and_hunt_cutoffs_are_chicago_wall_times(session: date) -> None:
    assert ENTRY_CUTOFF == time(11, 0)
    assert HUNT_CUTOFF == time(10, 45)
    assert entry_cutoff(session) == datetime.combine(session, time(11, 0), tzinfo=CHICAGO)
    assert hunt_cutoff(session) == datetime.combine(session, time(10, 45), tzinfo=CHICAGO)
    assert hunt_cutoff(session) < entry_cutoff(session)


@pytest.mark.parametrize(
    "session,utc_offset_hours",
    [
        (CST_DAY, -6),
        (CDT_DAY, -5),
    ],
    ids=["CST", "CDT"],
)
def test_regular_close_is_300_pm_chicago_equal_to_1600_et(session: date, utc_offset_hours: int) -> None:
    close_ct = regular_close(session)
    ny_close = datetime.combine(session, NYSE_CLOSE, tzinfo=NEW_YORK)

    assert close_ct.hour == 15 and close_ct.minute == 0
    assert close_ct.utcoffset() == timedelta(hours=utc_offset_hours)
    assert close_ct == ny_close.astimezone(CHICAGO)
    assert close_ct.astimezone(NEW_YORK).time() == NYSE_REGULAR_CLOSE
    assert REGULAR_CLOSE == time(15, 0)


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_entry_window_and_premarket_around_converted_open(session: date) -> None:
    open_ct = regular_open(session)
    pre = open_ct - timedelta(minutes=1)
    at_cutoff = entry_cutoff(session)

    assert is_premarket(pre)
    assert not can_enter_new(pre)
    assert not can_place_order(pre)

    assert not is_premarket(open_ct)
    assert is_regular_session(open_ct)
    assert can_enter_new(open_ct)
    assert can_place_order(open_ct)

    assert can_enter_new(at_cutoff - timedelta(minutes=1))
    assert not can_enter_new(at_cutoff)
    assert not can_enter_new(at_cutoff + timedelta(minutes=1))

    last_regular = regular_close(session) - timedelta(seconds=1)
    assert is_regular_session(last_regular)
    assert not is_regular_session(regular_close(session))
    assert not can_enter_new(regular_close(session) - timedelta(minutes=1))  # after 11:00
