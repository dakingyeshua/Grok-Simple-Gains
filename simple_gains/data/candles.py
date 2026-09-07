"""Engine OHLCV helper. Twelve Data first, else yfinance. Never Finnhub candles.

Desk rule: the paper engine must never call Finnhub candlesticks (free tier 403).
Quote last is not a close — if no OHLCV arrives, return an empty list.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

import httpx

from simple_gains.clock import CHICAGO, NEW_YORK, as_chicago, premarket_scan_start, regular_close
from simple_gains.models import Candle

TWELVE_DATA_BASE = "https://api.twelvedata.com"
TWELVE_DATA_TIME_SERIES = f"{TWELVE_DATA_BASE}/time_series"

INTERVAL_TWELVE = {"5": "5min", "15": "15min", "D": "1day"}
INTERVAL_YFINANCE = {"5": "5m", "15": "15m", "D": "1d"}


def twelve_data_api_key() -> str:
    """Non-empty TWELVE_DATA_API_KEY, else ''."""
    return (os.environ.get("TWELVE_DATA_API_KEY") or "").strip()


def fetch_ohlcv(
    ticker: str,
    session: date,
    resolution: str,
    *,
    client: httpx.Client | None = None,
) -> list[Candle]:
    """Session OHLCV for ORB confirms and tape.

    Twelve Data when ``TWELVE_DATA_API_KEY`` is set and non-empty; yfinance
    otherwise (and if Twelve Data returns no bars). Never Finnhub. Never a
    synthetic close from a quote last.
    """
    if resolution not in INTERVAL_TWELVE:
        raise ValueError(f"unsupported candle resolution: {resolution}")
    symbol = ticker.upper()
    key = twelve_data_api_key()
    if key:
        bars = twelve_data_ohlcv(symbol, session, resolution, api_key=key, client=client)
        if bars:
            return bars
    return yfinance_ohlcv(symbol, session, resolution)


def twelve_data_ohlcv(
    ticker: str,
    session: date,
    resolution: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
) -> list[Candle]:
    start, end = _window(session, resolution)
    params: dict[str, Any] = {
        "symbol": ticker.upper(),
        "interval": INTERVAL_TWELVE[resolution],
        "apikey": api_key,
        "timezone": "America/New_York",
        "outputsize": 5000,
        "order": "ASC",
        "start_date": _twelve_stamp(start, resolution),
        "end_date": _twelve_stamp(end, resolution),
    }
    if resolution != "D":
        params["prepost"] = "true"
    owns = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        response = http.get(TWELVE_DATA_TIME_SERIES, params=params)
        response.raise_for_status()
        raw = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if owns:
            http.close()
    if not isinstance(raw, dict) or raw.get("status") == "error":
        return []
    values = raw.get("values")
    if not isinstance(values, list):
        return []
    return _candles_from_rows(values, default_tz=NEW_YORK)


def yfinance_ohlcv(ticker: str, session: date, resolution: str) -> list[Candle]:
    start, end = _window(session, resolution)
    try:
        frame = _yfinance_download(
            ticker.upper(),
            start=start.date() if isinstance(start, datetime) else start,
            end=(end + timedelta(days=1)).date() if isinstance(end, datetime) else end + timedelta(days=1),
            interval=INTERVAL_YFINANCE[resolution],
            prepost=resolution != "D",
        )
    except Exception:
        return []
    return _candles_from_yfinance(frame)


def _yfinance_download(ticker: str, start: date, end: date, interval: str, prepost: bool) -> Any:
    import yfinance as yf

    return yf.download(
        ticker,
        start=start,
        end=end,
        interval=interval,
        prepost=prepost,
        progress=False,
        auto_adjust=True,
        threads=False,
    )


def _window(session: date, resolution: str) -> tuple[datetime, datetime]:
    if resolution == "D":
        start = datetime.combine(session - timedelta(days=80), datetime.min.time(), tzinfo=CHICAGO)
        return start, regular_close(session)
    # Include premarket so max(PMH, first 15m high) can be derived from bars.
    return premarket_scan_start(session), regular_close(session)


def _twelve_stamp(ts: datetime, resolution: str) -> str:
    local = ts.astimezone(NEW_YORK)
    if resolution == "D":
        return local.date().isoformat()
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _candles_from_rows(rows: Iterable[dict[str, Any]], *, default_tz) -> list[Candle]:
    out: list[Candle] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        ts = _parse_ts(raw.get("datetime") or raw.get("ts") or raw.get("time"), default_tz)
        if ts is None:
            continue
        try:
            o = Decimal(str(raw["open"]))
            h = Decimal(str(raw["high"]))
            low = Decimal(str(raw["low"]))
            c = Decimal(str(raw["close"]))
        except (KeyError, ArithmeticError, ValueError):
            continue
        vol_raw = raw.get("volume", 0) or 0
        try:
            volume = int(Decimal(str(vol_raw)))
        except (ArithmeticError, ValueError):
            volume = 0
        out.append(Candle(ts=ts, open=o, high=h, low=low, close=c, volume=volume))
    out.sort(key=lambda bar: bar.ts)
    return out


def _candles_from_yfinance(frame: Any) -> list[Candle]:
    if frame is None:
        return []
    try:
        empty = frame.empty
    except Exception:
        return []
    if empty:
        return []

    frame = frame.copy()
    frame.columns = _flatten_ohlc_columns(frame.columns)

    needed = ("open", "high", "low", "close")
    if any(name not in frame.columns for name in needed):
        return []

    out: list[Candle] = []
    for idx, row in frame.iterrows():
        ts = _parse_ts(idx, NEW_YORK)
        if ts is None:
            continue
        try:
            o = Decimal(str(row["open"]))
            h = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            c = Decimal(str(row["close"]))
        except (ArithmeticError, ValueError, KeyError):
            continue
        vol = row["volume"] if "volume" in frame.columns else 0
        try:
            volume = 0 if vol is None or (hasattr(vol, "__float__") and vol != vol) else int(Decimal(str(vol)))
        except (ArithmeticError, ValueError):
            volume = 0
        out.append(Candle(ts=ts, open=o, high=h, low=low, close=c, volume=volume))
    out.sort(key=lambda bar: bar.ts)
    return out


def _flatten_ohlc_columns(columns: Any) -> list[str]:
    names: list[str] = []
    wanted = {"open", "high", "low", "close", "volume", "adj close"}
    for col in columns:
        if isinstance(col, tuple):
            parts = [str(part).lower() for part in col]
            names.append(next((part for part in parts if part in wanted), parts[0]))
        else:
            names.append(str(col).lower())
    return names


def _parse_ts(value: Any, default_tz) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            if hasattr(value, "to_pydatetime"):
                dt = value.to_pydatetime()
            else:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return as_chicago(dt)
