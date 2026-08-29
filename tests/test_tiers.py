from decimal import Decimal

import pytest

from simple_gains.config import SKIP_BELOW, TIER_RISK_PCT
from simple_gains.lanes.grader import Grader, decision_for_tier, mapped_risk, tier_for_total
from simple_gains.models import BucketScores, Decision, IncompleteGraderCard
from tests.conftest import make_card


@pytest.mark.parametrize(
    "total,tier,risk",
    [
        (84, "skip", Decimal("0")),
        (85, "A", Decimal("0.010")),
        (89, "A", Decimal("0.010")),
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


def test_never_round_up_below_85():
    assert tier_for_total(84) == "skip"
    assert mapped_risk("skip") == Decimal("0")


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
