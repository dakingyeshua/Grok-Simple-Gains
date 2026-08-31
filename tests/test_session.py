from decimal import Decimal
from pathlib import Path

from simple_gains.broker.base import LiveTradingDisabled
from simple_gains.broker.webull_stub import WebullStubBroker
from simple_gains.clock import (
    Clock,
    as_chicago,
    can_enter_new,
    can_place_order,
    entry_cutoff,
    is_premarket,
    opening_range_end,
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


def test_entries_open_after_regular_open_before_1pm():
    assert ENTRY_CUTOFF.hour == 13 and ENTRY_CUTOFF.minute == 0
    assert regular_open(SESSION).hour == 8 and regular_open(SESSION).minute == 30
    assert not can_enter_new(chicago(8, 29))
    assert can_enter_new(regular_open(SESSION))
    assert can_enter_new(chicago(8, 30))
    assert can_enter_new(chicago(10, 59))
    assert can_enter_new(chicago(11, 0))
    assert can_enter_new(chicago(12, 59))
    assert not can_enter_new(entry_cutoff(SESSION))
    assert not can_enter_new(chicago(13, 0))
    assert not can_enter_new(chicago(13, 1))
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


def test_confirmation_ignores_bars_inside_opening_range(store):
    """First ORB-eligible 5-minute close is 8:45 CT, not a bar inside 8:30–8:45."""
    from simple_gains.lanes.scout import opening_range_candle

    eng = _paper_engine(store, 10, 0)
    snap = eng._snapshot("AAPL", SESSION)
    or_bar = opening_range_candle(snap.five_min, SESSION)
    assert or_bar is not None
    confirm = eng._confirmation(snap, or_bar.high)
    assert confirm is not None
    assert confirm.ts == opening_range_end(SESSION)
    assert confirm.ts.hour == 8 and confirm.ts.minute == 45
    for bar in snap.five_min:
        if as_chicago(bar.ts) < opening_range_end(SESSION):
            assert bar.ts != confirm.ts


def test_five_minute_confirmation_at_1059_cdt_can_still_paper_fill(store):
    """A 5-minute confirmation at 10:59 CDT can still entry (inside v1.2 window)."""
    eng = _paper_engine(store, 10, 59, confirm_hour=10, confirm_minute=59)
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"
    assert any(p.ticker == "AAPL" and not p.closed for p in eng.broker.positions())
    assert "fill" in eng.store.journal_kinds_for(SESSION, "AAPL")
    fill = next(e for e in eng.store.journal(session=SESSION) if e.kind == JournalKind.FILL)
    assert fill.ts == chicago(10, 59)


def test_five_minute_confirmation_at_1259_cdt_can_still_paper_fill(store):
    """Constitution v1.2: a 5-minute confirmation at 12:59 CDT can still entry.

    Hunt cutoff is 12:45, so scan first while hunt is open, then evaluate at 12:59.
    """
    eng = _paper_engine(store, 10, 0, confirm_hour=12, confirm_minute=59)
    eng.scan(session=SESSION)
    eng.clock.freeze(chicago(12, 59))
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"
    assert any(p.ticker == "AAPL" and not p.closed for p in eng.broker.positions())
    fill = next(e for e in eng.store.journal(session=SESSION) if e.kind == JournalKind.FILL)
    assert fill.ts == chicago(12, 59)


def test_1100_cdt_can_still_take_a_new_paper_fill(store):
    """v1.2 moved cutoff to 13:00 — 11:00 CDT is still an open entry window."""
    eng = _paper_engine(store, 11, 0, confirm_hour=10, confirm_minute=59)
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"


def test_1300_cdt_and_later_cannot_take_a_new_paper_fill(store):
    """13:00 CDT and later: Scout window closed; no new paper fill."""
    at_cutoff = _paper_engine(store, 10, 0, confirm_hour=12, confirm_minute=59)
    at_cutoff.scan(session=SESSION)
    at_cutoff.clock.freeze(chicago(13, 0))
    out_1300 = at_cutoff.evaluate_ticker("AAPL", SESSION)
    assert out_1300["decision"] == "scout_fail"
    assert not at_cutoff.broker.positions()
    assert "fill" not in at_cutoff.store.journal_kinds_for(SESSION, "AAPL")

    later = _paper_engine(store, 10, 0)
    later.scan(session=SESSION)
    later.clock.freeze(chicago(13, 30))
    out_later = later.evaluate_ticker("AAPL", SESSION)
    assert out_later["decision"] != "filled"
    assert not later.broker.positions()
    assert "fill" not in later.store.journal_kinds_for(SESSION, "AAPL")


def test_open_positions_still_trail_after_1300_cdt(store):
    """After 13:00 CDT, manage-only: open paper positions still trail."""
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
    eng.clock.freeze(chicago(13, 30))
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
        if e.kind == JournalKind.FILL and e.ts >= chicago(13, 0)
    ]
    assert after_cutoff_fills == []


def test_hunt_cutoff_freezes_watchlist_but_entries_remain_open(store):
    eng = _paper_engine(store, 10, 0)
    first = eng.scan(session=SESSION)
    names = {r["ticker"] for r in first}
    assert "AAPL" in names
    assert "MSFT" in names  # fixture top_gainers source
    eng.clock.freeze(chicago(12, 45))
    frozen = eng.scan(session=SESSION, tickers=["ZZZZ"])
    assert {r["ticker"] for r in frozen} == names
    assert "ZZZZ" not in {r["ticker"] for r in frozen}

    # 12:50 is after hunt cutoff and before entry cutoff — existing names can still fill.
    eng.clock.freeze(chicago(12, 50))
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"


def test_scan_after_hunt_cutoff_without_prior_watchlist_stays_empty(store):
    eng = _paper_engine(store, 12, 45)
    assert eng.scan(session=SESSION) == []


def test_scan_accepts_top_gainers_as_hunt_source(store):
    eng = _paper_engine(store, 10, 0)
    watch = eng.scan(
        session=SESSION,
        sources={
            "most_active": ["AAPL"],
            "unusual_volume": ["NVDA"],
            "top_gainers": ["MSFT"],
            "unusual_options": ["TSLA"],
        },
    )
    tickers = {r["ticker"] for r in watch}
    assert "MSFT" in tickers
    assert "AAPL" in tickers
    assert "NVDA" in tickers
    assert "TSLA" not in tickers  # unusual options is catalyst-only
    msft = next(r for r in watch if r["ticker"] == "MSFT")
    assert "top_gainers" in msft["note"]


def test_engine_confirmation_uses_max_pmh_orh(store):
    """Close above ORH but below a higher PMH is not a trigger."""
    eng = _paper_engine(store, 10, 0)
    eng.data.raw["names"]["AAPL"]["premarket_high"] = "190.00"
    snap = eng._snapshot("AAPL", SESSION)
    from simple_gains.lanes.scout import opening_range_candle, orb_trigger_level, resolve_premarket_high

    or_bar = opening_range_candle(snap.five_min, SESSION)
    assert or_bar is not None
    pmh = resolve_premarket_high(snap)
    level = orb_trigger_level(pmh, or_bar.high)
    assert pmh == Decimal("190.00")
    assert or_bar.high < level
    confirm = eng._confirmation(snap, level)
    assert confirm is None
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "waiting_trigger"


def test_engine_fills_when_close_above_higher_pmh(store):
    """PMH slightly above ORH: fixture 5-minute close 185.60 still confirms."""
    eng = _paper_engine(store, 10, 0)
    eng.data.raw["names"]["AAPL"]["premarket_high"] = "185.20"
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"
