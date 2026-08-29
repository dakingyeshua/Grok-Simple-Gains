"""Shared domain models. Lanes import these; they do not import each other."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from simple_gains.config import BUCKET_MAX, REQUIRED_BUCKETS


class Decision(str, Enum):
    S = "S"
    A_PLUS = "A+"
    A = "A"
    SKIP = "skip"


class JournalKind(str, Enum):
    SIGNAL = "signal"
    SKIP = "skip"
    FILL = "fill"
    STOP = "stop"
    TRAIL_HANDOFF = "trail_handoff"
    BREAKER = "breaker"
    VETO = "veto"
    ALERT = "alert"
    SCAN = "scan"
    POSTMORTEM = "postmortem"
    WEEKLY_REVIEW = "weekly_review"
    FLATTEN = "flatten"
    AUTHORIZE = "authorize"
    S_TIER_FLAG = "s_tier_flag"


class Candle(BaseModel):
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0

    @property
    def is_red(self) -> bool:
        return self.close < self.open

    @property
    def range(self) -> Decimal:
        return self.high - self.low


class Quote(BaseModel):
    ticker: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    halted: bool = False

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def spread_pct(self) -> Decimal:
        mid = self.mid
        if mid <= 0:
            return Decimal("100")
        return ((self.ask - self.bid) / mid) * Decimal("100")


class Profile(BaseModel):
    ticker: str
    exchange: str = ""
    sector: str = "other"
    theme: str = "other"
    is_nasdaq: bool = False


class MarketSnapshot(BaseModel):
    ticker: str
    session: date
    quote: Quote
    profile: Profile
    five_min: list[Candle] = Field(default_factory=list)
    fifteen_min: list[Candle] = Field(default_factory=list)
    daily: list[Candle] = Field(default_factory=list)
    adv_shares: int = 0
    adr: Decimal = Decimal("0")
    daily_20_ema: Decimal | None = None
    prior_day_low: Decimal | None = None
    has_catalyst: bool = False
    catalyst_note: str = ""
    on_watchlist: bool = False
    pattern_hint: str = ""
    level_note: str = ""
    spy_session_ret: Decimal = Decimal("0")
    qqq_session_ret: Decimal = Decimal("0")
    spy_last_5m_red: bool = False
    qqq_last_5m_red: bool = False
    hitl_level_override: int | None = None
    hitl_catalyst_override: int | None = None


class FilterResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class ScoutVerdict(BaseModel):
    ticker: str
    session: date
    passed: bool
    filters: list[FilterResult]
    opening_range: Candle | None = None
    confirmation: Candle | None = None
    stretch_above_orh: Decimal | None = None
    reason: str = ""

    @property
    def passed_names(self) -> list[str]:
        return [f.name for f in self.filters if f.passed]


class BucketScores(BaseModel):
    level_pattern: int
    rs_vs_spy: int
    volume: int
    catalyst: int
    daily_20_ema: int
    opening_range_quality: int

    @field_validator("*")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("bucket scores cannot be negative")
        return int(v)

    def as_dict(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in REQUIRED_BUCKETS}

    def capped_total(self) -> int:
        total = 0
        for name, raw in self.as_dict().items():
            total += min(int(raw), BUCKET_MAX[name])
        return total


class GraderCard(BaseModel):
    ticker: str
    date: date
    session: str
    pre_filter_pass_list: list[str]
    buckets: BucketScores
    total: int
    tier: str
    mapped_risk_pct: Decimal
    theme: str
    sector: str
    spy_qqq_headwind_note: str
    decision: Decision
    s_tier_session_flag: bool = False
    notes: str = ""

    def require_complete_buckets(self) -> None:
        missing = [name for name in REQUIRED_BUCKETS if name not in self.buckets.as_dict()]
        if missing:
            raise IncompleteGraderCard(f"missing buckets: {missing}")
        for name, cap in BUCKET_MAX.items():
            val = getattr(self.buckets, name)
            if val is None:
                raise IncompleteGraderCard(f"bucket {name} is empty")
            if val > cap:
                raise IncompleteGraderCard(f"bucket {name}={val} exceeds max {cap}")


class IncompleteGraderCard(ValueError):
    pass


class RiskDecision(BaseModel):
    accepted: bool
    veto: bool = False
    veto_reason: str = ""
    skip_reason: str = ""
    shares: int = 0
    planned_entry: Decimal = Decimal("0")
    planned_stop: Decimal = Decimal("0")
    planned_risk_dollars: Decimal = Decimal("0")
    planned_risk_pct: Decimal = Decimal("0")
    r_value: Decimal = Decimal("0")
    cut_reason: str = ""


class OrderTicket(BaseModel):
    ticker: str
    side: str  # buy | sell
    shares: int
    session: date
    intended_price: Decimal
    stop: Decimal
    theme: str
    sector: str
    risk_pct: Decimal
    grader_total: int
    tier: str
    reason: str = ""


class Fill(BaseModel):
    ticker: str
    side: str
    shares: int
    price: Decimal
    ts: datetime
    session: date
    slippage_bps: int = 0
    note: str = ""


class Position(BaseModel):
    ticker: str
    session: date
    shares: int
    fill_price: Decimal
    initial_stop: Decimal
    live_stop: Decimal
    theme: str
    sector: str
    risk_pct: Decimal
    r_value: Decimal
    opened_at: datetime
    stop_stage: str = "entry_candle_low"
    last_price: Decimal = Decimal("0")
    closed: bool = False
    close_price: Decimal | None = None
    close_reason: str = ""
    realized_pnl: Decimal = Decimal("0")

    @property
    def open_risk_dollars(self) -> Decimal:
        if self.closed or self.shares <= 0:
            return Decimal("0")
        dist = self.fill_price - self.live_stop
        if dist <= 0:
            return Decimal("0")
        return dist * Decimal(self.shares)

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.closed:
            return Decimal("0")
        px = self.last_price or self.fill_price
        return (px - self.fill_price) * Decimal(self.shares)

    @property
    def unrealized_r(self) -> Decimal:
        if self.r_value <= 0:
            return Decimal("0")
        px = self.last_price or self.fill_price
        return (px - self.fill_price) / self.r_value


class BreakerState(BaseModel):
    daily_halt: bool = False
    daily_reason: str = ""
    next_session_off: bool = False
    next_session_off_date: date | None = None
    postmortem_required: bool = False
    postmortem_filed: bool = False
    weekly_halt: bool = False
    weekly_review_required: bool = False
    weekly_review_filed: bool = False
    weekly_anchor_equity: Decimal = Decimal("0")
    weekly_anchor_monday: date | None = None
    portfolio_halt: bool = False
    portfolio_flatten: bool = False
    reauthorize_required: bool = False
    high_water_equity: Decimal = Decimal("0")
    sod_equity: Decimal = Decimal("0")
    sod_date: date | None = None
    consecutive_closed_losses: int = 0
    s_tier_count: int = 0

    def new_entries_blocked(self, session: date) -> tuple[bool, str]:
        if self.portfolio_halt:
            return True, "portfolio_stop_pending_reauthorize"
        if self.weekly_halt and not self.weekly_review_filed:
            return True, "weekly_stop_pending_review"
        if self.daily_halt:
            return True, self.daily_reason or "daily_breaker"
        if self.next_session_off and self.next_session_off_date == session:
            return True, "next_session_off_after_day_stop"
        return False, ""


class AccountState(BaseModel):
    equity: Decimal
    cash: Decimal
    starting_equity: Decimal
    high_water: Decimal
    mode: str
    slippage_bps: int = 0


class JournalEvent(BaseModel):
    id: int | None = None
    ts: datetime
    session: date
    kind: JournalKind
    ticker: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class Alert(BaseModel):
    ts: datetime
    session: date
    ticker: str
    message: str
    ticket: OrderTicket | None = None
    acknowledged: bool = False
