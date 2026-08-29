"""Session orchestrator. Wires the four lanes; does not score or size itself."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from simple_gains.broker.base import Broker, LiveTradingDisabled
from simple_gains.broker.hitl import HITLBroker
from simple_gains.broker.paper import PaperBroker
from simple_gains.broker.webull_stub import WebullStubBroker
from simple_gains.clock import Clock, can_enter_new, is_premarket, is_session_day, session_date
from simple_gains.config import (
    DEFAULT_SLIPPAGE_BPS,
    MODE_HITL,
    MODE_LIVE,
    MODE_PAPER,
    S_TIER_FLAG_COUNT,
    STARTING_EQUITY,
    VALID_MODES,
)
from simple_gains.data.base import MarketData
from simple_gains.data.finnhub import FinnhubData
from simple_gains.data.fixtures import FixtureData
from simple_gains.lanes.grader import Grader
from simple_gains.lanes.journal import Journal
from simple_gains.lanes.risk import RiskOfficer, next_stop
from simple_gains.lanes.scout import Scout, confirmation_closes_above_orh
from simple_gains.models import (
    Candle,
    Decision,
    GraderCard,
    JournalKind,
    MarketSnapshot,
    OrderTicket,
    Position,
    ScoutVerdict,
)
from simple_gains.persist import Store

load_dotenv()

DEFAULT_DB = Path(os.environ.get("SIMPLE_GAINS_DB") or Path.cwd() / "data" / "simple_gains.sqlite")
DEFAULT_UNIVERSE = ["AAPL", "NVDA", "TSLA", "AMD", "MSFT", "META", "AMZN", "GOOGL"]


def default_store(path: Path | None = None) -> Store:
    return Store(path or DEFAULT_DB)


def build_broker(store: Store, mode: str | None = None) -> Broker:
    mode = (mode or os.environ.get("SIMPLE_GAINS_MODE") or MODE_PAPER).lower()
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")
    starting = Decimal(os.environ.get("SIMPLE_GAINS_STARTING_EQUITY") or str(STARTING_EQUITY))
    slip = int(os.environ.get("SIMPLE_GAINS_SLIPPAGE_BPS") or DEFAULT_SLIPPAGE_BPS)
    account = store.ensure_account(starting=starting, mode=mode, slippage_bps=slip)
    account.mode = mode
    store.save_account(account)
    if mode == MODE_LIVE:
        return WebullStubBroker()
    if mode == MODE_HITL:
        return HITLBroker(store, account)
    return PaperBroker(store, account)


def build_data(use_fixtures: bool, fixture_path: Path | None = None) -> MarketData:
    if use_fixtures or not os.environ.get("FINNHUB_API_KEY"):
        return FixtureData(path=fixture_path)
    return FinnhubData()


class Engine:
    def __init__(
        self,
        store: Store,
        broker: Broker,
        data: MarketData,
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self.data = data
        self.clock = clock or Clock()
        self.scout = Scout()
        self.grader = Grader()
        self.risk = RiskOfficer(self.grader)
        self.journal = Journal(store, self.grader)

    def now(self) -> datetime:
        return self.clock.now()

    def session(self) -> date:
        return session_date(self.now())

    def scan(self, tickers: list[str] | None = None, session: date | None = None) -> list[dict[str, str]]:
        """Premarket / any-time hunt. Scout does not score. Watchlist only."""
        session = session or self.session()
        if isinstance(self.data, FixtureData):
            rows = self.data.watchlist_meta()
            tickers = [r[0] for r in rows]
        else:
            tickers = [t.upper() for t in (tickers or DEFAULT_UNIVERSE)]
            rows = []
            for t in tickers:
                try:
                    prof = self.data.profile(t)
                    rows.append((t, prof.theme, prof.sector, "scan"))
                except Exception as exc:  # pragma: no cover - network
                    rows.append((t, "other", "other", f"profile_error:{exc}"))
        self.store.set_watchlist(session, rows)
        self.journal.record(
            JournalKind.SCAN,
            self.now(),
            session,
            payload={"tickers": tickers, "note": "premarket scan/context — no orders"},
        )
        return self.store.watchlist(session)

    def _snapshot(self, ticker: str, session: date) -> MarketSnapshot:
        on_wl = self.store.on_watchlist(session, ticker)
        return self.data.snapshot(ticker, session, on_watchlist=on_wl)

    def _confirmation(self, snap: MarketSnapshot, orh: Decimal) -> Candle | None:
        if not snap.five_min:
            return None
        for bar in snap.five_min:
            if bar.ts.hour == 9 and bar.ts.minute < 45:
                continue
            if confirmation_closes_above_orh(bar, orh):
                return bar
        return None

    def evaluate_ticker(self, ticker: str, session: date | None = None) -> dict:
        session = session or self.session()
        now = self.now()
        ticker = ticker.upper()
        result: dict = {"ticker": ticker, "session": session.isoformat()}

        if isinstance(self.broker, WebullStubBroker):
            result["error"] = "live stub refuses all work that would send an order"
            return result

        snap = self._snapshot(ticker, session)
        or_high = None
        from simple_gains.lanes.scout import opening_range_candle

        or_bar = opening_range_candle(snap.five_min, session)
        if or_bar is None and snap.fifteen_min:
            or_bar = snap.fifteen_min[0]
        if or_bar:
            or_high = or_bar.high
        confirm = self._confirmation(snap, or_high) if or_high is not None else None

        open_pos = self.broker.positions()
        verdict = self.scout.evaluate(
            snap,
            now,
            open_position_count=len(open_pos),
            already_open_ticker=any(p.ticker == ticker for p in open_pos),
            confirmation=confirm,
        )
        result["scout"] = verdict.model_dump(mode="json")

        if not verdict.passed:
            self.journal.skip(now, session, ticker, verdict.reason or "scout_fail")
            result["decision"] = "scout_fail"
            return result

        if confirm is None or verdict.opening_range is None:
            self.journal.skip(now, session, ticker, "no_5m_close_above_orh")
            result["decision"] = "waiting_trigger"
            return result

        # Trigger is the 5-minute close, not the score.
        if not can_enter_new(confirm.ts) or is_premarket(confirm.ts):
            self.journal.skip(now, session, ticker, "confirmation_outside_entry_window")
            result["decision"] = "time_fail"
            return result

        breakers = self.store.load_breakers()
        account = self.store.ensure_account()
        self.risk.update_breakers(breakers, account, open_pos, session)
        self.store.save_breakers(breakers)

        card = self.grader.score(snap, verdict, s_tier_already=breakers.s_tier_count)
        self.store.save_card(card)
        self.journal.signal(now, card, extra={"orh": str(verdict.opening_range.high), "trigger": str(confirm.close)})
        result["card"] = card.model_dump(mode="json")

        if card.tier == "S":
            breakers.s_tier_count += 1
            if breakers.s_tier_count >= S_TIER_FLAG_COUNT:
                self.journal.record(
                    JournalKind.S_TIER_FLAG,
                    now,
                    session,
                    ticker,
                    {"count": breakers.s_tier_count, "note": "S-tier must be rare"},
                )
            self.store.save_breakers(breakers)

        if card.decision == Decision.SKIP:
            self.journal.skip(now, session, ticker, "below_85_never_round_up", card=card)
            result["decision"] = "skip"
            return result

        veto_reason = self.store.is_vetoed(session, ticker)
        decision = self.risk.review(
            card,
            verdict,
            snap,
            account,
            breakers,
            open_pos,
            already_vetoed=veto_reason,
            buying_power=self.broker.buying_power() if not isinstance(self.broker, WebullStubBroker) else None,
        )
        result["risk"] = decision.model_dump(mode="json")

        if decision.veto:
            self.store.add_veto(session, ticker, decision.veto_reason, now)
            self.journal.veto(now, session, ticker, decision.veto_reason, card=card)
            result["decision"] = "veto"
            return result

        if not decision.accepted:
            self.journal.skip(now, session, ticker, decision.skip_reason, card=card)
            result["decision"] = "risk_skip"
            return result

        ticket = OrderTicket(
            ticker=ticker,
            side="buy",
            shares=decision.shares,
            session=session,
            intended_price=decision.planned_entry,
            stop=decision.planned_stop,
            theme=card.theme,
            sector=card.sector,
            risk_pct=card.mapped_risk_pct,
            grader_total=card.total,
            tier=card.tier,
            reason="orb_5m_close_above_orh",
        )
        fill = self.broker.place_market_buy(ticket, confirm.ts, decision.planned_entry)
        if fill.shares == 0 and fill.note == "hitl_alert_no_fill":
            self.journal.record(
                JournalKind.ALERT,
                now,
                session,
                ticker,
                {"message": "HITL alert — no auto fill", "ticket": ticket.model_dump(mode="json")},
                card=card,
            )
            result["decision"] = "hitl_alert"
            return result

        # Recalc on actual fill; cut if real risk > 2% ceiling.
        cut = self.risk.apply_fill_cut(decision, fill, account.equity)
        if cut.cut_reason and isinstance(self.broker, PaperBroker) and cut.shares < fill.shares:
            self.broker.reduce_shares(ticker, cut.shares, confirm.ts, fill.price)
            fill = fill.model_copy(update={"shares": cut.shares, "note": fill.note + ";cut_to_2pct"})

        pos = next((p for p in self.broker.positions() if p.ticker == ticker), None)
        if pos and fill.price > pos.initial_stop:
            pos.r_value = fill.price - pos.initial_stop
            pos.fill_price = fill.price
            self.store.save_position(pos)

        self.journal.fill(
            confirm.ts,
            session,
            ticker,
            {
                "side": "buy",
                "shares": fill.shares,
                "price": str(fill.price),
                "stop": str(decision.planned_stop),
                "tier": card.tier,
                "risk_pct": str(card.mapped_risk_pct),
                "theme": card.theme,
                "cut_reason": cut.cut_reason,
                "r_value": str(pos.r_value if pos else decision.r_value),
            },
            card=card,
        )
        self.store.mark_equity(now, session, self.broker.equity(), "fill")
        result["decision"] = "filled"
        result["fill"] = fill.model_dump(mode="json")
        return result

    def manage_open(self, session: date | None = None) -> list[dict]:
        """After 14:00 CDT this is the only path that may act on the book."""
        session = session or self.session()
        now = self.now()
        reports = []
        prices: dict[str, Decimal] = {}
        for pos in list(self.broker.positions()):
            snap = self._snapshot(pos.ticker, session)
            last = snap.quote.last
            prices[pos.ticker] = last
            if isinstance(self.broker, PaperBroker):
                self.broker.mark(pos.ticker, last)
            pos.last_price = last
            new_stop, stage = next_stop(pos, last, snap.prior_day_low, snap.daily_20_ema)
            if new_stop > pos.live_stop:
                if isinstance(self.broker, PaperBroker):
                    self.broker.update_stop(pos.ticker, new_stop, stage)
                self.journal.trail(
                    now,
                    session,
                    pos.ticker,
                    {
                        "from": str(pos.live_stop),
                        "to": str(new_stop),
                        "stage": stage,
                    },
                )
                pos.live_stop = new_stop
                pos.stop_stage = stage
            stopped = self._stopped_out(pos, snap)
            if stopped:
                reports.append(self._close_stop(pos, snap, now, session))
            else:
                reports.append({"ticker": pos.ticker, "live_stop": str(pos.live_stop), "stage": pos.stop_stage})

        account = self.store.ensure_account()
        if not isinstance(self.broker, WebullStubBroker):
            account.equity = self.broker.equity()
        breakers = self.store.load_breakers()
        events = self.risk.update_breakers(breakers, account, self.broker.positions(), session)
        for name in events:
            self.journal.breaker(now, session, name, {"equity": str(account.equity)})
            if name == "portfolio_stop" and isinstance(self.broker, PaperBroker):
                fills = self.broker.flatten_all(now, prices, "portfolio_stop")
                for f in fills:
                    self.journal.record(
                        JournalKind.FLATTEN,
                        now,
                        session,
                        f.ticker,
                        {"price": str(f.price), "shares": f.shares, "reason": "portfolio_stop"},
                    )
        self.store.save_breakers(breakers)
        if not isinstance(self.broker, WebullStubBroker):
            self.store.mark_equity(now, session, self.broker.equity(), "mark")
        return reports

    def _stopped_out(self, pos: Position, snap: MarketSnapshot) -> bool:
        if snap.five_min:
            last_bar = snap.five_min[-1]
            return last_bar.low <= pos.live_stop
        return snap.quote.last <= pos.live_stop

    def _close_stop(self, pos: Position, snap: MarketSnapshot, now: datetime, session: date) -> dict:
        bar = snap.five_min[-1] if snap.five_min else None
        px = pos.live_stop
        if bar is not None and bar.open < pos.live_stop:
            px = bar.open  # gap through the stop
        ticket = OrderTicket(
            ticker=pos.ticker,
            side="sell",
            shares=pos.shares,
            session=session,
            intended_price=px,
            stop=pos.live_stop,
            theme=pos.theme,
            sector=pos.sector,
            risk_pct=pos.risk_pct,
            grader_total=0,
            tier="",
            reason="stop",
        )
        fill = self.broker.place_market_sell(ticket, now, px)
        pnl = (fill.price - pos.fill_price) * Decimal(fill.shares)
        self.journal.stop(
            now,
            session,
            pos.ticker,
            {
                "shares": fill.shares,
                "price": str(fill.price),
                "reason": "mechanical_stop",
                "realized_pnl": str(pnl),
                "r_value": str(pos.r_value),
                "stage": pos.stop_stage,
            },
        )
        account = self.store.ensure_account()
        if not isinstance(self.broker, WebullStubBroker):
            account.equity = self.broker.equity()
            self.store.save_account(account)
        breakers = self.store.load_breakers()
        events = self.risk.update_breakers(
            breakers,
            account,
            self.broker.positions(),
            session,
            just_closed_loss=pnl < 0,
        )
        for name in events:
            self.journal.breaker(now, session, name, {"after_stop": pos.ticker})
        self.store.save_breakers(breakers)
        self.store.mark_equity(now, session, account.equity, "stop")
        return {"ticker": pos.ticker, "stopped": True, "price": str(fill.price), "pnl": str(pnl)}

    def run_session(self, session: date | None = None, tickers: list[str] | None = None) -> dict:
        session = session or self.session()
        if not is_session_day(session):
            return {"error": f"{session} is not a US equity session day"}
        watch = self.scan(tickers, session)
        names = [w["ticker"] for w in watch]
        evaluations = [self.evaluate_ticker(t, session) for t in names]
        managed = self.manage_open(session)
        return {
            "session": session.isoformat(),
            "mode": getattr(self.broker, "mode", ""),
            "watchlist": watch,
            "evaluations": evaluations,
            "managed": managed,
            "equity": str(self.broker.equity()) if not isinstance(self.broker, WebullStubBroker) else None,
            "breakers": self.store.load_breakers().model_dump(mode="json"),
        }

    def dashboard_payload(self) -> dict:
        session = self.session()
        acct = self.store.ensure_account()
        try:
            equity = self.broker.equity()
        except LiveTradingDisabled:
            equity = acct.equity
        try:
            positions = [p.model_dump(mode="json") for p in self.broker.positions()]
        except LiveTradingDisabled:
            positions = []
        return {
            "philosophy": "Quality over quantity. Consistent gains over greedy wins.",
            "mode": getattr(self.broker, "mode", acct.mode),
            "session": session.isoformat(),
            "now": self.now().isoformat(),
            "can_enter": can_enter_new(self.now()),
            "premarket": is_premarket(self.now()),
            "equity": str(equity),
            "cash": str(acct.cash),
            "high_water": str(acct.high_water),
            "starting_equity": str(acct.starting_equity),
            "watchlist": self.store.watchlist(session),
            "positions": positions,
            "breakers": self.store.load_breakers().model_dump(mode="json"),
            "cards": [c.model_dump(mode="json") for c in self.store.cards(session)],
            "journal": [e.model_dump(mode="json") for e in self.store.journal(limit=80)],
            "equity_curve": self.store.equity_curve(),
            "alerts": self.store.alerts(limit=20),
            "closed": self.store.closed_trades(20),
            "live_disabled": isinstance(self.broker, WebullStubBroker),
        }

    def file_postmortem(self, text: str) -> None:
        now = self.now()
        session = self.session()
        breakers = self.risk.file_postmortem(self.store.load_breakers())
        self.store.save_breakers(breakers)
        self.journal.record(JournalKind.POSTMORTEM, now, session, payload={"text": text})

    def file_weekly_review(self, text: str) -> None:
        now = self.now()
        session = self.session()
        breakers = self.risk.file_weekly_review(self.store.load_breakers())
        self.store.save_breakers(breakers)
        self.journal.record(JournalKind.WEEKLY_REVIEW, now, session, payload={"text": text})

    def reauthorize(self) -> None:
        now = self.now()
        session = self.session()
        acct = self.store.ensure_account()
        breakers = self.risk.reauthorize(self.store.load_breakers(), acct.high_water)
        self.store.save_breakers(breakers)
        self.journal.record(JournalKind.AUTHORIZE, now, session, payload={"high_water": str(acct.high_water)})
