"""Market data port. Finnhub today; another vendor can implement this later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from simple_gains.models import Candle, MarketSnapshot, Profile, Quote


class MarketData(ABC):
    @abstractmethod
    def snapshot(self, ticker: str, session: date, on_watchlist: bool) -> MarketSnapshot: ...

    @abstractmethod
    def candles(self, ticker: str, session: date, resolution: str) -> list[Candle]: ...

    @abstractmethod
    def quote(self, ticker: str) -> Quote: ...

    @abstractmethod
    def profile(self, ticker: str) -> Profile: ...

    @abstractmethod
    def index_tape(self, session: date) -> dict[str, object]: ...
