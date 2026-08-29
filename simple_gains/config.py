"""Locked product rules. Weights and constitution numbers are v1 — do not change.

Constitution v1.1 locks the last NEW ENTRY cutoff at 11:00 America/Chicago.
Do not change risk %, book caps, breakers, scoring weights, or the +1R floor.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

# --- Philosophy / product ---
PHILOSOPHY = "Quality over quantity. Consistent gains over greedy wins."
PAPER_ONLY = True

# --- Session clock (all product times are America/Chicago) ---
SESSION_TZ = "America/Chicago"
PREMARKET_SCAN_START = time(3, 0)
REGULAR_OPEN = time(9, 30)
ENTRY_CUTOFF = time(11, 0)  # last NEW ENTRY; after this, trails/stops only
REGULAR_CLOSE = time(15, 0)  # US cash close is 16:00 ET = 15:00 CT

# First 15-minute candle of the system's regular session (9:30–9:45 CDT).
OPENING_RANGE_MINUTES = 15
TRIGGER_TIMEFRAME_MINUTES = 5

# --- Liquidity hard pre-filters (documented, Scout only) ---
MIN_PRICE = Decimal("5.00")
MIN_ADV_SHARES = 1_000_000
MAX_SPREAD_PCT = Decimal("0.50")  # 0.50% of mid

# Chase / stretch: fail if last <= confirmation and price already
# >= 0.8 × ADR above the first 15-minute high.
STRETCH_ADR_MULTIPLE = Decimal("0.8")

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
