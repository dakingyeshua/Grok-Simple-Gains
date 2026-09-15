"""Single-score path: desk card is authoritative; engine must not invent a competing grade."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from simple_gains.clock import Clock
from simple_gains.data.base import MarketData
from simple_gains.data.finnhub import FinnhubData
from simple_gains.engine import Engine, build_broker
from simple_gains.lanes.grader import Grader, capture_contract, cards_equivalent
from simple_gains.lanes.scout import Scout
from simple_gains.models import (
    CARD_SOURCE_DESK,
    BucketScores,
    Decision,
    DeskTicket,
    JournalKind,
    OrderTicket,
    PathSplitError,
    TicketStatus,
)
from tests.conftest import SESSION, chicago, daily_series, make_snap, orb_five_min

BE_SESSION = SESSION  # same calendar as fixture clock / ORB bars


def _desk_be_buckets() -> BucketScores:
    # 2026-09-08 desk Grader: Level 18 / RS 20 / Vol 18 / Cat 12 / EMA 10 / OR 8 = 86.
    # v1.4 map (2026-09-15): 85–94 is A+ 1.5%. Desk score stays 86; engine aligns the tier.
    return BucketScores(
        level_pattern=18,
        rs_vs_spy=20,
        volume=18,
        catalyst=12,
        daily_20_ema=10,
        opening_range_quality=8,
    )


def desk_be_card():
    return Grader().card_from_buckets(
        ticker="BE",
        date=BE_SESSION,
        buckets=_desk_be_buckets(),
        pre_filter_pass_list=[
            "on_watchlist",
            "regular_session_before_cutoff",
            "first_15m_complete",
            "liquid_enough",
            "not_halted",
            "cluster_slot_open",
            "not_a_chase",
        ],
        theme="energy",
        sector="energy",
        spy_qqq_headwind_note="SPY session 0.10%",
        notes="desk Grader after 9:50 ET yfinance confirm",
        source=CARD_SOURCE_DESK,
    )


def desk_be_ticket(shares: int = 1) -> DeskTicket:
    card = desk_be_card()
    return DeskTicket(
        ticker="BE",
        session=BE_SESSION,
        status=TicketStatus.ARMED,
        ticket=OrderTicket(
            ticker="BE",
            side="buy",
            shares=shares,
            session=BE_SESSION,
            intended_price=Decimal("190"),
            stop=Decimal("188"),
            theme=card.theme,
            sector=card.sector,
            risk_pct=card.mapped_risk_pct,
            grader_total=card.total,
            tier=card.tier,
            reason="desk_armed",
        ),
        source=CARD_SOURCE_DESK,
        note="Risk SIZE 1 — ARMED",
    )


def thin_be_snap():
    """Cold live snapshot missing Scout pattern + tight OR context.

    Reproduces the incident mechanical card: Level 0 / RS 20 / Vol 20 / Cat 8 / EMA 3 / OR 1 = 52 skip.
    """
    five = orb_five_min()
    five[-1] = five[-1].model_copy(update={"volume": 2_000_000})
    return make_snap(
        ticker="BE",
        last=Decimal("190"),
        pattern="",
        level="",
        catalyst=True,
        catalyst_note="in the news",
        ema=Decimal("160"),
        adr=Decimal("1.00"),
        spy_ret=Decimal("0"),
        qqq_ret=Decimal("0"),
        five=five,
        daily=daily_series(Decimal("190")),
        theme="energy",
    )


class ThinBEData(MarketData):
    """Live-like feed for BE: no pattern_hint / level_note (Finnhub snapshot shape)."""

    def __init__(self, snap=None) -> None:
        self._snap = snap or thin_be_snap()

    def snapshot(self, ticker: str, session: date, on_watchlist: bool):
        snap = self._snap.model_copy(update={"on_watchlist": on_watchlist, "session": session})
        snap.ticker = ticker.upper()
        return snap

    def candles(self, ticker: str, session: date, resolution: str):
        if resolution == "5":
            return self._snap.five_min
        if resolution == "15":
            return self._snap.fifteen_min
        return self._snap.daily

    def quote(self, ticker: str):
        return self._snap.quote

    def profile(self, ticker: str):
        return self._snap.profile

    def index_tape(self, session: date) -> dict[str, object]:
        return {
            "spy_session_ret": self._snap.spy_session_ret,
            "qqq_session_ret": self._snap.qqq_session_ret,
            "spy_last_5m_red": self._snap.spy_last_5m_red,
            "qqq_last_5m_red": self._snap.qqq_last_5m_red,
        }


def _cold_engine(store, data=None) -> Engine:
    broker = build_broker(store, "paper")
    clock = Clock()
    clock.freeze(chicago(10, 0))
    return Engine(store, broker, data or ThinBEData(), clock)


def test_identical_grade_inputs_produce_identical_totals():
    snap = make_snap()
    five = orb_five_min()
    confirm = five[-1]
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=confirm
    )
    assert verdict.passed
    desk = Grader().score(snap, verdict)
    engine = Grader().score(snap, verdict)
    assert desk.buckets.as_dict() == engine.buckets.as_dict()
    assert desk.total == engine.total
    assert desk.tier == engine.tier
    assert desk.decision == engine.decision
    via_contract = Grader().score_contract(
        capture_contract(snap, verdict),
        pre_filter_pass_list=list(verdict.passed_names),
    )
    assert via_contract.total == desk.total
    assert via_contract.buckets.as_dict() == desk.buckets.as_dict()
    assert cards_equivalent(desk, engine)


def test_thin_be_snapshot_mechanical_card_is_the_incident_52():
    snap = thin_be_snap()
    confirm = snap.five_min[-1]
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=confirm
    )
    assert verdict.passed
    card = Grader().score(snap, verdict)
    assert card.buckets.as_dict() == {
        "level_pattern": 0,
        "rs_vs_spy": 20,
        "volume": 20,
        "catalyst": 8,
        "daily_20_ema": 3,
        "opening_range_quality": 1,
    }
    assert card.total == 52
    assert card.decision == Decision.SKIP


def test_desk_be_card_is_86_a_plus():
    card = desk_be_card()
    assert card.total == 86
    assert card.tier == "A+"
    assert card.mapped_risk_pct == Decimal("0.015")
    assert card.decision == Decision.A_PLUS
    assert card.buckets.as_dict() == _desk_be_buckets().as_dict()


def test_shared_inputs_cannot_split_desk_86_vs_engine_52():
    """The 86 vs 52 split is missing inputs, not two formulas. Shared inputs align."""
    full = make_snap(ticker="BE", theme="energy")
    thin = thin_be_snap()
    confirm = full.five_min[-1]
    now = chicago(10)
    full_v = Scout().evaluate(full, now, open_position_count=0, already_open_ticker=False, confirmation=confirm)
    thin_v = Scout().evaluate(thin, now, open_position_count=0, already_open_ticker=False, confirmation=thin.five_min[-1])
    full_card = Grader().score(full, full_v)
    thin_card = Grader().score(thin, thin_v)
    assert thin_card.total == 52
    assert full_card.total != 52
    # Same full inputs through two call sites (desk module vs engine module) match.
    assert Grader().score(full, full_v).total == full_card.total
    assert capture_contract(full, full_v).pattern_hint
    assert not capture_contract(thin, thin_v).pattern_hint


def test_desk_armed_cold_start_does_not_emit_conflicting_skip(store):
    card = desk_be_card()
    ticket = desk_be_ticket()
    writer = _cold_engine(store)
    writer.scan(["BE"], BE_SESSION)
    writer.ingest_desk(card=card, ticket=ticket, contract=None)

    # Cold restart: new Engine, same book, thin BE feed (no pattern / wide OR).
    cold = _cold_engine(store, ThinBEData())
    out = cold.evaluate_ticker("BE", BE_SESSION)
    assert out["decision"] == "desk_armed"
    assert out["card"]["total"] == 86
    assert out["card"]["buckets"]["level_pattern"] == 18
    assert out["ticket"]["status"] == TicketStatus.ARMED.value
    assert out["ticket"]["ticket"]["shares"] == 1
    stored = cold.store.get_card(BE_SESSION, "BE")
    assert stored is not None and stored.total == 86
    armed = cold.store.armed_ticket(BE_SESSION, "BE")
    assert armed is not None and armed.status == TicketStatus.ARMED
    kinds = cold.store.journal_kinds_for(BE_SESSION, "BE")
    assert "skip" not in kinds
    assert JournalKind.PATH_SPLIT.value not in kinds
    skip_events = [e for e in cold.store.journal(session=BE_SESSION) if e.kind == JournalKind.SKIP]
    assert skip_events == []
    assert not cold.broker.positions()  # auto-print stays off


def test_desk_artifact_file_resync_preserves_armed(store, tmp_path, monkeypatch):
    card = desk_be_card()
    ticket = desk_be_ticket()
    art = tmp_path / "BE.json"
    art.write_text(
        __import__("json").dumps(
            {
                "ticker": "BE",
                "session": BE_SESSION.isoformat(),
                "card": card.model_dump(mode="json"),
                "ticket": {**ticket.ticket.model_dump(mode="json"), "status": "ARMED"},
            },
            default=str,
        )
    )
    monkeypatch.setenv("SIMPLE_GAINS_DESK_DIR", str(tmp_path))
    eng = _cold_engine(store)
    eng.scan(["BE"], BE_SESSION)
    out = eng.evaluate_ticker("BE", BE_SESSION)
    assert out["decision"] == "desk_armed"
    assert eng.store.get_card(BE_SESSION, "BE").total == 86
    assert eng.store.armed_ticket(BE_SESSION, "BE").status == TicketStatus.ARMED
    assert "skip" not in eng.store.journal_kinds_for(BE_SESSION, "BE")


def test_path_split_alarm_keeps_desk_and_does_not_cancel_armed(store):
    card = desk_be_card()
    writer = _cold_engine(store)
    writer.ingest_desk(card=card, ticket=desk_be_ticket())
    thin = thin_be_snap()
    confirm = thin.five_min[-1]
    engine_card = Grader().score(
        thin,
        Scout().evaluate(thin, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=confirm),
    )
    assert engine_card.total == 52
    with pytest.raises(PathSplitError, match="PATH SPLIT"):
        Grader().reconcile(card, engine_card)
    out = writer._path_split_result(chicago(10), BE_SESSION, "BE", card, engine_card)
    assert out["decision"] == "path_split"
    assert writer.store.get_card(BE_SESSION, "BE").total == 86
    assert writer.store.armed_ticket(BE_SESSION, "BE").status == TicketStatus.ARMED
    writer.store.save_ticket(
        DeskTicket(
            ticker="BE",
            session=BE_SESSION,
            status=TicketStatus.SKIPPED,
            ticket=desk_be_ticket().ticket,
            source="engine",
            note="engine skip must not land",
        )
    )
    assert writer.store.armed_ticket(BE_SESSION, "BE").status == TicketStatus.ARMED


def test_armed_resync_never_calls_finnhub_stock_candle(store, monkeypatch):
    writer = _cold_engine(store)
    writer.scan(["BE"], BE_SESSION)
    writer.ingest_desk(card=desk_be_card(), ticket=desk_be_ticket())

    class _BoomClient:
        def get(self, url, params=None):
            raise AssertionError(f"Finnhub HTTP must not run on ARMED resync: {url}")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "simple_gains.data.candles.fetch_ohlcv",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no OHLCV on ARMED resync")),
    )
    data = FinnhubData(api_key="test-key", client=_BoomClient())
    cold = _cold_engine(store, data)
    out = cold.evaluate_ticker("BE", BE_SESSION)
    assert out["decision"] == "desk_armed"
    assert out["card"]["total"] == 86
    assert "skip" not in cold.store.journal_kinds_for(BE_SESSION, "BE")


def test_engine_source_guard_still_bans_finnhub_candle():
    root = Path(__file__).resolve().parents[1] / "simple_gains"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if ' _get("/stock/candle"' in text or "_get('/stock/candle'" in text:
            offenders.append(str(path))
        if "finnhub.io" in text and "/stock/candle" in text and "forbidden" not in text.lower():
            offenders.append(str(path))
    assert offenders == []
