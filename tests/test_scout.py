from decimal import Decimal

from simple_gains.clock import entry_cutoff, opening_range_end, premarket_scan_start, regular_open
from simple_gains.config import STRETCH_ADR_MULTIPLE
from simple_gains.lanes.scout import Scout, confirmation_closes_above_orh, is_chase
from tests.conftest import SESSION, chicago, make_snap, orb_five_min


def _scout(snap, now, confirm=None, open_count=0, already=False):
    return Scout().evaluate(
        snap,
        now,
        open_position_count=open_count,
        already_open_ticker=already,
        confirmation=confirm,
    )


def test_wicks_do_not_count_as_trigger():
    orh = Decimal("185")
    wick_bar = orb_five_min()[-1].model_copy(update={"high": Decimal("186"), "close": Decimal("184.90")})
    assert wick_bar.high > orh
    assert not confirmation_closes_above_orh(wick_bar, orh)


def test_close_above_orh_is_trigger():
    orh = Decimal("185")
    bar = orb_five_min(trigger_close=Decimal("185.60"))[-1]
    assert confirmation_closes_above_orh(bar, orh)


def test_stretch_prefilter_0_8x_adr():
    orh = Decimal("100")
    adr = Decimal("5")
    assert not is_chase(Decimal("103.99"), orh, adr)
    assert is_chase(orh + STRETCH_ADR_MULTIPLE * adr, orh, adr)
    snap = make_snap(
        last=Decimal("189.00"),
        adr=Decimal("4.00"),
        five=orb_five_min(or_high=Decimal("185"), trigger_close=Decimal("184.50"), trigger_low=Decimal("184.00")),
    )
    # last 189 >= 185 + 0.8*4 = 188.2, and confirmation close 184.50 is NOT above ORH
    v = _scout(snap, chicago(10, 5), confirm=None)
    names = {f.name: f.passed for f in v.filters}
    assert names["not_a_chase"] is False


def test_premarket_never_orders():
    snap = make_snap()
    now = premarket_scan_start(SESSION).replace(hour=7)
    v = _scout(snap, now)
    names = {f.name: f.passed for f in v.filters}
    assert names["regular_session_before_cutoff"] is False


def test_cutoff_1pm_chicago_blocks_new_entries():
    snap = make_snap()
    v = _scout(snap, entry_cutoff(SESSION))
    names = {f.name: f.passed for f in v.filters}
    assert names["regular_session_before_cutoff"] is False
    assert "13:00" in next(f.detail for f in v.filters if f.name == "regular_session_before_cutoff")
    v_ok = _scout(snap, chicago(12, 59))
    names_ok = {f.name: f.passed for f in v_ok.filters}
    assert names_ok["regular_session_before_cutoff"] is True
    assert "13:00" in next(f.detail for f in v_ok.filters if f.name == "regular_session_before_cutoff")
    still_open_at_11 = _scout(snap, chicago(11, 0))
    assert {f.name: f.passed for f in still_open_at_11.filters}["regular_session_before_cutoff"] is True


def test_not_on_watchlist_fails():
    snap = make_snap(on_watchlist=False)
    v = _scout(snap, chicago(10))
    assert not v.passed
    assert any(f.name == "on_watchlist" and not f.passed for f in v.filters)


def test_opening_range_must_be_complete():
    snap = make_snap()
    v = _scout(snap, regular_open(SESSION).replace(minute=40))
    names = {f.name: f.passed for f in v.filters}
    assert names["first_15m_complete"] is False
    assert regular_open(SESSION).hour == 8 and regular_open(SESSION).minute == 30


def test_opening_range_complete_at_845_chicago():
    snap = make_snap()
    done = opening_range_end(SESSION)
    assert done.hour == 8 and done.minute == 45
    v = _scout(snap, done)
    names = {f.name: f.passed for f in v.filters}
    assert names["first_15m_complete"] is True


def test_chase_uses_trigger_level_not_orh_alone():
    """When PMH > ORH, 0.8×ADR chase is measured from max(PMH, ORH)."""
    snap = make_snap(
        last=Decimal("189.00"),
        adr=Decimal("4.00"),
        five=orb_five_min(or_high=Decimal("185"), trigger_close=Decimal("184.50"), trigger_low=Decimal("184.00")),
        premarket_high=Decimal("190"),
    )
    v = _scout(snap, chicago(10, 5), confirm=None)
    assert v.trigger_level == Decimal("190")
    # 189 < 190 + 0.8*4 = 193.2, so not a chase vs trigger even though it would
    # have been a chase vs ORH 185 + 3.2 = 188.2.
    names = {f.name: f.passed for f in v.filters}
    assert names["not_a_chase"] is True


def test_merge_scout_universe_includes_top_gainers_uo_is_catalyst_only():
    from simple_gains.config import SCOUT_UNIVERSE_CAP, SOURCE_TOP_GAINERS
    from simple_gains.lanes.scout import merge_scout_universe

    rows = merge_scout_universe(
        {
            "most_active": ["AAA", "BBB"],
            "unusual_volume": ["CCC"],
            "top_gainers": ["DDD", "AAA"],
            "unusual_options": ["EEE", "AAA"],
        }
    )
    tickers = [r.ticker for r in rows]
    assert "DDD" in tickers
    assert rows[tickers.index("DDD")].source == SOURCE_TOP_GAINERS
    assert rows[tickers.index("DDD")].role == "hunt"
    assert "EEE" not in tickers  # unusual options does not fill a hunt slot
    assert "AAA" in tickers
    assert tickers.count("AAA") == 1

    overflow = merge_scout_universe(
        {
            "most_active": [f"M{i}" for i in range(8)],
            "unusual_volume": [f"U{i}" for i in range(4)],
            "top_gainers": [f"G{i}" for i in range(10)],
            "unusual_options": ["OPT"],
        }
    )
    assert len(overflow) == SCOUT_UNIVERSE_CAP
    assert all(n.role == "hunt" for n in overflow)
    assert "OPT" not in [n.ticker for n in overflow]
    # 8 MA + 4 UV = 12; remaining hunt slots come from Top Gainers.
    hunt_sources = {n.source for n in overflow}
    assert SOURCE_TOP_GAINERS in hunt_sources
    assert sum(1 for n in overflow if n.source == SOURCE_TOP_GAINERS) == 3
