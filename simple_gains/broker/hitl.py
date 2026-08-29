"""HITL mode: same paper book, but new entries become alerts instead of auto-fills."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from simple_gains.broker.paper import PaperBroker
from simple_gains.config import MODE_HITL
from simple_gains.models import Alert, Fill, OrderTicket
from simple_gains.persist import Store


class HITLBroker(PaperBroker):
    mode = MODE_HITL

    def __init__(self, store: Store, account) -> None:
        super().__init__(store, account)
        self.pending_alerts: list[Alert] = []

    def place_market_buy(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill:
        alert = Alert(
            ts=ts,
            session=ticket.session,
            ticker=ticket.ticker,
            message=(
                f"HITL: buy {ticket.shares} {ticket.ticker} at ~{fill_price} "
                f"stop {ticket.stop} ({ticket.tier} {ticket.risk_pct})"
            ),
            ticket=ticket,
        )
        self.pending_alerts.append(alert)
        self.store.add_alert(alert)
        return Fill(
            ticker=ticket.ticker,
            side="buy",
            shares=0,
            price=fill_price,
            ts=ts,
            session=ticket.session,
            note="hitl_alert_no_fill",
        )

    def confirm_alert(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill:
        """Operator accepts the alert; then we paper-fill at the given price."""
        return PaperBroker.place_market_buy(self, ticket, ts, fill_price)
