from datetime import timedelta
from decimal import Decimal

from simple_gains.clock import Clock
from simple_gains.config import THEME_NAME_CAP
from simple_gains.engine import Engine, build_broker
from simple_gains.lanes.grader import Grader
from simple_gains.lanes.risk import RiskOfficer
from simple_gains.lanes.scout import Scout
from simple_gains.models import (
    AccountState,
    BreakerState,
    Decision,
    Fill,
    JournalKind,
    Position,
)
from tests.conftest import SESSION, chicago, make_card, make_snap, orb_five_min


def test_risk_veto_is_final_same_day(store):
    officer = RiskOfficer()
    grader = Grader()
    snap = make_snap(spy_ret=Decimal("-0.008"), spy_red=True)
    five = orb_five_min()
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=five[-1]
    )
    card = grader.score(snap, verdict)
    if card.decision == Decision.SKIP:
        # force a passing card so we can test the tape veto
        card = make_card(total=90)
        grader.validate_card(card)
    acct = AccountState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        starting_equity=Decimal("100000"),
        high_water=Decimal("100000"),
        mode="paper",
    )
    first = officer.review(card, verdict, snap, acct, BreakerState(), [], already_vetoed=None)
    assert first.veto and first.veto_reason == "violent_against_tape"
    store.add_veto(SESSION, card.ticker, first.veto_reason, chicago(10))
    # Better grade cannot lift a veto the same day.
    better = make_card(total=90)
    better.ticker = card.ticker
    second = officer.review(
        better, verdict, snap, acct, BreakerState(), [], already_vetoed=store.is_vetoed(SESSION, card.ticker)
    )
    assert second.veto
    assert second.veto_reason.startswith("veto_stands")


def test_theme_cap_is_skip_not_resize():
    officer = RiskOfficer()
    card = make_card(theme="semi")
    snap = make_snap(theme="semi")
    five = orb_five_min()
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=2, already_open_ticker=False, confirmation=five[-1]
    )
    verdict.confirmation = five[-1]
    verdict.passed = True
    book = []
    for t in ("NVDA", "AMD"):
        book.append(
            Position(
                ticker=t,
                session=SESSION,
                shares=10,
                fill_price=Decimal("100"),
                initial_stop=Decimal("99"),
                live_stop=Decimal("99"),
                theme="semi",
                sector="semi",
                risk_pct=Decimal("0.01"),
                r_value=Decimal("1"),
                opened_at=chicago(9, 50),
            )
        )
    assert len(book) == THEME_NAME_CAP
    acct = AccountState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        starting_equity=Decimal("100000"),
        high_water=Decimal("100000"),
        mode="paper",
    )
    decision = officer.review(card, verdict, snap, acct, BreakerState(), book, already_vetoed=None)
    assert not decision.accepted
    assert not decision.veto
    assert decision.skip_reason.startswith("theme_cap")
    assert decision.shares == 0


def test_book_cap_skips_when_6pct_full():
    officer = RiskOfficer()
    card = make_card()
    snap = make_snap()
    five = orb_five_min()
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=1, already_open_ticker=False, confirmation=five[-1]
    )
    verdict.confirmation = five[-1]
    heavy = Position(
        ticker="META",
        session=SESSION,
        shares=600,
        fill_price=Decimal("100"),
        initial_stop=Decimal("90"),
        live_stop=Decimal("90"),
        theme="other",
        sector="other",
        risk_pct=Decimal("0.02"),
        r_value=Decimal("10"),
        opened_at=chicago(9, 50),
        last_price=Decimal("100"),
    )
    acct = AccountState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        starting_equity=Decimal("100000"),
        high_water=Decimal("100000"),
        mode="paper",
    )
    decision = officer.review(card, verdict, snap, acct, BreakerState(), [heavy], already_vetoed=None)
    assert not decision.accepted
    assert decision.skip_reason == "book_risk_cap_6pct"


