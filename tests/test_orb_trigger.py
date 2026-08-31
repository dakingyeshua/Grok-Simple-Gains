"""Constitution v1.2 ORB trigger: max(PMH, ORH) + 5% upper-wick rule.

Cover CDT and CST so the 8:45 opening-range end stays the NYSE conversion,
not a hardcoded Chicago hour. Premarket high includes wicks.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from simple_gains.clock import opening_range_end, regular_open
from simple_gains.config import CONFIRM_UPPER_WICK_MAX
from simple_gains.lanes.scout import (
    confirmation_closes_above_level,
    orb_trigger_level,
    premarket_high_from_bars,
    upper_wick_fraction,
)
from simple_gains.models import Candle

CST_DAY = date(2026, 1, 13)
CDT_DAY = date(2026, 8, 31)


def _bar(ts: datetime, o, h, l, c, vol: int = 100_000) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=vol,
    )


def _pm_bar(session: date, high: Decimal) -> Candle:
    """One premarket 5-minute bar whose high (wick included) is `high`."""
    ts = regular_open(session) - timedelta(minutes=30)
    low = high - Decimal("2")
    close = low + Decimal("0.50")
    return _bar(ts, close, high, low, close)


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_trigger_level_is_max_of_pmh_and_orh(session: date) -> None:
    orh = Decimal("185")
    assert orb_trigger_level(Decimal("190"), orh) == Decimal("190")  # PMH > ORH
    assert orb_trigger_level(Decimal("180"), orh) == Decimal("185")  # ORH > PMH
    assert orb_trigger_level(None, orh) == orh
    assert opening_range_end(session).hour == 8
    assert opening_range_end(session).minute == 45


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_premarket_high_includes_wicks(session: date) -> None:
    body = _pm_bar(session, high=Decimal("188"))
    wicky = body.model_copy(update={"high": Decimal("192"), "close": Decimal("187")})
    or_open = regular_open(session)
    rth = _bar(or_open, 184, 185, 184, 184.5)
    pmh = premarket_high_from_bars([wicky, rth], session)
    assert pmh == Decimal("192")
    assert wicky.close < pmh


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_close_above_level_passes_wick_through_fails(session: date) -> None:
    or_end = opening_range_end(session)
    level = Decimal("185")
    close_above = _bar(or_end, 185, Decimal("185.40"), Decimal("184.90"), Decimal("185.40"))
    assert close_above.close > level
    assert confirmation_closes_above_level(close_above, level)

    wick_through = _bar(or_end, 184.8, Decimal("186"), Decimal("184.50"), Decimal("184.90"))
    assert wick_through.high > level
    assert wick_through.close <= level
    assert not confirmation_closes_above_level(wick_through, level)


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_pmh_above_orh_requires_close_above_pmh(session: date) -> None:
    or_end = opening_range_end(session)
    orh = Decimal("185")
    pmh = Decimal("190")
    level = orb_trigger_level(pmh, orh)
    assert level == pmh
    between = _bar(or_end, 185.2, Decimal("186"), Decimal("185"), Decimal("185.80"))
    assert between.close > orh
    assert not confirmation_closes_above_level(between, level)
    above_pmh = _bar(or_end, 190, Decimal("190.40"), Decimal("189.80"), Decimal("190.40"))
    assert confirmation_closes_above_level(above_pmh, level)


@pytest.mark.parametrize("session", [CST_DAY, CDT_DAY], ids=["CST", "CDT"])
def test_orh_above_pmh_requires_close_above_orh(session: date) -> None:
    or_end = opening_range_end(session)
    orh = Decimal("185")
    pmh = Decimal("180")
    level = orb_trigger_level(pmh, orh)
    assert level == orh
    above_pmh_only = _bar(or_end, 181, Decimal("182"), Decimal("180.50"), Decimal("181.50"))
    assert above_pmh_only.close > pmh
    assert not confirmation_closes_above_level(above_pmh_only, level)
    above_orh = _bar(or_end, 185, Decimal("185.50"), Decimal("184.90"), Decimal("185.50"))
    assert confirmation_closes_above_level(above_orh, level)


def test_upper_wick_5_percent_pass_and_5_1_percent_fail() -> None:
    assert CONFIRM_UPPER_WICK_MAX == Decimal("0.05")
    ts = opening_range_end(CDT_DAY)
    # range = 100; 5.0% upper wick → high-close = 5, close = 195.
    pass_bar = _bar(ts, 150, 200, 100, 195)
    assert upper_wick_fraction(pass_bar) == Decimal("0.05")
    assert confirmation_closes_above_level(pass_bar, Decimal("190"))

    fail_bar = _bar(ts, 150, 200, 100, Decimal("194.9"))
    assert upper_wick_fraction(fail_bar) == Decimal("0.051")
    assert fail_bar.close > Decimal("190")
    assert not confirmation_closes_above_level(fail_bar, Decimal("190"))


def test_doji_high_equals_low_fails_even_if_close_above_level() -> None:
    ts = opening_range_end(CDT_DAY)
    doji = _bar(ts, 200, 200, 200, 200)
    assert doji.high == doji.low
    assert doji.close > Decimal("185")
    assert upper_wick_fraction(doji) is None
    assert not confirmation_closes_above_level(doji, Decimal("185"))
