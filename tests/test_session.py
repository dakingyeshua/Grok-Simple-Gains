from decimal import Decimal
from pathlib import Path

from simple_gains.broker.base import LiveTradingDisabled
from simple_gains.broker.webull_stub import WebullStubBroker
from simple_gains.clock import (
    Clock,
    can_enter_new,
    can_place_order,
    entry_cutoff,
    is_premarket,
    premarket_scan_start,
    regular_open,
)
from simple_gains.config import ENTRY_CUTOFF
from simple_gains.data.fixtures import FixtureData
from simple_gains.engine import Engine, build_broker
from simple_gains.models import JournalKind, OrderTicket
from tests.conftest import SESSION, chicago


def test_premarket_is_scan_only_never_orders():
    ts = premarket_scan_start(SESSION).replace(hour=4, minute=15)
    assert is_premarket(ts)
    assert not can_place_order(ts)
    assert not can_enter_new(ts)


def test_entries_open_after_regular_open_before_11am():
    assert ENTRY_CUTOFF.hour == 11 and ENTRY_CUTOFF.minute == 0
    assert can_enter_new(regular_open(SESSION))
    assert can_enter_new(chicago(10, 59))
    assert not can_enter_new(entry_cutoff(SESSION))
    assert not can_enter_new(chicago(11, 0))
    assert not can_enter_new(chicago(11, 1))
    assert not can_enter_new(chicago(13, 59))
    assert not can_enter_new(chicago(14, 30))


def test_webull_stub_refuses_orders():
    stub = WebullStubBroker()
    ticket = OrderTicket(
        ticker="AAPL",
        side="buy",
        shares=1,
        session=SESSION,
        intended_price=Decimal("100"),
        stop=Decimal("99"),
        theme="mega-tech",
        sector="mega-tech",
        risk_pct=Decimal("0.01"),
        grader_total=90,
        tier="A+",
    )
    try:
        stub.place_market_buy(ticket, chicago(10), Decimal("100"))
        raise AssertionError("stub must refuse")
    except LiveTradingDisabled as exc:
        assert "refuses" in str(exc).lower() or "not enabled" in str(exc).lower()


def _paper_engine(store, hour: int, minute: int, *, confirm_hour: int | None = None, confirm_minute: int = 0) -> Engine:
    broker = build_broker(store, "paper")
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    if confirm_hour is not None:
        data.raw["names"]["AAPL"]["five_min"][3]["ts"] = chicago(confirm_hour, confirm_minute).isoformat()
    clock = Clock()
    clock.freeze(chicago(hour, minute))
    return Engine(store, broker, data, clock)


def test_five_minute_confirmation_at_1059_cdt_can_still_paper_fill(store):
    """Constitution v1.1: a 5-minute confirmation at 10:59 CDT can still entry."""
    eng = _paper_engine(store, 10, 59, confirm_hour=10, confirm_minute=59)
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"
    assert any(p.ticker == "AAPL" and not p.closed for p in eng.broker.positions())
    assert "fill" in eng.store.journal_kinds_for(SESSION, "AAPL")
    fill = next(e for e in eng.store.journal(session=SESSION) if e.kind == JournalKind.FILL)
    assert fill.ts == chicago(10, 59)


def test_1100_cdt_and_later_cannot_take_a_new_paper_fill(store):
    """11:00 CDT and later: Scout window closed; no new paper fill."""
    at_cutoff = _paper_engine(store, 11, 0, confirm_hour=10, confirm_minute=59)
    at_cutoff.scan(session=SESSION)
    out_1100 = at_cutoff.evaluate_ticker("AAPL", SESSION)
    assert out_1100["decision"] == "scout_fail"
    assert not at_cutoff.broker.positions()
    assert "fill" not in at_cutoff.store.journal_kinds_for(SESSION, "AAPL")

    later = _paper_engine(store, 11, 30)
    later.scan(session=SESSION)
    out_later = later.evaluate_ticker("AAPL", SESSION)
    assert out_later["decision"] != "filled"
    assert not later.broker.positions()
    assert "fill" not in later.store.journal_kinds_for(SESSION, "AAPL")


def test_open_positions_still_trail_after_1100_cdt(store):
    """After 11:00 CDT, manage-only: open paper positions still trail."""
    eng = _paper_engine(store, 10, 0)
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"
    pos = next(p for p in eng.broker.positions() if p.ticker == "AAPL")
    initial_stop = pos.live_stop
    last = pos.fill_price + (pos.r_value * Decimal("2.1"))
    quote = eng.data.raw["names"]["AAPL"]["quote"]
    quote["last"] = str(last)
    quote["bid"] = str(last - Decimal("0.02"))
    quote["ask"] = str(last + Decimal("0.02"))
    bar = eng.data.raw["names"]["AAPL"]["five_min"][-1]
    bar["low"] = str(last - Decimal("0.10"))
    bar["high"] = str(last + Decimal("0.10"))
    bar["close"] = str(last)
    eng.clock.freeze(chicago(11, 30))
    eng.manage_open(SESSION)
    pos = next(p for p in eng.broker.positions() if p.ticker == "AAPL")
    assert not pos.closed
    assert pos.live_stop > initial_stop
    assert pos.stop_stage == "plus_1r_floor"
    kinds = eng.store.journal_kinds_for(SESSION, "AAPL")
    assert "fill" in kinds
    assert "trail_handoff" in kinds
    after_cutoff_fills = [
        e for e in eng.store.journal(session=SESSION)
        if e.kind == JournalKind.FILL and e.ts >= chicago(11, 0)
    ]
    assert after_cutoff_fills == []
