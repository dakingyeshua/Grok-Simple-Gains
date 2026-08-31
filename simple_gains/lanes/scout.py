"""Scout: hunt + hard pre-filters only. Does not score. Does not size."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from simple_gains.clock import (
    can_enter_new,
    first_15m_complete,
    is_premarket,
    opening_range_end,
    regular_open,
)
from simple_gains.config import (
    CLUSTER_MAX_OPEN,
    CONFIRM_UPPER_WICK_MAX,
    ENTRY_CUTOFF,
    MAX_SPREAD_PCT,
    MIN_ADV_SHARES,
    MIN_PRICE,
    SCOUT_UNIVERSE_CAP,
    SOURCE_MOST_ACTIVE,
    SOURCE_TOP_GAINERS,
    SOURCE_UNUSUAL_OPTIONS,
    SOURCE_UNUSUAL_VOLUME,
    STRETCH_ADR_MULTIPLE,
)
from simple_gains.models import Candle, FilterResult, MarketSnapshot, ScoutVerdict


FILTER_WATCHLIST = "on_watchlist"
FILTER_SESSION_WINDOW = "regular_session_before_cutoff"
FILTER_OR_COMPLETE = "first_15m_complete"
FILTER_LIQUID = "liquid_enough"
FILTER_HALTED = "not_halted"
FILTER_CLUSTER = "cluster_slot_open"
FILTER_STRETCH = "not_a_chase"

# Hunt order is constitution v1.2: Most Active, Unusual Volume, Top Gainers.
_HUNT_MERGE_ORDER = (SOURCE_MOST_ACTIVE, SOURCE_UNUSUAL_VOLUME, SOURCE_TOP_GAINERS)


@dataclass(frozen=True)
class UniverseName:
    ticker: str
    source: str
    role: str  # "hunt" | "catalyst"


def merge_scout_universe(
    source_lists: dict[str, list[str]],
    *,
    cap: int = SCOUT_UNIVERSE_CAP,
) -> list[UniverseName]:
    """Merge scout source lists. Cap ~15 hunt names.

    Hunt sources: Most Active, Unusual Volume, Top Gainers.
    Unusual Options is catalyst/watchlist only — it tags names already in the
    hunt set and does not occupy a hunt slot of its own.
    """
    seen: dict[str, UniverseName] = {}
    ordered: list[UniverseName] = []
    for source in _HUNT_MERGE_ORDER:
        for raw in source_lists.get(source, []) or []:
            ticker = str(raw).upper().strip()
            if not ticker or ticker in seen:
                continue
            if len(ordered) >= cap:
                break
            item = UniverseName(ticker=ticker, source=source, role="hunt")
            seen[ticker] = item
            ordered.append(item)
        if len(ordered) >= cap:
            break

    uo = source_lists.get(SOURCE_UNUSUAL_OPTIONS, []) or []
    for raw in uo:
        ticker = str(raw).upper().strip()
        if not ticker:
            continue
        if ticker in seen:
            prior = seen[ticker]
            if prior.role == "hunt":
                tagged = UniverseName(ticker=ticker, source=prior.source, role="hunt")
                seen[ticker] = tagged
                ordered = [tagged if n.ticker == ticker else n for n in ordered]
            continue
        # Catalyst context only — not a hunt slot, not added as an ORB candidate.
    return ordered


def opening_range_candle(five_min: list[Candle], session: date) -> Candle | None:
    """Build the first 15-minute regular-session candle from 5-minute bars."""
    start = opening_range_end(session) - timedelta(minutes=15)
    end = opening_range_end(session)
    bars = [c for c in five_min if start <= c.ts < end]
    if not bars:
        # Accept a pre-aggregated 15-minute bar whose open timestamp is session open.
        return None
    return Candle(
        ts=bars[0].ts,
        open=bars[0].open,
        high=max(c.high for c in bars),
        low=min(c.low for c in bars),
        close=bars[-1].close,
        volume=sum(c.volume for c in bars),
    )


def premarket_high_from_bars(five_min: list[Candle], session: date) -> Decimal | None:
    """Premarket high includes wicks: max high of bars before regular open."""
    open_ts = regular_open(session)
    bars = [c for c in five_min if c.ts < open_ts]
    if not bars:
        return None
    return max(c.high for c in bars)


def resolve_premarket_high(snap: MarketSnapshot) -> Decimal | None:
    if snap.premarket_high is not None:
        return snap.premarket_high
    return premarket_high_from_bars(snap.five_min, snap.session)


def orb_trigger_level(pmh: Decimal | None, orh: Decimal) -> Decimal:
    """Hard pre-filter level: max(premarket high, first 15-minute high)."""
    if pmh is None:
        return orh
    return max(pmh, orh)


def upper_wick_fraction(bar: Candle) -> Decimal | None:
    """(high - close) / (high - low). None when high == low (doji)."""
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    return (bar.high - bar.close) / rng


def confirmation_closes_above_level(bar: Candle, level: Decimal) -> bool:
    """Wicks through the level do not count. Close must be strictly above.

    Confirming 5-minute bar upper wick must be ≤ 5% of that bar's own range.
    A doji (high == low) is a fail.
    """
    if bar.close <= level:
        return False
    frac = upper_wick_fraction(bar)
    if frac is None:
        return False
    return frac <= CONFIRM_UPPER_WICK_MAX


def confirmation_closes_above_orh(bar: Candle, orh: Decimal) -> bool:
    """Alias: trigger level may be max(PMH, ORH), not ORH alone."""
    return confirmation_closes_above_level(bar, orh)


def is_chase(last_price: Decimal, level: Decimal, adr: Decimal) -> bool:
    if adr <= 0:
        return False
    return last_price >= level + (STRETCH_ADR_MULTIPLE * adr)


class Scout:
    """Hunt candidates and apply hard pass/fail filters. Never scores or sizes."""

    def evaluate(
        self,
        snap: MarketSnapshot,
        now: datetime,
        *,
        open_position_count: int,
        already_open_ticker: bool,
        confirmation: Candle | None,
    ) -> ScoutVerdict:
        filters: list[FilterResult] = []
        or_candle = opening_range_candle(snap.five_min, snap.session)
        if or_candle is None and snap.fifteen_min:
            # Fall back to the first 15-minute bar at or after regular open.
            open_ts = regular_open(snap.session)
            for bar in snap.fifteen_min:
                if bar.ts == open_ts or (open_ts <= bar.ts < opening_range_end(snap.session)):
                    or_candle = bar
                    break
            if or_candle is None:
                or_candle = snap.fifteen_min[0] if snap.fifteen_min else None

        pmh = resolve_premarket_high(snap)
        level = orb_trigger_level(pmh, or_candle.high) if or_candle is not None else None
        stretch = None
        if or_candle is not None and level is not None:
            stretch = snap.quote.last - level

        def add(name: str, passed: bool, detail: str = "") -> None:
            filters.append(FilterResult(name=name, passed=passed, detail=detail))

        add(
            FILTER_WATCHLIST,
            snap.on_watchlist,
            "on today's scan/watchlist" if snap.on_watchlist else "not on today's watchlist",
        )
        cutoff_hm = ENTRY_CUTOFF.strftime("%H:%M")
        in_window = can_enter_new(now) and not is_premarket(now)
        add(
            FILTER_SESSION_WINDOW,
            in_window,
            f"regular session before {cutoff_hm} America/Chicago"
            if in_window
            else f"outside new-entry window (premarket or at/after {cutoff_hm} CDT)",
        )
        or_done = first_15m_complete(now, snap.session) and or_candle is not None
        add(
            FILTER_OR_COMPLETE,
            or_done,
            "first 15-minute candle complete" if or_done else "opening range not complete",
        )

        liquid, liquid_detail = self._liquidity(snap)
        add(FILTER_LIQUID, liquid, liquid_detail)
        add(
            FILTER_HALTED,
            not snap.quote.halted,
            "not halted" if not snap.quote.halted else "halted — Scout fail",
        )
        cluster_ok = (not already_open_ticker) and open_position_count < CLUSTER_MAX_OPEN
        add(
            FILTER_CLUSTER,
            cluster_ok,
            "cluster slot open"
            if cluster_ok
            else (
                "already have a full-size ticket in this ticker"
                if already_open_ticker
                else f"cluster full ({open_position_count}/{CLUSTER_MAX_OPEN})"
            ),
        )

        chase = False
        chase_detail = "no stretch yet"
        if or_candle is not None and level is not None:
            # Chase is evaluated vs the trigger level before any 5-minute close above it.
            confirmed = confirmation is not None and confirmation_closes_above_level(
                confirmation, level
            )
            if not confirmed:
                chase = is_chase(snap.quote.last, level, snap.adr)
                chase_detail = (
                    f"price {snap.quote.last} >= trigger {level} + 0.8×ADR {snap.adr}"
                    if chase
                    else f"last {snap.quote.last} within 0.8×ADR of trigger {level}"
                )
            else:
                chase_detail = "5-minute confirmation already in hand; stretch filter N/A"
        add(FILTER_STRETCH, not chase, chase_detail)

        failed = [f for f in filters if not f.passed]
        passed = len(failed) == 0
        reason = "" if passed else "; ".join(f"{f.name}: {f.detail}" for f in failed)
        return ScoutVerdict(
            ticker=snap.ticker,
            session=snap.session,
            passed=passed,
            filters=filters,
            opening_range=or_candle,
            confirmation=confirmation,
            stretch_above_orh=stretch,
            premarket_high=pmh,
            trigger_level=level,
            reason=reason,
        )

    def _liquidity(self, snap: MarketSnapshot) -> tuple[bool, str]:
        reasons = []
        if snap.quote.last < MIN_PRICE:
            reasons.append(f"price {snap.quote.last} < min {MIN_PRICE}")
        if snap.adv_shares < MIN_ADV_SHARES:
            reasons.append(f"ADV {snap.adv_shares} < {MIN_ADV_SHARES}")
        if snap.quote.spread_pct > MAX_SPREAD_PCT:
            reasons.append(f"spread {snap.quote.spread_pct:.2f}% > {MAX_SPREAD_PCT}%")
        if reasons:
            return False, "; ".join(reasons)
        return True, f"price>={MIN_PRICE} ADV>={MIN_ADV_SHARES} spread<={MAX_SPREAD_PCT}%"
