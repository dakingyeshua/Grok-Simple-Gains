"""Desk artifacts: the authoritative Scout → Grader → Risk → Smiler write-path.

The paper engine does not invent a competing 100-point card when a desk grade
or ARMED ticket is present. On restart it resyncs these artifacts instead of
scoring a cold live snapshot that is missing Scout pattern / OR context.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from simple_gains.models import (
    CARD_SOURCE_DESK,
    DeskTicket,
    GradeContract,
    GraderCard,
    OrderTicket,
    TicketStatus,
)

DEFAULT_DESK_DIR = Path(os.environ.get("SIMPLE_GAINS_DESK_DIR") or Path.cwd() / "data" / "desk")


def default_desk_dir() -> Path:
    return Path(os.environ.get("SIMPLE_GAINS_DESK_DIR") or DEFAULT_DESK_DIR)


def load_desk_artifacts(
    directory: Path | None = None,
    *,
    session: date | None = None,
    ticker: str | None = None,
) -> list[dict[str, Any]]:
    """Load JSON desk artifacts. Missing directory is a no-op."""
    root = directory or default_desk_dir()
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        raw.setdefault("_path", str(path))
        art_session = raw.get("session") or (raw.get("card") or {}).get("date")
        art_ticker = str(raw.get("ticker") or (raw.get("card") or {}).get("ticker") or "").upper()
        if session is not None and art_session and str(art_session)[:10] != session.isoformat():
            continue
        if ticker is not None and art_ticker and art_ticker != ticker.upper():
            continue
        out.append(raw)
    return out


def parse_artifact(raw: dict[str, Any]) -> tuple[GraderCard | None, DeskTicket | None, GradeContract | None]:
    """Accept a desk JSON blob: card and/or ticket and/or GradeContract."""
    card = None
    ticket = None
    contract = None
    card_raw = raw.get("card")
    if isinstance(card_raw, dict):
        if "source" not in card_raw:
            card_raw = {**card_raw, "source": CARD_SOURCE_DESK}
        card = GraderCard.model_validate(card_raw)
    contract_raw = raw.get("contract") or raw.get("grade_contract") or raw.get("inputs")
    if isinstance(contract_raw, dict):
        contract = GradeContract.model_validate(contract_raw)
    elif card is not None:
        contract = card.contract
    ticket_raw = raw.get("ticket")
    status_raw = raw.get("status") or (ticket_raw or {}).get("status") or TicketStatus.ARMED.value
    if isinstance(ticket_raw, dict):
        status = TicketStatus(str(status_raw).upper())
        inner = ticket_raw.get("ticket") if "grader_total" not in ticket_raw and "ticket" in ticket_raw else ticket_raw
        inner = {k: v for k, v in inner.items() if k != "status"}
        ot = OrderTicket.model_validate(inner)
        ticket = DeskTicket(
            ticker=ot.ticker,
            session=ot.session,
            status=status,
            ticket=ot,
            source=str(raw.get("source") or CARD_SOURCE_DESK),
            note=str(raw.get("note") or "desk artifact"),
        )
    return card, ticket, contract
