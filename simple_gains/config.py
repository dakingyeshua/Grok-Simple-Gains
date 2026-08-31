"""Locked product rules. Weights and constitution numbers are v1 — do not change.

Constitution v1.2 (locked 2026-08-31) last NEW ENTRY cutoff is 13:00 America/Chicago.
Hunt/scan cutoff is 12:45 Chicago. NYSE regular session is 9:30–16:00
America/New_York, converted to America/Chicago (8:30–15:00). Never treat
9:30 as a Chicago wall time. ORB trigger level is max(premarket high,
first 15-minute high); a 5-minute bar must close above that level with
an upper wick ≤ 5% of its own range.

Do not change risk %, 85-bar skip, 6% book cap, 2-theme cap, breakers,
scoring weights, or the +1R floor.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

# --- Philosophy / product ---
PHILOSOPHY = "Quality over quantity. Consistent gains over greedy wins."
PAPER_ONLY = True

# --- Session clock ---
# Product desk times are America/Chicago. NYSE cash hours live in
# America/New_York and must be converted — DST must not shift the window.
SESSION_TZ = "America/Chicago"
EXCHANGE_TZ = "America/New_York"

NYSE_REGULAR_OPEN = time(9, 30)   # 9:30 ET → 8:30 CT
NYSE_REGULAR_CLOSE = time(16, 0)  # 16:00 ET → 15:00 CT

# Locked desk times in America/Chicago (not converted from Eastern).
PREMARKET_SCAN_START = time(3, 0)
HUNT_CUTOFF = time(12, 45)  # last hunt/scan; after this, watchlist is frozen
ENTRY_CUTOFF = time(13, 0)  # last NEW ENTRY (1:00 PM CT); after this, manage only

# Chicago wall times that must equal the NYSE conversion every session day.
REGULAR_OPEN = time(8, 30)
REGULAR_CLOSE = time(15, 0)

# First 15-minute candle of regular session: 8:30–8:45 America/Chicago.
OPENING_RANGE_MINUTES = 15
TRIGGER_TIMEFRAME_MINUTES = 5

# --- Liquidity hard pre-filters (documented, Scout only) ---
MIN_PRICE = Decimal("5.00")
MIN_ADV_SHARES = 1_000_000
MAX_SPREAD_PCT = Decimal("0.50")  # 0.50% of mid

# ORB trigger (hard pre-filter, not Grader points). Level = max(PMH, ORH).
# Confirming 5-minute bar: close strictly above the level, and
# (high - close) / (high - low) ≤ 5%. Doji (high == low) fails.
CONFIRM_UPPER_WICK_MAX = Decimal("0.05")

# Chase / stretch: fail if last is already ≥ 0.8 × ADR above the trigger level
# (max of premarket high and first 15-minute high) before confirmation.
STRETCH_ADR_MULTIPLE = Decimal("0.8")

# Scout hunt universe. Unusual Options is catalyst/watchlist only.
SCOUT_UNIVERSE_CAP = 15
SOURCE_MOST_ACTIVE = "most_active"
SOURCE_UNUSUAL_VOLUME = "unusual_volume"
SOURCE_TOP_GAINERS = "top_gainers"
SOURCE_UNUSUAL_OPTIONS = "unusual_options"
HUNT_SOURCES = (SOURCE_MOST_ACTIVE, SOURCE_UNUSUAL_VOLUME, SOURCE_TOP_GAINERS)
CATALYST_ONLY_SOURCES = (SOURCE_UNUSUAL_OPTIONS,)

# Cluster: max concurrent open names. One ticker, one full-size ticket.
CLUSTER_MAX_OPEN = 4

# --- Locked 100-point model v1 ---
BUCKET_MAX = {
    "level_pattern": 25,
    "rs_vs_spy": 20,
    "volume": 20,
    "catalyst": 15,
    "daily_20_ema": 10,
    "opening_range_quality": 10,
}
REQUIRED_BUCKETS = tuple(BUCKET_MAX.keys())
assert sum(BUCKET_MAX.values()) == 100

SKIP_BELOW = 85  # never round up
TIER_A = range(85, 90)       # 85–89
TIER_A_PLUS = range(90, 95)  # 90–94
TIER_S = range(95, 101)      # 95–100

TIER_RISK_PCT = {
    "skip": Decimal("0"),
    "A": Decimal("0.010"),
    "A+": Decimal("0.015"),
    "S": Decimal("0.020"),
}

S_TIER_FLAG_COUNT = 3  # flag if three S names in a session

# --- Locked risk constitution v1 ---
# Hard starting budget for paper (and later live). Aaron locked $1,000.
STARTING_EQUITY = Decimal("1000")
RISK_CEILING_PCT = Decimal("0.02")       # post-fill hard ceiling
BOOK_RISK_CAP_PCT = Decimal("0.06")      # max combined open risk
THEME_NAME_CAP = 2                       # skip, do not resize
DAILY_STOP_PCT = Decimal("0.10")         # −10% from start-of-day equity
STREAK_LOSS_LIMIT = 2                    # consecutive closed losses
WEEKLY_STOP_PCT = Decimal("0.06")        # −6% from Monday opening equity
PORTFOLIO_STOP_PCT = Decimal("0.20")     # −20% from high-water

# Slippage buffer added to planned stop distance for sizing.
DEFAULT_SLIPPAGE_BPS = 0
PLANNING_SLIPPAGE_BUFFER_BPS = 5  # extra distance reserved when sizing

# Sanity cap: a micro-stop cannot balloon share count.
# 1) planned stop distance must be at least max($0.10, 0.20% of entry)
# 2) notional may not exceed 25% of equity
MIN_STOP_DOLLARS = Decimal("0.10")
MIN_STOP_PCT_OF_ENTRY = Decimal("0.0020")
MAX_NOTIONAL_PCT = Decimal("0.25")

# Violent against-tape: no new longs.
# SPY (and QQQ when the name is NASDAQ) session return from the system's
# regular open is <= −0.60% AND the latest completed 5-minute candle is red.
VIOLENT_TAPE_SESSION_PCT = Decimal("-0.0060")

# Daily 20 EMA trail handoff: extended if unrealized R >= 1.5
# OR last price is at least 1.2% above the daily 20 EMA.
EMA_HANDOFF_R = Decimal("1.5")
EMA_HANDOFF_EXT_PCT = Decimal("0.012")

# Once +2R, live stop may not sit below +1R.
TWO_R = Decimal("2")
ONE_R_FLOOR = Decimal("1")

# --- Broker modes ---
MODE_PAPER = "paper"
MODE_HITL = "hitl"
MODE_LIVE = "live"
VALID_MODES = (MODE_PAPER, MODE_HITL, MODE_LIVE)
