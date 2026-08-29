"""Finnhub market-data adapter. Requires FINNHUB_API_KEY. Never places orders."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from simple_gains.clock import CHICAGO, as_chicago, regular_close, regular_open
from simple_gains.data.base import MarketData
from simple_gains.data.fixtures import adr20, ema
from simple_gains.models import Candle, MarketSnapshot, Profile, Quote

BASE = "https://finnhub.io/api/v1"

THEME_BY_INDUSTRY = {
    "semiconductor": "semi",
    "technology": "mega-tech",
    "software": "mega-tech",
    "internet": "mega-tech",
    "biotechnology": "biotech",
    "pharmaceutical": "biotech",
    "oil": "energy",
    "energy": "energy",
    "bank": "financials",
    "capital markets": "financials",
    "retail": "consumer",
    "automobile": "consumer",
    "aerospace": "industrial",
}


class FinnhubError(RuntimeError):
    pass


class FinnhubData(MarketData):
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        if not self.api_key:
            raise FinnhubError(
                "FINNHUB_API_KEY is not set. Use --fixtures or export a key. "
                "Tests never need a live key."
            )
        self._client = client or httpx.Client(timeout=20.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        params = dict(params)
        params["token"] = self.api_key
        r = self._client.get(f"{BASE}{path}", params=params)
        r.raise_for_status()
        data = r.json()
        return data

    def candles(self, ticker: str, session: date, resolution: str) -> list[Candle]:
        if resolution == "D":
            start = datetime.combine(session - timedelta(days=80), datetime.min.time(), tzinfo=CHICAGO)
            end = regular_close(session)
        else:
            start = regular_open(session) - timedelta(minutes=5)
            end = regular_close(session)
        raw = self._get(
            "/stock/candle",
            {
                "symbol": ticker.upper(),
                "resolution": resolution,
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
            },
        )
        if not raw or raw.get("s") != "ok":
            return []
        out = []
        for t, o, h, l, c, v in zip(raw["t"], raw["o"], raw["h"], raw["l"], raw["c"], raw["v"]):
            ts = as_chicago(datetime.fromtimestamp(int(t), tz=CHICAGO))
            out.append(
                Candle(
                    ts=ts,
                    open=Decimal(str(o)),
                    high=Decimal(str(h)),
                    low=Decimal(str(l)),
                    close=Decimal(str(c)),
                    volume=int(v),
                )
            )
        return out

    def quote(self, ticker: str) -> Quote:
        raw = self._get("/quote", {"symbol": ticker.upper()})
        last = Decimal(str(raw.get("c") or 0))
        # Finnhub quote has no bid/ask on the free plan; approximate a tight spread.
        bid = last * Decimal("0.9998")
        ask = last * Decimal("1.0002")
        return Quote(ticker=ticker.upper(), bid=bid, ask=ask, last=last, halted=last <= 0)

    def profile(self, ticker: str) -> Profile:
        raw = self._get("/stock/profile2", {"symbol": ticker.upper()})
        exch = str(raw.get("exchange") or "")
        industry = str(raw.get("finnhubIndustry") or raw.get("gics") or "other")
        theme = "other"
        low = industry.lower()
        for key, tag in THEME_BY_INDUSTRY.items():
            if key in low:
                theme = tag
                break
        nasdaq = "NASDAQ" in exch.upper()
        return Profile(
            ticker=ticker.upper(),
            exchange=exch,
            sector=industry or "other",
            theme=theme,
            is_nasdaq=nasdaq,
        )

    def _news_catalyst(self, ticker: str, session: date) -> tuple[bool, str]:
        raw = self._get(
            "/company-news",
            {
                "symbol": ticker.upper(),
                "from": (session - timedelta(days=2)).isoformat(),
                "to": session.isoformat(),
            },
        )
        if not raw or not isinstance(raw, list):
            return False, ""
        headline = str(raw[0].get("headline") or raw[0].get("summary") or "")
        return bool(headline), headline[:240]

    def index_tape(self, session: date) -> dict[str, object]:
        spy = self._index_ret("SPY", session)
        qqq = self._index_ret("QQQ", session)
        return {**spy, **qqq}

    def _index_ret(self, symbol: str, session: date) -> dict[str, object]:
        bars = self.candles(symbol, session, "5")
        prefix = "spy" if symbol == "SPY" else "qqq"
        if not bars:
            return {f"{prefix}_session_ret": Decimal("0"), f"{prefix}_last_5m_red": False}
        o = bars[0].open
        last = bars[-1].close
        ret = (last - o) / o if o else Decimal("0")
        return {
            f"{prefix}_session_ret": ret,
            f"{prefix}_last_5m_red": bars[-1].is_red,
        }

    def snapshot(self, ticker: str, session: date, on_watchlist: bool) -> MarketSnapshot:
        daily = self.candles(ticker, session, "D")
        five = [c for c in self.candles(ticker, session, "5") if c.ts.date() == session]
        fifteen = [c for c in self.candles(ticker, session, "15") if c.ts.date() == session]
        tape = self.index_tape(session)
        has_cat, note = self._news_catalyst(ticker, session)
        adv = 0
        if daily:
            window = daily[-20:]
            adv = int(sum(c.volume for c in window) / max(len(window), 1))
        prior_low = daily[-2].low if len(daily) >= 2 else None
        return MarketSnapshot(
            ticker=ticker.upper(),
            session=session,
            quote=self.quote(ticker),
            profile=self.profile(ticker),
            five_min=five,
            fifteen_min=fifteen,
            daily=daily,
            adv_shares=adv,
            adr=adr20(daily),
            daily_20_ema=ema([c.close for c in daily], 20),
            prior_day_low=prior_low,
            has_catalyst=has_cat,
            catalyst_note=note,
            on_watchlist=on_watchlist,
            spy_session_ret=tape["spy_session_ret"],  # type: ignore[arg-type]
            qqq_session_ret=tape["qqq_session_ret"],  # type: ignore[arg-type]
            spy_last_5m_red=tape["spy_last_5m_red"],  # type: ignore[arg-type]
            qqq_last_5m_red=tape["qqq_last_5m_red"],  # type: ignore[arg-type]
        )
