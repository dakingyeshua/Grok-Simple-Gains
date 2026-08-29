"""Broker / execution port. Live Webull can implement this later without rewriting rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from simple_gains.models import Fill, OrderTicket, Position


class LiveTradingDisabled(RuntimeError):
    """Raised by the live stub. No tickets are sent."""


class Broker(ABC):
    mode: str

    @abstractmethod
    def equity(self) -> Decimal:
        """Mark-to-market equity. Sizing uses this, never buying power."""

    def buying_power(self) -> Decimal:
        """Exposed for future live adapters. Risk Officer must ignore it."""
        return self.equity()

    @abstractmethod
    def cash(self) -> Decimal: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def place_market_buy(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill: ...

    @abstractmethod
    def place_market_sell(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill: ...

    @abstractmethod
    def flatten_all(self, ts: datetime, prices: dict[str, Decimal], reason: str) -> list[Fill]: ...
