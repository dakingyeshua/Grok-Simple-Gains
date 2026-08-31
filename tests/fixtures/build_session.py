"""Generate tests/fixtures/session_orb.json — run from repo root: python tests/fixtures/build_session.py"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from simple_gains.clock import CHICAGO
from simple_gains.data.fixtures import make_orb_bars

SESSION = date(2024, 3, 15)


def dailies(close: float, n: int = 30, start: date = date(2024, 2, 1)) -> list[dict]:
    out = []
    px = close - 8
    d = start
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(
            {
                "ts": datetime(d.year, d.month, d.day, 15, 0, tzinfo=CHICAGO).isoformat(),
                "open": f"{px:.2f}",
                "high": f"{px + 1.20:.2f}",
                "low": f"{px - 1.00:.2f}",
                "close": f"{px + 0.30:.2f}",
                "volume": 14_000_000,
            }
        )
        px += 0.28
        d += timedelta(days=1)
    out[-1]["close"] = f"{close:.2f}"
    out[-1]["high"] = f"{max(float(out[-1]['high']), close):.2f}"
    return out


def main() -> None:
    aapl_later = [
        (Decimal("185.70"), Decimal("186.40"), Decimal("185.50"), Decimal("186.20")),
        (Decimal("186.20"), Decimal("186.80"), Decimal("185.90"), Decimal("186.50")),
    ]
    payload = {
        "session": SESSION.isoformat(),
        "watchlist": ["AAPL", "NVDA", "TSLA", "MSFT"],
        "sources": {
            "most_active": ["AAPL", "NVDA"],
            "unusual_volume": ["TSLA"],
            "top_gainers": ["MSFT"],
            "unusual_options": ["AAPL"],
        },
        "index": {
            "spy_session_ret": "0.0015",
            "qqq_session_ret": "0.0020",
            "spy_last_5m_red": False,
            "qqq_last_5m_red": False,
        },
        "names": {
            "AAPL": {
                "adv_shares": 45_000_000,
                "has_catalyst": True,
                "catalyst_note": "earnings beat",
                "pattern_hint": "Inverted Head & Shoulders",
                "level_note": "clean daily 185",
                "profile": {"exchange": "NASDAQ", "sector": "mega-tech", "theme": "mega-tech"},
                "quote": {"bid": "186.48", "ask": "186.52", "last": "186.50", "halted": False},
                "daily": dailies(185.2),
                "five_min": make_orb_bars(
                    SESSION,
                    Decimal("184.40"),
                    Decimal("185.00"),
                    Decimal("184.20"),
                    Decimal("185.60"),
                    Decimal("184.90"),
                    aapl_later,
                ),
                "fifteen_min": [],
            },
            "NVDA": {
                "adv_shares": 40_000_000,
                "adr": "6.00",
                "has_catalyst": False,
                "pattern_hint": "",
                "level_note": "",
                "profile": {"exchange": "NASDAQ", "sector": "semi", "theme": "semi"},
                "quote": {"bid": "890.00", "ask": "890.40", "last": "890.20", "halted": False},
                "daily": dailies(880.0),
                # No 5-minute close above ORH; last is already ≥ 0.8×ADR above ORH.
                "five_min": make_orb_bars(
                    SESSION,
                    Decimal("880.00"),
                    Decimal("881.00"),
                    Decimal("879.00"),
                    Decimal("880.80"),  # close still below ORH — not a trigger
                    Decimal("879.50"),
                ),
                "fifteen_min": [],
            },
            "TSLA": {
                "adv_shares": 80_000_000,
                "has_catalyst": False,
                "pattern_hint": "",
                "level_note": "",
                "profile": {"exchange": "NASDAQ", "sector": "consumer", "theme": "consumer"},
                "quote": {"bid": "172.10", "ask": "172.20", "last": "172.15", "halted": False},
                "daily": dailies(168.0),
                "five_min": make_orb_bars(
                    SESSION,
                    Decimal("170.00"),
                    Decimal("171.80"),
                    Decimal("168.00"),
                    Decimal("172.15"),
                    Decimal("171.40"),
                    [],
                ),
                "fifteen_min": [],
            },
            "MSFT": {
                "adv_shares": 20_000_000,
                "has_catalyst": False,
                "pattern_hint": "",
                "profile": {"exchange": "NASDAQ", "sector": "mega-tech", "theme": "mega-tech"},
                "quote": {"bid": "410.00", "ask": "410.10", "last": "410.05", "halted": False},
                "daily": dailies(410.0),
                "five_min": make_orb_bars(
                    SESSION,
                    Decimal("409.00"),
                    Decimal("411.00"),
                    Decimal("408.50"),
                    Decimal("410.20"),  # close below ORH
                    Decimal("409.00"),
                ),
                "fifteen_min": [],
            },
            "SPY": {
                "adv_shares": 80_000_000,
                "profile": {"exchange": "NYSE", "sector": "index", "theme": "index"},
                "quote": {"bid": "510.00", "ask": "510.02", "last": "510.01", "halted": False},
                "daily": dailies(510.0),
                "five_min": [],
                "fifteen_min": [],
            },
            "QQQ": {
                "adv_shares": 40_000_000,
                "profile": {"exchange": "NASDAQ", "sector": "index", "theme": "index"},
                "quote": {"bid": "430.00", "ask": "430.02", "last": "430.01", "halted": False},
                "daily": dailies(430.0),
                "five_min": [],
                "fifteen_min": [],
            },
        },
    }
    # Stretch NVDA last so chase fires: ORH 881 + 0.8 * ADR.
    # ADR from 30 dailies ~ 2.2; force quote well above.
    payload["names"]["NVDA"]["quote"] = {
        "bid": "900.00",
        "ask": "900.40",
        "last": "900.20",
        "halted": False,
    }
    dest = Path(__file__).with_name("session_orb.json")
    dest.write_text(json.dumps(payload, indent=2))
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
