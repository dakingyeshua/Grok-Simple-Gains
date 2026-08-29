from decimal import Decimal

from simple_gains.lanes.risk import next_stop
from simple_gains.models import Position
from tests.conftest import SESSION, chicago


def _pos(fill=Decimal("100"), stop=Decimal("98"), live=None, last=Decimal("100")) -> Position:
    r = fill - stop
    return Position(
        ticker="AAPL",
        session=SESSION,
        shares=100,
        fill_price=fill,
        initial_stop=stop,
        live_stop=live if live is not None else stop,
        theme="mega-tech",
        sector="mega-tech",
        risk_pct=Decimal("0.01"),
        r_value=r,
        opened_at=chicago(10),
        last_price=last,
        stop_stage="entry_candle_low",
    )


def test_stop_starts_at_entry_candle_low():
    pos = _pos()
    stop, stage = next_stop(pos, Decimal("100.50"), prior_day_low=Decimal("97.00"), daily_20_ema=Decimal("96"))
    assert stop == pos.initial_stop
    assert stage == "entry_candle_low"


def test_prior_day_low_handoff_once_above_initial():
    pos = _pos()
    stop, stage = next_stop(pos, Decimal("101"), prior_day_low=Decimal("98.50"), daily_20_ema=None)
    assert stop == Decimal("98.50")
    assert stage == "prior_day_low"


def test_daily_20_ema_handoff_when_extended():
    pos = _pos(last=Decimal("103"))  # +1.5R, not yet +2R
    stop, stage = next_stop(pos, Decimal("103"), prior_day_low=None, daily_20_ema=Decimal("100.50"))
    assert stop == Decimal("100.50")
    assert stage == "daily_20_ema"


def test_plus_2r_then_1r_floor():
    pos = _pos()
    stop, stage = next_stop(pos, Decimal("104.00"), prior_day_low=None, daily_20_ema=None)
    assert stop == Decimal("102.00")  # fill + 1R (R = 2)
    assert stage == "plus_1r_floor"


def test_stop_never_loosens():
    pos = _pos(live=Decimal("101.00"))
    stop, _ = next_stop(pos, Decimal("100.20"), prior_day_low=Decimal("97"), daily_20_ema=Decimal("96"))
    assert stop == Decimal("101.00")
