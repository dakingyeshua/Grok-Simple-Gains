from simple_gains.clock import (
    can_enter_new,
    can_place_order,
    entry_cutoff,
    is_premarket,
    premarket_scan_start,
    regular_open,
)
from simple_gains.broker.webull_stub import WebullStubBroker
from simple_gains.broker.base import LiveTradingDisabled
from simple_gains.models import OrderTicket
from tests.conftest import SESSION, chicago
from decimal import Decimal


def test_premarket_is_scan_only_never_orders():
    ts = premarket_scan_start(SESSION).replace(hour=4, minute=15)
    assert is_premarket(ts)
    assert not can_place_order(ts)
    assert not can_enter_new(ts)


def test_entries_open_after_regular_open_before_2pm():
    assert can_enter_new(regular_open(SESSION))
    assert can_enter_new(chicago(13, 59))
    assert not can_enter_new(entry_cutoff(SESSION))
    assert not can_enter_new(chicago(14, 30))


def test_webull_stub_refuses_orders():
    stub = WebullStubBroker()
    ticket = OrderTicket(
        ticker="AAPL",
        side="buy",
        shares=1,
        session=SESSION,
        intended_price=Decimal("100"),
        stop=Decimal("99"),
        theme="mega-tech",
        sector="mega-tech",
        risk_pct=Decimal("0.01"),
        grader_total=90,
        tier="A+",
    )
    try:
        stub.place_market_buy(ticket, chicago(10), Decimal("100"))
        raise AssertionError("stub must refuse")
    except LiveTradingDisabled as exc:
        assert "refuses" in str(exc).lower() or "not enabled" in str(exc).lower()