def test_default_paper_book_starts_at_1000(store):
    broker = build_broker(store, "paper")
    acct = store.ensure_account()
    assert acct.starting_equity == Decimal("1000")
    assert acct.equity == Decimal("1000")
    assert acct.cash == Decimal("1000")
    assert broker.equity() == Decimal("1000")


def test_paper_fill_marks_equity_not_notional(store):
    from pathlib import Path

    from simple_gains.data.fixtures import FixtureData

    broker = build_broker(store, "paper")
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    clock = Clock()
    clock.freeze(chicago(10, 0))
    eng = Engine(store, broker, data, clock)
    before = broker.equity()
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "filled"
    after = broker.equity()
    # Long at 185.60, last 186.50 — small winner, not a 25% hole.
    assert after >= before
    assert after > Decimal("990")


def test_fixture_session_fills_aapl_and_journals(engine):
    result = engine.run_session(SESSION)
    ev = {e["ticker"]: e for e in result["evaluations"]}
    assert ev["AAPL"]["decision"] == "filled"
    assert ev["NVDA"]["decision"] == "scout_fail"
    kinds = engine.store.journal_kinds_for(SESSION, "AAPL")
    assert "signal" in kinds and "fill" in kinds
    fill_events = [e for e in engine.store.journal(session=SESSION) if e.kind == JournalKind.FILL]
    assert fill_events[0].payload["card"]["buckets"]
    assert any(p.ticker == "AAPL" for p in engine.broker.positions())


def test_engine_stop_out_completes_journal(engine):
    engine.scan(session=SESSION)
    engine.evaluate_ticker("AAPL", SESSION)
    pos = next(p for p in engine.broker.positions() if p.ticker == "AAPL")
    # Force the last 5-minute bar through the live stop and re-manage.
    snap = engine._snapshot("AAPL", SESSION)
    low = pos.live_stop - Decimal("0.20")
    snap.five_min[-1] = snap.five_min[-1].model_copy(update={"low": low, "close": low})
    engine.data.raw["names"]["AAPL"]["five_min"][-1]["low"] = str(low)
    engine.data.raw["names"]["AAPL"]["five_min"][-1]["close"] = str(low)
    engine.data.raw["names"]["AAPL"]["quote"]["last"] = str(low)
    engine.manage_open(SESSION)
    kinds = engine.store.journal_kinds_for(SESSION, "AAPL")
    assert "fill" in kinds and "stop" in kinds
    stop = next(e for e in engine.store.journal(session=SESSION) if e.kind == JournalKind.STOP)
    assert {"shares", "price", "reason", "realized_pnl", "r_value"} <= set(stop.payload)


def test_hitl_alerts_instead_of_fill(store):
    from simple_gains.data.fixtures import FixtureData
    from pathlib import Path

    broker = build_broker(store, "hitl")
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    clock = Clock()
    clock.freeze(chicago(10, 0))
    eng = Engine(store, broker, data, clock)
    eng.scan(session=SESSION)
    out = eng.evaluate_ticker("AAPL", SESSION)
    assert out["decision"] == "hitl_alert"
    assert store.alerts()
    assert not broker.positions()


def test_post_fill_cut_on_worse_fill():
    officer = RiskOfficer()
    decision = type("D", (), {})()
    from simple_gains.models import RiskDecision

    rd = RiskDecision(
        accepted=True,
        shares=4000,
        planned_entry=Decimal("100"),
        planned_stop=Decimal("98"),
        planned_risk_dollars=Decimal("2000"),
        planned_risk_pct=Decimal("0.02"),
        r_value=Decimal("2"),
    )
    fill = Fill(
        ticker="AAPL",
        side="buy",
        shares=4000,
        price=Decimal("101"),
        ts=chicago(10),
        session=SESSION,
    )
    cut = officer.apply_fill_cut(rd, fill, Decimal("100000"))
    assert cut.cut_reason == "post_fill_2pct_ceiling"
    assert cut.shares == 666  # 2000 / 3
    assert (fill.price - rd.planned_stop) * Decimal(cut.shares) <= Decimal("2000")
