"""Stub live/Webull adapter. Refuses to send any order. No tickets, no balances invented."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from simple_gains.broker.base import Broker, LiveTradingDisabled
from simple_gains.config import MODE_LIVE
from simple_gains.models import Fill, OrderTicket, Position


REFUSAL = (
    "Webull live mode is not enabled. This stub refuses to send orders. "
    "Simple Gains v1 is paper/sim only — no live brokerage tickets."
)


class WebullStubBroker(Broker):
    mode = MODE_LIVE

    def equity(self) -> Decimal:
        raise LiveTradingDisabled("No live account balance is available or invented.")

    def cash(self) -> Decimal:
        raise LiveTradingDisabled("No live cash balance is available or invented.")

    def positions(self) -> list[Position]:
        raise LiveTradingDisabled(REFUSAL)

    def place_market_buy(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill:
        raise LiveTradingDisabled(REFUSAL)

    def place_market_sell(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill:
        raise LiveTradingDisabled(REFUSAL)

    def flatten_all(self, ts: datetime, prices: dict[str, Decimal], reason: str) -> list[Fill]:
        raise LiveTradingDisabled(REFUSAL)
