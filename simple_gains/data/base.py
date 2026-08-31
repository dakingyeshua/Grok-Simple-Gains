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

    def source_lists(self) -> dict[str, list[str]]:
        """Scout source lists keyed by most_active / unusual_volume / top_gainers /
        unusual_options. Empty means the engine falls back to its default universe.
        Unusual Options is catalyst/watchlist only. This port does not fetch candles.
        """
        return {}
