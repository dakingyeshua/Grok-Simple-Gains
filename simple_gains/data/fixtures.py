"""Offline market data for tests and --fixtures sessions. No API key required."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from simple_gains.clock import CHICAGO, as_chicago, regular_open
from simple_gains.models import Candle, MarketSnapshot, Profile, Quote
from simple_gains.data.base import MarketData


def _D(v: Any) -> Decimal:
    return Decimal(str(v))


def parse_candle(raw: dict[str, Any]) -> Candle:
    ts = raw["ts"]
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts)
    else:
        dt = datetime.fromtimestamp(int(ts), tz=CHICAGO)
    return Candle(
        ts=as_chicago(dt),
        open=_D(raw["open"]),
        high=_D(raw["high"]),
        low=_D(raw["low"]),
        close=_D(raw["close"]),
        volume=int(raw.get("volume", 0)),
    )


def ema(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    k = Decimal("2") / Decimal(period + 1)
    seed = sum(values[:period], Decimal("0")) / Decimal(period)
    val = seed
    for price in values[period:]:
        val = price * k + val * (Decimal("1") - k)
    return val


def adr20(dailies: list[Candle]) -> Decimal:
    window = dailies[-20:] if len(dailies) >= 20 else dailies
    if not window:
        return Decimal("0")
    return sum((c.high - c.low for c in window), Decimal("0")) / Decimal(len(window))


class FixtureData(MarketData):
    def __init__(self, payload: dict[str, Any] | None = None, path: Path | None = None) -> None:
        if payload is None:
            path = path or default_fixture_path()
            payload = json.loads(path.read_text())
        self.raw = payload
        self.session = date.fromisoformat(payload["session"])

    def _name(self, ticker: str) -> dict[str, Any]:
        return self.raw["names"][ticker.upper()]

    def candles(self, ticker: str, session: date, resolution: str) -> list[Candle]:
        key = {"5": "five_min", "15": "fifteen_min", "D": "daily"}[resolution]
        return [parse_candle(c) for c in self._name(ticker).get(key, [])]

    def quote(self, ticker: str) -> Quote:
        q = self._name(ticker)["quote"]
        return Quote(
            ticker=ticker.upper(),
            bid=_D(q["bid"]),
            ask=_D(q["ask"]),
            last=_D(q["last"]),
            halted=bool(q.get("halted", False)),
        )

    def profile(self, ticker: str) -> Profile:
        p = self._name(ticker).get("profile", {})
        exch = p.get("exchange", "NASDAQ")
        return Profile(
            ticker=ticker.upper(),
            exchange=exch,
            sector=p.get("sector", "other"),
            theme=p.get("theme", p.get("sector", "other")),
            is_nasdaq="NASDAQ" in exch.upper() or ticker.upper() in {"AAPL", "NVDA", "TSLA", "AMD", "MSFT", "META", "AMZN", "GOOGL"},
        )

    def index_tape(self, session: date) -> dict[str, object]:
        idx = self.raw.get("index", {})
        return {
            "spy_session_ret": _D(idx.get("spy_session_ret", "0")),
            "qqq_session_ret": _D(idx.get("qqq_session_ret", "0")),
            "spy_last_5m_red": bool(idx.get("spy_last_5m_red", False)),
            "qqq_last_5m_red": bool(idx.get("qqq_last_5m_red", False)),
        }

    def snapshot(self, ticker: str, session: date, on_watchlist: bool) -> MarketSnapshot:
        ticker = ticker.upper()
        raw = self._name(ticker)
        daily = self.candles(ticker, session, "D")
        five = self.candles(ticker, session, "5")
        fifteen = self.candles(ticker, session, "15")
        tape = self.index_tape(session)
        closes = [c.close for c in daily]
        prior_low = daily[-2].low if len(daily) >= 2 else (daily[-1].low if daily else None)
        return MarketSnapshot(
            ticker=ticker,
            session=session,
            quote=self.quote(ticker),
            profile=self.profile(ticker),
            five_min=five,
            fifteen_min=fifteen,
            daily=daily,
            adv_shares=int(raw.get("adv_shares", 2_000_000)),
            adr=adr20(daily) if daily else _D(raw.get("adr", "0")),
            daily_20_ema=ema(closes, 20),
            prior_day_low=prior_low,
            has_catalyst=bool(raw.get("has_catalyst", False)),
            catalyst_note=raw.get("catalyst_note", ""),
            on_watchlist=on_watchlist,
            pattern_hint=raw.get("pattern_hint", ""),
            level_note=raw.get("level_note", ""),
            spy_session_ret=tape["spy_session_ret"],  # type: ignore[arg-type]
            qqq_session_ret=tape["qqq_session_ret"],  # type: ignore[arg-type]
            spy_last_5m_red=tape["spy_last_5m_red"],  # type: ignore[arg-type]
            qqq_last_5m_red=tape["qqq_last_5m_red"],  # type: ignore[arg-type]
            hitl_level_override=raw.get("hitl_level_override"),
            hitl_catalyst_override=raw.get("hitl_catalyst_override"),
        )

    def watchlist_tickers(self) -> list[str]:
        return list(self.raw.get("watchlist", self.raw["names"].keys()))

    def watchlist_meta(self) -> list[tuple[str, str, str, str]]:
        rows = []
        for t in self.watchlist_tickers():
            if t.upper() in {"SPY", "QQQ"}:
                continue
            p = self.profile(t)
            note = self._name(t).get("catalyst_note", "")
            rows.append((t.upper(), p.theme, p.sector, note))
        return rows


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parents[1].parent / "tests" / "fixtures" / "session_orb.json"


def make_orb_bars(
    session: date,
    or_open: Decimal,
    or_high: Decimal,
    or_low: Decimal,
    trigger_close: Decimal,
    trigger_low: Decimal,
    later: list[tuple[Decimal, Decimal, Decimal, Decimal]] | None = None,
) -> list[dict[str, Any]]:
    """Helper used by tests to synthesize an 8:30 CT OR + 5-minute trigger."""
    start = regular_open(session)
    bars = []
    # three 5-minute bars composing the opening range (8:30–8:45 CT)
    pieces = [
        (or_open, or_high, (or_open + or_low) / 2, (or_open + or_high) / 2),
        ((or_open + or_high) / 2, or_high, or_low, (or_high + or_low) / 2),
        ((or_high + or_low) / 2, or_high, or_low, (or_open + or_low) / 2),
    ]
    for i, (o, h, l, c) in enumerate(pieces):
        ts = start + __import__("datetime").timedelta(minutes=5 * i)
        bars.append(_bar(ts, o, h, l, c, 400_000 + i * 10_000))
    # confirmation 08:45–08:50 CT (first ORB-eligible 5-minute bar)
    trig_ts = start + __import__("datetime").timedelta(minutes=15)
    bars.append(_bar(trig_ts, or_high, trigger_close, trigger_low, trigger_close, 800_000))
    cursor = start + __import__("datetime").timedelta(minutes=20)
    for o, h, l, c in later or []:
        bars.append(_bar(cursor, o, h, l, c, 300_000))
        cursor += __import__("datetime").timedelta(minutes=5)
    return bars


def _bar(ts: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal, vol: int) -> dict[str, Any]:
    return {
        "ts": as_chicago(ts).isoformat(),
        "open": str(o),
        "high": str(h),
        "low": str(l),
        "close": str(c),
        "volume": vol,
    }
