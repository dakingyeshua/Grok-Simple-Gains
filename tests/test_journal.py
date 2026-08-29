from datetime import datetime
from decimal import Decimal

import pytest

from simple_gains.lanes.journal import Journal
from simple_gains.lanes.grader import Grader
from simple_gains.models import IncompleteGraderCard, JournalKind
from tests.conftest import SESSION, chicago, make_card


def test_journal_rejects_incomplete_grader_card(store):
    journal = Journal(store, Grader())
    card = make_card()
    card.total = 70  # no longer matches six-bucket sum
    with pytest.raises(IncompleteGraderCard):
        journal.signal(chicago(10), card)


def test_paper_fill_and_stop_out_complete_journal(store):
    journal = Journal(store, Grader())
    card = make_card(total=90)
    ts = chicago(10)
    journal.signal(ts, card, extra={"orh": "185"})
    journal.fill(
        ts,
        SESSION,
        "AAPL",
        {
            "side": "buy",
            "shares": 100,
            "price": "185.60",
            "stop": "184.90",
            "tier": "A+",
            "risk_pct": "0.015",
            "theme": "mega-tech",
        },
        card,
    )
    journal.stop(
        chicago(11),
        SESSION,
        "AAPL",
        {
            "shares": 100,
            "price": "184.90",
            "reason": "mechanical_stop",
            "realized_pnl": "-70",
            "r_value": "0.70",
        },
    )
    kinds = store.journal_kinds_for(SESSION, "AAPL")
    assert kinds >= {"signal", "fill", "stop"}
    events = store.journal(session=SESSION)
    fill = next(e for e in events if e.kind == JournalKind.FILL)
    stop = next(e for e in events if e.kind == JournalKind.STOP)
    assert "card" in fill.payload
    assert fill.payload["shares"] == 100
    assert stop.payload["reason"] == "mechanical_stop"
    assert "realized_pnl" in stop.payload
