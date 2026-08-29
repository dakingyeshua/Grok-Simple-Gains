from decimal import Decimal

import pytest

from simple_gains.lanes.grader import Grader, GraderError
from simple_gains.lanes.scout import Scout
from simple_gains.models import Decision, IncompleteGraderCard
from tests.conftest import chicago, make_card, make_snap, orb_five_min


def test_grader_refuses_to_score_scout_fail():
    snap = make_snap(on_watchlist=False)
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=None
    )
    assert not verdict.passed
    with pytest.raises(GraderError, match="will not score"):
        Grader().score(snap, verdict)


def test_grader_mechanical_card_has_six_buckets_and_locked_map():
    snap = make_snap()
    five = orb_five_min()
    confirm = five[-1]
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=confirm
    )
    assert verdict.passed
    card = Grader().score(snap, verdict)
    assert set(card.buckets.as_dict()) == {
        "level_pattern",
        "rs_vs_spy",
        "volume",
        "catalyst",
        "daily_20_ema",
        "opening_range_quality",
    }
    Grader().validate_card(card)
    if card.total < 85:
        assert card.decision == Decision.SKIP
        assert card.mapped_risk_pct == Decimal("0")


def test_s_tier_flag_at_three_names():
    snap = make_snap()
    five = orb_five_min()
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=five[-1]
    )
    card = Grader().score(snap, verdict, s_tier_already=2)
    # flag is only meaningful when the card itself is S; still stored
    if card.tier == "S":
        assert card.s_tier_session_flag is True


def test_incomplete_card_rejected_by_grader():
    card = make_card()
    card.total = 80
    with pytest.raises(IncompleteGraderCard):
        Grader().validate_card(card)
