from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from simple_gains.clock import CHICAGO, Clock, regular_open
from simple_gains.data.fixtures import FixtureData, make_orb_bars
from simple_gains.engine import Engine, build_broker
from simple_gains.models import (
    BucketScores,
    Candle,
    Decision,
    GraderCard,
    MarketSnapshot,
    Profile,
    Quote,
)
from simple_gains.persist import Store

SESSION = date(2024, 3, 15)  # Friday


def chicago(hour: int, minute: int = 0, day: date = SESSION) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CHICAGO)


def bar(ts: datetime, o, h, l, c, vol: int = 200_000) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=vol,
    )


def daily_series(close: Decimal = Decimal("185"), n: int = 25, start: date = date(2024, 2, 8)) -> list[Candle]:
    out = []
    px = close - Decimal("8")
    d = start
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        high = px + Decimal("1.2")
        low = px - Decimal("1.0")
        out.append(
            Candle(
                ts=datetime(d.year, d.month, d.day, 15, 0, tzinfo=CHICAGO),
                open=px,
                high=high,
                low=low,
                close=px + Decimal("0.3"),
                volume=12_000_000,
            )
        )
        px += Decimal("0.3")
        d += timedelta(days=1)
    out[-1] = out[-1].model_copy(update={"close": close, "high": max(out[-1].high, close)})
    return out


def orb_five_min(
    or_high=Decimal("185"),
    or_low=Decimal("184.2"),
    trigger_close=Decimal("185.60"),
    trigger_low=Decimal("184.90"),
    later=None,
) -> list[Candle]:
    raw = make_orb_bars(
        SESSION,
        or_open=Decimal("184.40"),
        or_high=Decimal(str(or_high)),
        or_low=Decimal(str(or_low)),
        trigger_close=Decimal(str(trigger_close)),
        trigger_low=Decimal(str(trigger_low)),
        later=later,
    )
    return [
        Candle(
            ts=datetime.fromisoformat(r["ts"]),
            open=Decimal(r["open"]),
            high=Decimal(r["high"]),
            low=Decimal(r["low"]),
            close=Decimal(r["close"]),
            volume=r["volume"],
        )
        for r in raw
    ]


def make_card(
    ticker="AAPL",
    total=90,
    theme="mega-tech",
    buckets: BucketScores | None = None,
    decision: Decision | None = None,
) -> GraderCard:
    if buckets is None:
        # A locked split that sums to `total` without exceeding caps.
        # Default 90: 22+18+18+12+10+10
        buckets = BucketScores(
            level_pattern=22,
            rs_vs_spy=18,
            volume=18,
            catalyst=12,
            daily_20_ema=10,
            opening_range_quality=10,
        )
        if total != 90:
            buckets = buckets.model_copy(update={"catalyst": max(0, 12 - (90 - total))})
            # rebuild exact total if needed by tests that pass explicit buckets
    if decision is None:
        decision = Decision.SKIP if total < 85 else (Decision.A if total < 90 else (Decision.A_PLUS if total < 95 else Decision.S))
    risk = {"skip": "0", "A": "0.010", "A+": "0.015", "S": "0.020"}[
        "skip" if decision == Decision.SKIP else decision.value
    ]
    return GraderCard(
        ticker=ticker,
        date=SESSION,
        session="RTH",
        pre_filter_pass_list=[
            "on_watchlist",
            "regular_session_before_cutoff",
            "first_15m_complete",
            "liquid_enough",
            "not_halted",
            "cluster_slot_open",
            "not_a_chase",
        ],
        buckets=buckets,
        total=buckets.capped_total() if total == 90 else total,
        tier="skip" if decision == Decision.SKIP else decision.value,
        mapped_risk_pct=Decimal(risk),
        theme=theme,
        sector=theme,
        spy_qqq_headwind_note="SPY session 0.10%",
        decision=decision,
    )


def make_snap(
    ticker="AAPL",
    last=Decimal("185.60"),
    on_watchlist=True,
    halted=False,
    adv=2_000_000,
    adr=Decimal("4.00"),
    spread_bid=None,
    five=None,
    theme="mega-tech",
    is_nasdaq=True,
    spy_ret=Decimal("0.001"),
    qqq_ret=Decimal("0.001"),
    spy_red=False,
    qqq_red=False,
    pattern="Inverted Head & Shoulders",
    level="clean daily 185",
    catalyst=True,
    catalyst_note="earnings beat",
    daily=None,
    ema=Decimal("183"),
    prior_low=Decimal("183.50"),
    premarket_high=None,
) -> MarketSnapshot:
    five = five if five is not None else orb_five_min()
    daily = daily if daily is not None else daily_series(last)
    bid = spread_bid if spread_bid is not None else last - Decimal("0.02")
    ask = last + Decimal("0.02")
    return MarketSnapshot(
        ticker=ticker,
        session=SESSION,
        quote=Quote(ticker=ticker, bid=bid, ask=ask, last=last, halted=halted),
        profile=Profile(ticker=ticker, exchange="NASDAQ", sector=theme, theme=theme, is_nasdaq=is_nasdaq),
        five_min=five,
        fifteen_min=[],
        daily=daily,
        adv_shares=adv,
        adr=adr,
        daily_20_ema=ema,
        prior_day_low=prior_low,
        has_catalyst=catalyst,
        catalyst_note=catalyst_note,
        on_watchlist=on_watchlist,
        pattern_hint=pattern,
        level_note=level,
        spy_session_ret=spy_ret,
        qqq_session_ret=qqq_ret,
        spy_last_5m_red=spy_red,
        qqq_last_5m_red=qqq_red,
        premarket_high=premarket_high,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "book.sqlite")


@pytest.fixture
def engine(store: Store) -> Engine:
    broker = build_broker(store, "paper")
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    clock = Clock()
    clock.freeze(chicago(10, 0))
    return Engine(store, broker, data, clock)
