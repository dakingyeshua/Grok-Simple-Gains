from decimal import Decimal

import pytest

from simple_gains.config import SKIP_BELOW, TIER_A, TIER_A_PLUS, TIER_RISK_PCT, TIER_S
from simple_gains.lanes.grader import Grader, decision_for_tier, mapped_risk, tier_for_total
from simple_gains.lanes.risk import RiskOfficer
from simple_gains.lanes.scout import Scout
from simple_gains.models import AccountState, BreakerState, BucketScores, Decision, IncompleteGraderCard
from tests.conftest import chicago, make_card, make_snap, orb_five_min


@pytest.mark.parametrize(
    "total,tier,risk",
    [
        (79, "skip", Decimal("0")),
        (80, "A", Decimal("0.010")),
        (84, "A", Decimal("0.010")),
        (85, "A+", Decimal("0.015")),
        (89, "A+", Decimal("0.015")),
        (90, "A+", Decimal("0.015")),
        (94, "A+", Decimal("0.015")),
        (95, "S", Decimal("0.020")),
        (100, "S", Decimal("0.020")),
    ],
)
def test_locked_tier_risk_map(total, tier, risk):
    assert tier_for_total(total) == tier
    assert mapped_risk(tier) == risk
    assert mapped_risk(tier) == TIER_RISK_PCT[tier]
    if total < SKIP_BELOW:
        assert decision_for_tier(tier) == Decision.SKIP


def test_never_round_up_below_80():
    assert SKIP_BELOW == 80
    assert tier_for_total(79) == "skip"
    assert mapped_risk("skip") == Decimal("0")
    assert decision_for_tier("skip") == Decision.SKIP


def test_constitution_tier_ranges_match_desk_map():
    assert list(TIER_A) == list(range(80, 85))
    assert list(TIER_A_PLUS) == list(range(85, 95))
    assert list(TIER_S) == list(range(95, 101))
    assert 79 not in TIER_A
    assert 80 in TIER_A and 84 in TIER_A
    assert 85 in TIER_A_PLUS and 94 in TIER_A_PLUS
    assert 95 in TIER_S


def test_grader_rejects_rounding_up_below_80():
    grader = Grader()
    card = make_card(total=79, decision=Decision.A)
    with pytest.raises(IncompleteGraderCard, match="below 80 must be skip"):
        grader.validate_card(card)


def _review(card):
    officer = RiskOfficer()
    snap = make_snap()
    five = orb_five_min()
    verdict = Scout().evaluate(
        snap, chicago(10), open_position_count=0, already_open_ticker=False, confirmation=five[-1]
    )
    verdict.confirmation = five[-1]
    verdict.passed = True
    acct = AccountState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        starting_equity=Decimal("100000"),
        high_water=Decimal("100000"),
        mode="paper",
    )
    return officer.review(card, verdict, snap, acct, BreakerState(), [], already_vetoed=None)


def test_risk_officer_skips_below_80():
    card = make_card(total=79)
    assert card.tier == "skip"
    assert card.mapped_risk_pct == Decimal("0")
    decision = _review(card)
    assert not decision.accepted
    assert decision.skip_reason == "below_80_or_skip_tier"


def test_risk_officer_sizes_80_as_a_tier():
    card = make_card(total=80)
    Grader().validate_card(card)
    assert card.tier == "A"
    assert card.mapped_risk_pct == Decimal("0.010")
    decision = _review(card)
    assert decision.accepted
    assert decision.planned_risk_pct == Decimal("0.010")


def test_risk_officer_sizes_84_as_a_tier_not_skip():
    """84 was skip under the retired 85-bar. v1.4 maps 80–84 to A 1.0%."""
    card = make_card(total=84)
    Grader().validate_card(card)
    assert card.tier == "A"
    assert card.mapped_risk_pct == Decimal("0.010")
    decision = _review(card)
    assert decision.accepted
    assert decision.planned_risk_pct == Decimal("0.010")


def test_risk_officer_sizes_85_as_a_plus():
    card = make_card(total=85)
    Grader().validate_card(card)
    assert card.tier == "A+"
    assert card.mapped_risk_pct == Decimal("0.015")
    decision = _review(card)
    assert decision.accepted
    assert decision.planned_risk_pct == Decimal("0.015")


def test_grader_rejects_incomplete_bucket_card():
    grader = Grader()
    card = make_card(total=90)
    # Drop a bucket by constructing an invalid payload via model_copy + extra validation
    with pytest.raises(IncompleteGraderCard):
        broken = card.model_copy()
        broken.buckets = BucketScores(
            level_pattern=25,
            rs_vs_spy=20,
            volume=20,
            catalyst=15,
            daily_20_ema=10,
            opening_range_quality=20,  # exceeds locked max of 10
        )
        broken.total = 110
        grader.validate_card(broken)


def test_grader_rejects_total_mismatch():
    grader = Grader()
    card = make_card(total=90)
    card.total = 99
    with pytest.raises(IncompleteGraderCard, match="does not match"):
        grader.validate_card(card)
