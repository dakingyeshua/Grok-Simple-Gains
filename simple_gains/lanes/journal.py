"""Journal: append-only record. Rejects any Grader card that lacks the six-bucket split."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from simple_gains.lanes.grader import Grader
from simple_gains.models import (
    GraderCard,
    IncompleteGraderCard,
    JournalEvent,
    JournalKind,
)
from simple_gains.persist import Store


REQUIRED_FILL_KEYS = {
    "side",
    "shares",
    "price",
    "stop",
    "tier",
    "risk_pct",
    "theme",
}
REQUIRED_STOP_KEYS = {"shares", "price", "reason", "realized_pnl", "r_value"}


class Journal:
    def __init__(self, store: Store, grader: Grader | None = None) -> None:
        self.store = store
        self.grader = grader or Grader()

    def record(
        self,
        kind: JournalKind,
        ts: datetime,
        session: date,
        ticker: str = "",
        payload: dict[str, Any] | None = None,
        card: GraderCard | None = None,
    ) -> JournalEvent:
        body = dict(payload or {})
        if card is not None:
            try:
                self.grader.validate_card(card)
            except IncompleteGraderCard:
                raise
            body["card"] = card.model_dump(mode="json")
        if kind == JournalKind.FILL:
            missing = REQUIRED_FILL_KEYS - set(body)
            if missing:
                raise IncompleteGraderCard(f"fill journal missing {sorted(missing)}")
        if kind == JournalKind.STOP:
            missing = REQUIRED_STOP_KEYS - set(body)
            if missing:
                raise IncompleteGraderCard(f"stop journal missing {sorted(missing)}")
        event = JournalEvent(ts=ts, session=session, kind=kind, ticker=ticker, payload=body)
        return self.store.append_journal(event)

    def signal(self, ts: datetime, card: GraderCard, extra: dict[str, Any] | None = None) -> JournalEvent:
        return self.record(JournalKind.SIGNAL, ts, card.date, card.ticker, extra, card=card)

    def skip(self, ts: datetime, session: date, ticker: str, reason: str, card: GraderCard | None = None) -> JournalEvent:
        return self.record(JournalKind.SKIP, ts, session, ticker, {"reason": reason}, card=card)

    def veto(self, ts: datetime, session: date, ticker: str, reason: str, card: GraderCard | None = None) -> JournalEvent:
        return self.record(JournalKind.VETO, ts, session, ticker, {"reason": reason}, card=card)

    def fill(self, ts: datetime, session: date, ticker: str, payload: dict[str, Any], card: GraderCard) -> JournalEvent:
        return self.record(JournalKind.FILL, ts, session, ticker, payload, card=card)

    def stop(self, ts: datetime, session: date, ticker: str, payload: dict[str, Any]) -> JournalEvent:
        return self.record(JournalKind.STOP, ts, session, ticker, payload)

    def trail(self, ts: datetime, session: date, ticker: str, payload: dict[str, Any]) -> JournalEvent:
        return self.record(JournalKind.TRAIL_HANDOFF, ts, session, ticker, payload)

    def breaker(self, ts: datetime, session: date, name: str, payload: dict[str, Any] | None = None) -> JournalEvent:
        body = {"name": name, **(payload or {})}
        return self.record(JournalKind.BREAKER, ts, session, "", body)
