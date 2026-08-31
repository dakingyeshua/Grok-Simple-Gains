"""Grader: 100-point conviction on Scout survivors only. Does not hunt. Does not size."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from simple_gains.config import (
    BUCKET_MAX,
    REQUIRED_BUCKETS,
    S_TIER_FLAG_COUNT,
    SKIP_BELOW,
    TIER_RISK_PCT,
)
from simple_gains.models import (
    BucketScores,
    Decision,
    GraderCard,
    IncompleteGraderCard,
    MarketSnapshot,
    ScoutVerdict,
)


class GraderError(ValueError):
    pass


def tier_for_total(total: int) -> str:
    """Locked map. Totals below 85 never round up to a trade."""
    if total < SKIP_BELOW:
        return "skip"
    if total <= 89:
        return "A"
    if total <= 94:
        return "A+"
    return "S"


def decision_for_tier(tier: str) -> Decision:
    return {
        "skip": Decision.SKIP,
        "A": Decision.A,
        "A+": Decision.A_PLUS,
        "S": Decision.S,
    }[tier]


def mapped_risk(tier: str) -> Decimal:
    return TIER_RISK_PCT[tier]


class Grader:
    def score(
        self,
        snap: MarketSnapshot,
        scout: ScoutVerdict,
        *,
        s_tier_already: int = 0,
        session_label: str = "RTH",
    ) -> GraderCard:
        if not scout.passed:
            raise GraderError("Grader does not hunt and will not score a Scout fail")
        if scout.ticker != snap.ticker or scout.session != snap.session:
            raise GraderError("Scout verdict does not match snapshot")

        buckets = self._mechanical_buckets(snap, scout)
        total = buckets.capped_total()
        # never round up
        total = int(Decimal(total).to_integral_value(rounding=ROUND_DOWN))
        tier = tier_for_total(total)
        card = GraderCard(
            ticker=snap.ticker,
            date=snap.session,
            session=session_label,
            pre_filter_pass_list=list(scout.passed_names),
            buckets=buckets,
            total=total,
            tier=tier,
            mapped_risk_pct=mapped_risk(tier),
            theme=snap.profile.theme,
            sector=snap.profile.sector,
            spy_qqq_headwind_note=self._headwind_note(snap),
            decision=decision_for_tier(tier),
            s_tier_session_flag=tier == "S" and (s_tier_already + 1) >= S_TIER_FLAG_COUNT,
            notes=self._notes(snap, scout),
        )
        self.validate_card(card)
        return card

    def validate_card(self, card: GraderCard) -> None:
        """Journal and Risk both call this. Incomplete six-bucket split is rejected."""
        data = card.buckets.as_dict()
        missing = [name for name in REQUIRED_BUCKETS if name not in data]
        if missing:
            raise IncompleteGraderCard(f"Grader card missing buckets: {missing}")
        for name, cap in BUCKET_MAX.items():
            val = data[name]
            if val is None:
                raise IncompleteGraderCard(f"bucket {name} is empty")
            if not isinstance(val, int):
                raise IncompleteGraderCard(f"bucket {name} must be an int")
            if val > cap:
                raise IncompleteGraderCard(f"bucket {name}={val} exceeds locked max {cap}")
        expected = min(sum(min(data[n], BUCKET_MAX[n]) for n in REQUIRED_BUCKETS), 100)
        if card.total != expected:
            raise IncompleteGraderCard(
                f"total {card.total} does not match six-bucket sum {expected}"
            )
        if card.total < SKIP_BELOW and card.decision != Decision.SKIP:
            raise IncompleteGraderCard("below 85 must be skip; never round up")
        if card.tier != tier_for_total(card.total):
            raise IncompleteGraderCard("tier does not match locked map")
        if card.mapped_risk_pct != mapped_risk(card.tier):
            raise IncompleteGraderCard("mapped risk % does not match locked tier map")

    def _mechanical_buckets(self, snap: MarketSnapshot, scout: ScoutVerdict) -> BucketScores:
        return BucketScores(
            level_pattern=self._level_pattern(snap),
            rs_vs_spy=self._rs(snap),
            volume=self._volume(snap, scout),
            catalyst=self._catalyst(snap),
            daily_20_ema=self._ema(snap),
            opening_range_quality=self._or_quality(snap, scout),
        )

    def _level_pattern(self, snap: MarketSnapshot) -> int:
        if snap.hitl_level_override is not None:
            return min(int(snap.hitl_level_override), BUCKET_MAX["level_pattern"])
        score = 0
        hint = (snap.pattern_hint or "").lower()
        if "inverted head" in hint or "inv h&s" in hint or "ihs" in hint:
            score += 16
        elif "cup" in hint and "handle" in hint:
            score += 14
        if snap.level_note:
            score += 8
        elif snap.prior_day_low is not None and snap.daily:
            last = snap.daily[-1].close
            pdl = snap.prior_day_low
            if pdl > 0 and abs(last - pdl) / pdl <= Decimal("0.008"):
                score += 6
        return min(score, BUCKET_MAX["level_pattern"])

    def _rs(self, snap: MarketSnapshot) -> int:
        rs_spy = self._stock_session_ret(snap) - snap.spy_session_ret
        points = self._rs_points(rs_spy)
        if snap.profile.is_nasdaq:
            rs_qqq = self._stock_session_ret(snap) - snap.qqq_session_ret
            points = (points + self._rs_points(rs_qqq)) // 2
        return min(points, BUCKET_MAX["rs_vs_spy"])

    def _rs_points(self, rs: Decimal) -> int:
        if rs >= Decimal("0.015"):
            return 20
        if rs >= Decimal("0.0075"):
            return 16
        if rs >= Decimal("0.003"):
            return 12
        if rs >= Decimal("0"):
            return 8
        if rs >= Decimal("-0.005"):
            return 4
        return 0

    def _stock_session_ret(self, snap: MarketSnapshot) -> Decimal:
        if not snap.five_min:
            return Decimal("0")
        o = snap.five_min[0].open
        if o <= 0:
            return Decimal("0")
        return (snap.quote.last - o) / o

    def _volume(self, snap: MarketSnapshot, scout: ScoutVerdict) -> int:
        bar = scout.confirmation
        if bar is None or not snap.five_min:
            return 0
        prior = [c.volume for c in snap.five_min if c.ts < bar.ts][-20:]
        if not prior:
            return 8 if bar.volume > 0 else 0
        avg = sum(prior) / len(prior)
        if avg <= 0:
            return 8
        ratio = bar.volume / avg
        if ratio >= 2.5:
            return 20
        if ratio >= 1.8:
            return 16
        if ratio >= 1.3:
            return 12
        if ratio >= 1.0:
            return 8
        return 4

    def _catalyst(self, snap: MarketSnapshot) -> int:
        if snap.hitl_catalyst_override is not None:
            return min(int(snap.hitl_catalyst_override), BUCKET_MAX["catalyst"])
        if snap.has_catalyst:
            note = (snap.catalyst_note or "").lower()
            if "earnings" in note or "guidance" in note:
                return 15
            if "upgrade" in note or "contract" in note:
                return 12
            return 8
        return 0

    def _ema(self, snap: MarketSnapshot) -> int:
        ema = snap.daily_20_ema
        if ema is None or ema <= 0 or not snap.daily:
            return 0
        close = snap.daily[-1].close
        if close < ema:
            return 0
        ext = (close - ema) / ema
        if ext <= Decimal("0.04"):
            return 10
        if ext <= Decimal("0.08"):
            return 6
        return 3

    def _or_quality(self, snap: MarketSnapshot, scout: ScoutVerdict) -> int:
        or_bar = scout.opening_range
        if or_bar is None or snap.adr <= 0:
            return 0
        width = or_bar.high - or_bar.low
        if width <= 0:
            return 4
        frac = width / snap.adr
        if frac <= Decimal("0.20"):
            return 10
        if frac <= Decimal("0.35"):
            return 7
        if frac <= Decimal("0.50"):
            return 4
        return 1

    def _headwind_note(self, snap: MarketSnapshot) -> str:
        bits = [f"SPY session {snap.spy_session_ret:.2%}"]
        if snap.profile.is_nasdaq:
            bits.append(f"QQQ session {snap.qqq_session_ret:.2%}")
        if snap.spy_session_ret <= Decimal("-0.004"):
            bits.append("SPY headwind")
        if snap.profile.is_nasdaq and snap.qqq_session_ret <= Decimal("-0.004"):
            bits.append("QQQ headwind")
        return "; ".join(bits)

    def _notes(self, snap: MarketSnapshot, scout: ScoutVerdict) -> str:
        parts = []
        if snap.pattern_hint:
            parts.append(snap.pattern_hint)
        if snap.level_note:
            parts.append(snap.level_note)
        if snap.catalyst_note:
            parts.append(snap.catalyst_note)
        if scout.opening_range:
            parts.append(f"ORH {scout.opening_range.high}")
        if scout.premarket_high is not None:
            parts.append(f"PMH {scout.premarket_high}")
        if scout.trigger_level is not None:
            parts.append(f"trigger {scout.trigger_level}")
        return " | ".join(parts)
