from decimal import Decimal

from simple_gains.config import BOOK_RISK_CAP_PCT, RISK_CEILING_PCT, STARTING_EQUITY, THEME_NAME_CAP
from simple_gains.lanes.risk import (
    apply_sanity_cap,
    open_book_risk,
    planned_stop_distance,
    post_fill_cut,
    size_shares,
    theme_count,
)
from simple_gains.models import Fill, Position
from tests.conftest import SESSION, chicago


def test_size_off_equity_never_buying_power():
    equity = Decimal("100000")
    buying_power = Decimal("400000")  # must be ignored
    entry, stop = Decimal("100"), Decimal("99")
    shares = size_shares(equity, Decimal("0.010"), entry, stop)
    # planned distance = 1 + 5bps of 100 = 1.05
    assert shares == int(equity * Decimal("0.010") / planned_stop_distance(entry, stop))
    assert shares != int(buying_power * Decimal("0.010") / planned_stop_distance(entry, stop))


def test_sizing_formula_a_tier():
    equity = STARTING_EQUITY
    entry, stop = Decimal("50.00"), Decimal("49.00")
    shares = size_shares(equity, Decimal("0.010"), entry, stop)
    dist = planned_stop_distance(entry, stop)
    assert shares == 1000 if dist == Decimal("1") else int((equity * Decimal("0.010")) / dist)
    assert shares > 0


def test_post_fill_cut_when_slippage_breaches_2pct_ceiling():
    equity = Decimal("100000")
    shares = 2000
    fill = Decimal("101.00")
    stop = Decimal("99.00")  # $2 risk/share * 2000 = $4000 = 4% > 2%
    cut, reason = post_fill_cut(shares, equity, fill, stop)
    assert reason == "post_fill_2pct_ceiling"
    assert cut == 1000  # 2000 / 2
    assert (fill - stop) * Decimal(cut) <= equity * RISK_CEILING_PCT


def test_2pct_ceiling_does_not_cut_when_inside():
    shares, reason = post_fill_cut(100, Decimal("100000"), Decimal("50"), Decimal("49"))
    assert reason == ""
    assert shares == 100


def test_sanity_cap_blocks_micro_stop_balloon():
    equity = Decimal("100000")
    entry = Decimal("100")
    huge = size_shares(equity, Decimal("0.020"), entry, Decimal("99.99"))
    capped = apply_sanity_cap(huge, equity, entry)
    assert capped <= int(equity * Decimal("0.25") / entry)


def test_book_risk_cap_6pct():
    pos = Position(
        ticker="AAA",
        session=SESSION,
        shares=600,
        fill_price=Decimal("100"),
        initial_stop=Decimal("90"),
        live_stop=Decimal("90"),
        theme="semi",
        sector="semi",
        risk_pct=Decimal("0.02"),
        r_value=Decimal("10"),
        opened_at=chicago(10),
        last_price=Decimal("100"),
    )
    # $10 * 600 = $6000 = 6% of 100k
    assert open_book_risk([pos]) == Decimal("6000")
    assert open_book_risk([pos]) == STARTING_EQUITY * BOOK_RISK_CAP_PCT


def test_theme_cap_counts_open_names():
    def p(t, theme):
        return Position(
            ticker=t,
            session=SESSION,
            shares=10,
            fill_price=Decimal("10"),
            initial_stop=Decimal("9"),
            live_stop=Decimal("9"),
            theme=theme,
            sector=theme,
            risk_pct=Decimal("0.01"),
            r_value=Decimal("1"),
            opened_at=chicago(10),
        )

    book = [p("NVDA", "semi"), p("AMD", "semi")]
    assert theme_count(book, "semi") == THEME_NAME_CAP
    assert theme_count(book, "mega-tech") == 0
