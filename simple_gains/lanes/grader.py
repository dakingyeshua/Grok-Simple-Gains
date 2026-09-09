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
    CARD_SOURCE_DESK,
    CARD_SOURCE_ENGINE,
    BucketScores,
    Decision,
    GradeContract,
    GraderCard,
    IncompleteGraderCard,
    MarketSnapshot,
    PathSplitError,
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


def capture_contract(snap: MarketSnapshot, scout: ScoutVerdict, *, source: str = CARD_SOURCE_ENGINE) -> GradeContract:
    """Freeze the exact inputs Grader reads. This is the shared resource contract."""
    bar = scout.confirmation
    prior: list[int] = []
    if bar is not None and snap.five_min:
        prior = [c.volume for c in snap.five_min if c.ts < bar.ts][-20:]
    return GradeContract(
        ticker=snap.ticker,
        session=snap.session,
        pattern_hint=snap.pattern_hint or "",
        level_note=snap.level_note or "",
        hitl_level_override=snap.hitl_level_override,
        prior_day_low=snap.prior_day_low,
        last_daily_close=snap.daily[-1].close if snap.daily else None,
        quote_last=snap.quote.last,
        session_open=snap.five_min[0].open if snap.five_min else None,
        spy_session_ret=snap.spy_session_ret,
        qqq_session_ret=snap.qqq_session_ret,
        is_nasdaq=snap.profile.is_nasdaq,
        confirmation=bar,
        has_five_min=bool(snap.five_min),
        prior_volumes=prior,
        has_catalyst=snap.has_catalyst,
        catalyst_note=snap.catalyst_note or "",
        hitl_catalyst_override=snap.hitl_catalyst_override,
        daily_20_ema=snap.daily_20_ema,
        opening_range=scout.opening_range,
        adr=snap.adr,
        premarket_high=scout.premarket_high,
        trigger_level=scout.trigger_level,
        theme=snap.profile.theme,
        sector=snap.profile.sector,
        source=source,
    )


def apply_contract(snap: MarketSnapshot, contract: GradeContract) -> MarketSnapshot:
    """Overlay desk/engine GradeContract fields onto a live snapshot. Never invent a close."""
    updates: dict = {}
    if contract.pattern_hint:
        updates["pattern_hint"] = contract.pattern_hint
    if contract.level_note:
        updates["level_note"] = contract.level_note
    if contract.hitl_level_override is not None:
        updates["hitl_level_override"] = contract.hitl_level_override
    if contract.hitl_catalyst_override is not None:
        updates["hitl_catalyst_override"] = contract.hitl_catalyst_override
    if contract.catalyst_note:
        updates["has_catalyst"] = True
        updates["catalyst_note"] = contract.catalyst_note
    elif contract.has_catalyst:
        updates["has_catalyst"] = True
    if contract.adr > 0:
        updates["adr"] = contract.adr
    if contract.daily_20_ema is not None:
        updates["daily_20_ema"] = contract.daily_20_ema
    if contract.prior_day_low is not None:
        updates["prior_day_low"] = contract.prior_day_low
    if contract.premarket_high is not None:
        updates["premarket_high"] = contract.premarket_high
    if contract.spy_session_ret:
        updates["spy_session_ret"] = contract.spy_session_ret
    if contract.qqq_session_ret:
        updates["qqq_session_ret"] = contract.qqq_session_ret
    return snap.model_copy(update=updates) if updates else snap


def apply_contract_to_verdict(scout: ScoutVerdict, contract: GradeContract) -> ScoutVerdict:
    updates: dict = {}
    if contract.opening_range is not None:
        updates["opening_range"] = contract.opening_range
    if contract.confirmation is not None and scout.confirmation is None:
        updates["confirmation"] = contract.confirmation
    if contract.premarket_high is not None:
        updates["premarket_high"] = contract.premarket_high
    if contract.trigger_level is not None:
        updates["trigger_level"] = contract.trigger_level
    return scout.model_copy(update=updates) if updates else scout


