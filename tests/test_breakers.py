from datetime import date
from decimal import Decimal

from simple_gains.clock import is_session_day
from simple_gains.lanes.risk import RiskOfficer
from simple_gains.models import AccountState, BreakerState
from tests.conftest import SESSION


def _acct(equity: Decimal) -> AccountState:
    return AccountState(
        equity=equity,
        cash=equity,
        starting_equity=Decimal("100000"),
        high_water=Decimal("100000"),
        mode="paper",
    )


def _next_session(d: date) -> date:
    from datetime import timedelta

    nxt = d + timedelta(days=1)
    while not is_session_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def test_daily_minus_10_halts_and_sets_next_session_off():
    officer = RiskOfficer()
    br = BreakerState(
        sod_date=SESSION,
        sod_equity=Decimal("100000"),
        high_water_equity=Decimal("100000"),
        weekly_anchor_monday=SESSION,
        weekly_anchor_equity=Decimal("50000"),
    )
    events = officer.update_breakers(br, _acct(Decimal("89999")), [], SESSION)
    assert "daily_stop" in events
    assert br.daily_halt
    assert br.daily_reason == "daily_minus_10pct"
    assert br.next_session_off
    assert br.next_session_off_date == _next_session(SESSION)
    assert br.postmortem_required and not br.postmortem_filed
    blocked, why = br.new_entries_blocked(SESSION)
    assert blocked and why == "daily_minus_10pct"


def test_next_session_off_after_day_stop():
    officer = RiskOfficer()
    nxt = _next_session(SESSION)
    br = BreakerState(
        next_session_off=True,
        next_session_off_date=nxt,
        postmortem_required=True,
        high_water_equity=Decimal("100000"),
    )
    officer.update_breakers(br, _acct(Decimal("100000")), [], nxt)
    blocked, why = br.new_entries_blocked(nxt)
    assert blocked
    assert "next_session_off" in why


def test_two_consecutive_closed_losses():
    officer = RiskOfficer()
    br = BreakerState(sod_date=SESSION, sod_equity=Decimal("100000"), high_water_equity=Decimal("100000"))
    officer.update_breakers(br, _acct(Decimal("100000")), [], SESSION, just_closed_loss=True)
    assert not br.daily_halt
    events = officer.update_breakers(br, _acct(Decimal("100000")), [], SESSION, just_closed_loss=True)
    assert "streak_stop" in events
    assert br.daily_reason == "two_consecutive_losses"
    assert br.next_session_off
    assert br.postmortem_required


def test_weekly_minus_6_until_review_filed():
    officer = RiskOfficer()
    br = BreakerState(
        sod_date=SESSION,
        sod_equity=Decimal("100000"),
        weekly_anchor_monday=SESSION,
        weekly_anchor_equity=Decimal("100000"),
        high_water_equity=Decimal("100000"),
    )
    events = officer.update_breakers(br, _acct(Decimal("93000")), [], SESSION)
    assert "weekly_stop" in events
    assert br.weekly_halt and br.weekly_review_required
    blocked, why = br.new_entries_blocked(SESSION)
    assert blocked and why == "weekly_stop_pending_review"
    officer.file_weekly_review(br)
    blocked, _ = br.new_entries_blocked(SESSION)
    assert not blocked


def test_portfolio_minus_20_flattens_until_reauthorize():
    officer = RiskOfficer()
    br = BreakerState(
        sod_date=SESSION,
        sod_equity=Decimal("80000"),
        high_water_equity=Decimal("100000"),
        weekly_anchor_monday=SESSION,
        weekly_anchor_equity=Decimal("80000"),
    )
    events = officer.update_breakers(br, _acct(Decimal("79000")), [], SESSION)
    assert "portfolio_stop" in events
    assert br.portfolio_halt and br.portfolio_flatten and br.reauthorize_required
    blocked, why = br.new_entries_blocked(SESSION)
    assert blocked and why == "portfolio_stop_pending_reauthorize"
    officer.reauthorize(br, Decimal("79000"))
    blocked, _ = br.new_entries_blocked(SESSION)
    assert not blocked
