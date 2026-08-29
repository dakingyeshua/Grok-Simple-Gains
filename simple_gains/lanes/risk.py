"""Risk Officer: size + gates + veto. Does not hunt. Does not score.

A veto is final for that setup that day. Grader cannot rescore it into a pass.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from simple_gains.clock import is_session_day, monday_of
from simple_gains.config import (
    BOOK_RISK_CAP_PCT,
    DAILY_STOP_PCT,
    EMA_HANDOFF_EXT_PCT,
    EMA_HANDOFF_R,
    MAX_NOTIONAL_PCT,
    MIN_STOP_DOLLARS,
    MIN_STOP_PCT_OF_ENTRY,
    ONE_R_FLOOR,
    PLANNING_SLIPPAGE_BUFFER_BPS,
    PORTFOLIO_STOP_PCT,
    RISK_CEILING_PCT,
    STREAK_LOSS_LIMIT,
    THEME_NAME_CAP,
    TWO_R,
    VIOLENT_TAPE_SESSION_PCT,
    WEEKLY_STOP_PCT,
)
from simple_gains.lanes.grader import Grader
from simple_gains.models import (
    AccountState,
    BreakerState,
    Decision,
    Fill,
    GraderCard,
    IncompleteGraderCard,
    MarketSnapshot,
    Position,
    RiskDecision,
    ScoutVerdict,
)


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def planned_stop_distance(entry: Decimal, initial_stop: Decimal, buffer_bps: int = PLANNING_SLIPPAGE_BUFFER_BPS) -> Decimal:
    raw = entry - initial_stop
    buffer = entry * Decimal(buffer_bps) / Decimal("10000")
    return raw + buffer


def min_stop_distance(entry: Decimal) -> Decimal:
    return max(MIN_STOP_DOLLARS, entry * MIN_STOP_PCT_OF_ENTRY)


def size_shares(equity: Decimal, risk_pct: Decimal, entry: Decimal, initial_stop: Decimal) -> int:
    """shares = (equity × risk%) ÷ (entry − initial stop) with slippage buffer.

    Size is off equity, never buying power.
    """
    if equity <= 0 or risk_pct <= 0 or entry <= initial_stop:
        return 0
    dist = planned_stop_distance(entry, initial_stop)
    if dist <= 0:
        return 0
    dollars = equity * risk_pct
    shares = (dollars / dist).to_integral_value(rounding=ROUND_DOWN)
    return max(int(shares), 0)


def apply_sanity_cap(shares: int, equity: Decimal, entry: Decimal) -> int:
    if entry <= 0 or shares <= 0:
        return 0
    max_notional = equity * MAX_NOTIONAL_PCT
    capped = (max_notional / entry).to_integral_value(rounding=ROUND_DOWN)
    return min(shares, int(capped))


def post_fill_cut(shares: int, equity: Decimal, fill: Decimal, initial_stop: Decimal) -> tuple[int, str]:
    """Recalc on actual fill. If real risk exceeds the 2% ceiling, cut size immediately."""
    if shares <= 0 or fill <= initial_stop or equity <= 0:
        return 0, "invalid_fill_risk"
    risk_per_share = fill - initial_stop
    real = risk_per_share * Decimal(shares)
    ceiling = equity * RISK_CEILING_PCT
    if real <= ceiling:
        return shares, ""
    cut = (ceiling / risk_per_share).to_integral_value(rounding=ROUND_DOWN)
    return max(int(cut), 0), "post_fill_2pct_ceiling"


def open_book_risk(positions: list[Position]) -> Decimal:
    return sum((p.open_risk_dollars for p in positions), Decimal("0"))


def theme_count(positions: list[Position], theme: str) -> int:
    key = (theme or "other").lower()
    return sum(1 for p in positions if not p.closed and (p.theme or "other").lower() == key)


def violent_against_tape(snap: MarketSnapshot) -> bool:
    """No new longs if SPY (and QQQ for NASDAQ names) is violently against.

    Testable rule: index session return from the system's regular open is
    <= −0.60% AND the latest completed 5-minute candle on that index is red.
    """
    spy_violent = snap.spy_session_ret <= VIOLENT_TAPE_SESSION_PCT and snap.spy_last_5m_red
    if spy_violent:
        return True
    if snap.profile.is_nasdaq:
        return snap.qqq_session_ret <= VIOLENT_TAPE_SESSION_PCT and snap.qqq_last_5m_red
    return False


def next_stop(
    position: Position,
    last_price: Decimal,
    prior_day_low: Decimal | None,
    daily_20_ema: Decimal | None,
) -> tuple[Decimal, str]:
    """Mechanical trail. Stop only tightens. Full size, no scale-out.

    Stages:
      1. entry-candle low (initial)
      2. prior-day low once last price is above the initial stop and PDL > initial
      3. daily 20 EMA once extended (R >= 1.5 or price >= EMA × 1.012)
      4. once +2R, live stop may not sit below +1R
    """
    stage = "entry_candle_low"
    candidate = position.initial_stop

    if prior_day_low is not None and last_price > position.initial_stop and prior_day_low > position.initial_stop:
        if prior_day_low > candidate:
            candidate = prior_day_low
            stage = "prior_day_low"

    extended = position.unrealized_r >= EMA_HANDOFF_R
    if daily_20_ema is not None and daily_20_ema > 0:
        if last_price >= daily_20_ema * (Decimal("1") + EMA_HANDOFF_EXT_PCT):
            extended = True
    if extended and daily_20_ema is not None and daily_20_ema > candidate:
        candidate = daily_20_ema
        stage = "daily_20_ema"

    if position.r_value > 0:
        r_now = (last_price - position.fill_price) / position.r_value
        if r_now >= TWO_R:
            floor = position.fill_price + position.r_value * ONE_R_FLOOR
            if candidate < floor:
                candidate = floor
                stage = "plus_1r_floor"

    # Stop only tightens (longs: stop may only rise).
    if candidate < position.live_stop:
        candidate = position.live_stop
        stage = position.stop_stage
    return _q(candidate), stage


class RiskOfficer:
    def __init__(self, grader: Grader | None = None) -> None:
        self.grader = grader or Grader()

    def review(
        self,
        card: GraderCard,
        scout: ScoutVerdict,
        snap: MarketSnapshot,
        account: AccountState,
        breakers: BreakerState,
        open_positions: list[Position],
        *,
        already_vetoed: str | None,
        buying_power: Decimal | None = None,  # accepted only to prove we ignore it
    ) -> RiskDecision:
        del buying_power  # size off equity, never buying power
        try:
            self.grader.validate_card(card)
        except IncompleteGraderCard as exc:
            return RiskDecision(accepted=False, veto=True, veto_reason=f"incomplete_card:{exc}")

        if already_vetoed:
            return RiskDecision(
                accepted=False,
                veto=True,
                veto_reason=f"veto_stands:{already_vetoed}",
            )

        if card.decision == Decision.SKIP or card.total < 85 or card.mapped_risk_pct <= 0:
            return RiskDecision(accepted=False, skip_reason="below_85_or_skip_tier")

        blocked, why = breakers.new_entries_blocked(card.date)
        if blocked:
            return RiskDecision(accepted=False, skip_reason=f"breaker:{why}")

        if violent_against_tape(snap):
            return RiskDecision(
                accepted=False,
                veto=True,
                veto_reason="violent_against_tape",
            )

        confirm = scout.confirmation
        if confirm is None:
            return RiskDecision(accepted=False, skip_reason="no_confirmation_candle")
        entry = confirm.close
        stop = confirm.low
        if entry <= stop:
            return RiskDecision(accepted=False, veto=True, veto_reason="invalid_stop_vs_entry")

        dist = planned_stop_distance(entry, stop)
        if dist < min_stop_distance(entry):
            return RiskDecision(
                accepted=False,
                veto=True,
                veto_reason="micro_stop_sanity_cap",
            )

        if theme_count(open_positions, card.theme) >= THEME_NAME_CAP:
            return RiskDecision(
                accepted=False,
                skip_reason=f"theme_cap:{card.theme}",
            )

        shares = size_shares(account.equity, card.mapped_risk_pct, entry, stop)
        shares = apply_sanity_cap(shares, account.equity, entry)
        if shares <= 0:
            return RiskDecision(accepted=False, skip_reason="zero_shares_after_size")

        planned_risk = (entry - stop) * Decimal(shares)
        # Use planned distance including buffer for book-cap projection
        projected_risk = dist * Decimal(shares)
        existing = open_book_risk(open_positions)
        cap = account.equity * BOOK_RISK_CAP_PCT
        if existing + projected_risk > cap:
            return RiskDecision(accepted=False, skip_reason="book_risk_cap_6pct")

        r_value = entry - stop
        return RiskDecision(
            accepted=True,
            shares=shares,
            planned_entry=entry,
            planned_stop=stop,
            planned_risk_dollars=_q(planned_risk),
            planned_risk_pct=card.mapped_risk_pct,
            r_value=r_value,
        )

    def apply_fill_cut(self, decision: RiskDecision, fill: Fill, equity: Decimal) -> RiskDecision:
        shares, reason = post_fill_cut(decision.shares, equity, fill.price, decision.planned_stop)
        updated = decision.model_copy()
        updated.shares = shares
        updated.cut_reason = reason
        if shares <= 0:
            updated.accepted = False
            updated.skip_reason = reason or "cut_to_zero"
        if fill.price > decision.planned_stop:
            updated.r_value = fill.price - decision.planned_stop
            updated.planned_risk_dollars = _q((fill.price - decision.planned_stop) * Decimal(shares))
        return updated

    def update_breakers(
        self,
        breakers: BreakerState,
        account: AccountState,
        open_positions: list[Position],
        session: date,
        *,
        just_closed_loss: bool | None = None,
        flatten_equity: Decimal | None = None,
    ) -> list[str]:
        """Apply daily / streak / weekly / portfolio breakers. Returns new event names.

        Daily and streak halt NEW entries only. Portfolio −20% flattens all
        and requires an explicit re-authorize.
        """
        events: list[str] = []
        equity = flatten_equity if flatten_equity is not None else account.equity
        marked = equity  # caller must pass mark-to-market equity (cash + last * shares)
        if breakers.sod_date != session:
            # New session: honor next-session-off, then roll SOD.
            if breakers.next_session_off and breakers.next_session_off_date == session:
                breakers.daily_halt = True
                breakers.daily_reason = "next_session_off_after_day_stop"
            else:
                breakers.daily_halt = False
                breakers.daily_reason = ""
            breakers.sod_date = session
            breakers.sod_equity = marked
            breakers.consecutive_closed_losses = (
                breakers.consecutive_closed_losses if breakers.next_session_off_date == session else 0
            )
            if breakers.next_session_off_date is not None and session > breakers.next_session_off_date:
                breakers.next_session_off = False
                breakers.next_session_off_date = None
            breakers.s_tier_count = 0
            mon = monday_of(session)
            if breakers.weekly_anchor_monday != mon:
                breakers.weekly_anchor_monday = mon
                breakers.weekly_anchor_equity = marked
                breakers.weekly_halt = False
                breakers.weekly_review_required = False
                breakers.weekly_review_filed = False

        if breakers.high_water_equity <= 0:
            breakers.high_water_equity = account.high_water
        if marked > breakers.high_water_equity:
            breakers.high_water_equity = marked

        sod = breakers.sod_equity or marked
        # Daily −10% from SOD including open P&L (marked already includes it if caller MTM'd).
        if sod > 0 and (sod - marked) / sod >= DAILY_STOP_PCT:
            if not breakers.daily_halt:
                events.append("daily_stop")
            breakers.daily_halt = True
            breakers.daily_reason = "daily_minus_10pct"
            breakers.next_session_off = True
            nxt = session + timedelta(days=1)
            while not is_session_day(nxt):
                nxt += timedelta(days=1)
            breakers.next_session_off_date = nxt
            breakers.postmortem_required = True
            breakers.postmortem_filed = False

        if just_closed_loss is True:
            breakers.consecutive_closed_losses += 1
            if breakers.consecutive_closed_losses >= STREAK_LOSS_LIMIT:
                if not (breakers.daily_halt and breakers.daily_reason == "two_consecutive_losses"):
                    events.append("streak_stop")
                breakers.daily_halt = True
                breakers.daily_reason = "two_consecutive_losses"
                breakers.next_session_off = True
                nxt = session + timedelta(days=1)
                while not is_session_day(nxt):
                    nxt += timedelta(days=1)
                breakers.next_session_off_date = nxt
                breakers.postmortem_required = True
                breakers.postmortem_filed = False
        elif just_closed_loss is False:
            breakers.consecutive_closed_losses = 0

        weekly_anchor = breakers.weekly_anchor_equity or marked
        if weekly_anchor > 0 and (weekly_anchor - marked) / weekly_anchor >= WEEKLY_STOP_PCT:
            if not breakers.weekly_halt:
                events.append("weekly_stop")
            breakers.weekly_halt = True
            breakers.weekly_review_required = True

        hw = breakers.high_water_equity or account.high_water
        if hw > 0 and (hw - marked) / hw >= PORTFOLIO_STOP_PCT:
            if not breakers.portfolio_halt:
                events.append("portfolio_stop")
            breakers.portfolio_halt = True
            breakers.portfolio_flatten = True
            breakers.reauthorize_required = True

        return events

    def file_postmortem(self, breakers: BreakerState) -> BreakerState:
        breakers.postmortem_filed = True
        return breakers

    def file_weekly_review(self, breakers: BreakerState) -> BreakerState:
        breakers.weekly_review_filed = True
        breakers.weekly_halt = False
        return breakers

    def reauthorize(self, breakers: BreakerState, high_water: Decimal) -> BreakerState:
        breakers.portfolio_halt = False
        breakers.portfolio_flatten = False
        breakers.reauthorize_required = False
        breakers.high_water_equity = high_water
        return breakers