def cards_equivalent(left: GraderCard, right: GraderCard) -> bool:
    return (
        left.ticker == right.ticker
        and left.date == right.date
        and left.buckets.as_dict() == right.buckets.as_dict()
        and left.total == right.total
        and left.tier == right.tier
        and left.decision == right.decision
        and left.mapped_risk_pct == right.mapped_risk_pct
    )


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

        contract = capture_contract(snap, scout)
        return self.score_contract(
            contract,
            pre_filter_pass_list=list(scout.passed_names),
            s_tier_already=s_tier_already,
            session_label=session_label,
            notes=self._notes(snap, scout),
        )

    def score_contract(
        self,
        contract: GradeContract,
        *,
        pre_filter_pass_list: list[str],
        s_tier_already: int = 0,
        session_label: str = "RTH",
        notes: str = "",
        source: str | None = None,
    ) -> GraderCard:
        buckets = self.buckets_from_contract(contract)
        return self.card_from_buckets(
            ticker=contract.ticker,
            date=contract.session,
            buckets=buckets,
            pre_filter_pass_list=pre_filter_pass_list,
            theme=contract.theme,
            sector=contract.sector,
            spy_qqq_headwind_note=self._headwind_note_contract(contract),
            s_tier_already=s_tier_already,
            session_label=session_label,
            notes=notes,
            source=source or contract.source or CARD_SOURCE_ENGINE,
            contract=contract,
        )

    def card_from_buckets(
        self,
        *,
        ticker: str,
        date,
        buckets: BucketScores,
        pre_filter_pass_list: list[str],
        theme: str,
        sector: str,
        spy_qqq_headwind_note: str = "",
        notes: str = "",
        s_tier_already: int = 0,
        session_label: str = "RTH",
        source: str = CARD_SOURCE_DESK,
        contract: GradeContract | None = None,
        decision: Decision | None = None,
    ) -> GraderCard:
        """Single card constructor. Desk ingest and engine score both land here."""
        total = int(Decimal(buckets.capped_total()).to_integral_value(rounding=ROUND_DOWN))
        tier = tier_for_total(total)
        card = GraderCard(
            ticker=ticker.upper(),
            date=date,
            session=session_label,
            pre_filter_pass_list=list(pre_filter_pass_list),
            buckets=buckets,
            total=total,
            tier=tier,
            mapped_risk_pct=mapped_risk(tier),
            theme=theme,
            sector=sector,
            spy_qqq_headwind_note=spy_qqq_headwind_note,
            decision=decision if decision is not None else decision_for_tier(tier),
            s_tier_session_flag=tier == "S" and (s_tier_already + 1) >= S_TIER_FLAG_COUNT,
            notes=notes,
            source=source,
            contract=contract,
        )
        self.validate_card(card)
        return card

    def reconcile(self, stored: GraderCard, computed: GraderCard) -> GraderCard:
        """If a competing card appears, keep stored (desk) and raise PATH SPLIT."""
        self.validate_card(stored)
        self.validate_card(computed)
        if cards_equivalent(stored, computed):
            return stored
        raise PathSplitError(stored, computed)

    def buckets_from_contract(self, contract: GradeContract) -> BucketScores:
        return BucketScores(
            level_pattern=self._level_pattern_contract(contract),
            rs_vs_spy=self._rs_contract(contract),
            volume=self._volume_contract(contract),
            catalyst=self._catalyst_contract(contract),
            daily_20_ema=self._ema_contract(contract),
            opening_range_quality=self._or_quality_contract(contract),
        )

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
        return self.buckets_from_contract(capture_contract(snap, scout))

    def _level_pattern(self, snap: MarketSnapshot) -> int:
        return self._level_pattern_contract(capture_contract(snap, ScoutVerdict(
            ticker=snap.ticker, session=snap.session, passed=True, filters=[]
        )))

    def _level_pattern_contract(self, contract: GradeContract) -> int:
        if contract.hitl_level_override is not None:
            return min(int(contract.hitl_level_override), BUCKET_MAX["level_pattern"])
        score = 0
        hint = (contract.pattern_hint or "").lower()
        if "inverted head" in hint or "inv h&s" in hint or "ihs" in hint:
            score += 16
        elif "cup" in hint and "handle" in hint:
            score += 14
        if contract.level_note:
            score += 8
        elif contract.prior_day_low is not None and contract.last_daily_close is not None:
            last = contract.last_daily_close
            pdl = contract.prior_day_low
            if pdl > 0 and abs(last - pdl) / pdl <= Decimal("0.008"):
                score += 6
        return min(score, BUCKET_MAX["level_pattern"])

    def _rs(self, snap: MarketSnapshot) -> int:
        return self._rs_contract(capture_contract(snap, ScoutVerdict(
            ticker=snap.ticker, session=snap.session, passed=True, filters=[]
        )))

    def _rs_contract(self, contract: GradeContract) -> int:
        ret = self._stock_session_ret_contract(contract)
        rs_spy = ret - contract.spy_session_ret
        points = self._rs_points(rs_spy)
        if contract.is_nasdaq:
            rs_qqq = ret - contract.qqq_session_ret
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
        return self._stock_session_ret_contract(capture_contract(snap, ScoutVerdict(
            ticker=snap.ticker, session=snap.session, passed=True, filters=[]
        )))

    def _stock_session_ret_contract(self, contract: GradeContract) -> Decimal:
        o = contract.session_open
        if o is None or o <= 0:
            return Decimal("0")
        return (contract.quote_last - o) / o

    def _volume(self, snap: MarketSnapshot, scout: ScoutVerdict) -> int:
        return self._volume_contract(capture_contract(snap, scout))

    def _volume_contract(self, contract: GradeContract) -> int:
        bar = contract.confirmation
        if bar is None or not contract.has_five_min:
            return 0
        prior = contract.prior_volumes
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
        return self._catalyst_contract(capture_contract(snap, ScoutVerdict(
            ticker=snap.ticker, session=snap.session, passed=True, filters=[]
        )))

    def _catalyst_contract(self, contract: GradeContract) -> int:
        if contract.hitl_catalyst_override is not None:
            return min(int(contract.hitl_catalyst_override), BUCKET_MAX["catalyst"])
        if contract.has_catalyst:
            note = (contract.catalyst_note or "").lower()
            if "earnings" in note or "guidance" in note:
                return 15
            if "upgrade" in note or "contract" in note:
                return 12
            return 8
        return 0

    def _ema(self, snap: MarketSnapshot) -> int:
        return self._ema_contract(capture_contract(snap, ScoutVerdict(
            ticker=snap.ticker, session=snap.session, passed=True, filters=[]
        )))

    def _ema_contract(self, contract: GradeContract) -> int:
        ema = contract.daily_20_ema
        close = contract.last_daily_close
        if ema is None or ema <= 0 or close is None:
            return 0
        if close < ema:
            return 0
        ext = (close - ema) / ema
        if ext <= Decimal("0.04"):
            return 10
        if ext <= Decimal("0.08"):
            return 6
        return 3

    def _or_quality(self, snap: MarketSnapshot, scout: ScoutVerdict) -> int:
        return self._or_quality_contract(capture_contract(snap, scout))

    def _or_quality_contract(self, contract: GradeContract) -> int:
        or_bar = contract.opening_range
        if or_bar is None or contract.adr <= 0:
            return 0
        width = or_bar.high - or_bar.low
        if width <= 0:
            return 4
        frac = width / contract.adr
        if frac <= Decimal("0.20"):
            return 10
        if frac <= Decimal("0.35"):
            return 7
        if frac <= Decimal("0.50"):
            return 4
        return 1

    def _headwind_note(self, snap: MarketSnapshot) -> str:
        return self._headwind_note_contract(capture_contract(snap, ScoutVerdict(
            ticker=snap.ticker, session=snap.session, passed=True, filters=[]
        )))

    def _headwind_note_contract(self, contract: GradeContract) -> str:
        bits = [f"SPY session {contract.spy_session_ret:.2%}"]
        if contract.is_nasdaq:
            bits.append(f"QQQ session {contract.qqq_session_ret:.2%}")
        if contract.spy_session_ret <= Decimal("-0.004"):
            bits.append("SPY headwind")
        if contract.is_nasdaq and contract.qqq_session_ret <= Decimal("-0.004"):
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
