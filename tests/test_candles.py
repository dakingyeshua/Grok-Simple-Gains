"""OHLCV path: Twelve Data or yfinance only. Finnhub /stock/candle must never run."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from simple_gains.clock import CHICAGO, NEW_YORK
from simple_gains.data.candles import (
    fetch_ohlcv,
    twelve_data_ohlcv,
    yfinance_ohlcv,
)
from simple_gains.data.finnhub import FinnhubCandleForbidden, FinnhubData
from simple_gains.engine import Engine, build_broker
from simple_gains.models import Candle
from tests.conftest import chicago

FIVE_SESSION = date(2026, 9, 3)  # the day the running engine 403'd on FIVE


def _bar(ts: datetime | None = None, close: str = "10.50") -> Candle:
    ts = ts or datetime(2026, 9, 3, 8, 45, tzinfo=CHICAGO)
    px = Decimal(close)
    return Candle(ts=ts, open=px - Decimal("0.10"), high=px + Decimal("0.20"), low=px - Decimal("0.20"), close=px, volume=1000)


class _JsonResp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self):
        return self

    def json(self):
        return self._payload


class _FailIfCandleClient:
    """httpx stand-in: any Finnhub candle URL fails the test immediately."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url, params=None):
        full = str(url)
        self.urls.append(full)
        if "/stock/candle" in full:
            raise AssertionError(f"Finnhub /stock/candle must not be requested: {full}")
        path = httpx.URL(full).path if "://" in full else full
        if path.endswith("/quote"):
            return _JsonResp({"c": 27.41})
        if path.endswith("/stock/profile2"):
            return _JsonResp({"exchange": "NASDAQ", "finnhubIndustry": "retail"})
        if path.endswith("/company-news"):
            return _JsonResp([{"headline": "desk note"}])
        raise AssertionError(f"unexpected Finnhub request: {full}")

    def close(self) -> None:
        pass


def test_finnhub_stock_candle_attempt_fails_the_suite():
    """A Finnhub candle attempt must fail the suite, not return HTTP 403."""
    client = _FailIfCandleClient()
    data = FinnhubData(api_key="test-key", client=client)
    with pytest.raises(FinnhubCandleForbidden, match="forbidden"):
        data._get("/stock/candle", {"symbol": "FIVE", "resolution": "5"})
    assert client.urls == []


def test_engine_sources_never_http_finnhub_stock_candle():
    root = Path(__file__).resolve().parents[1] / "simple_gains"
    call_re = re.compile(r"""_get\(\s*['\"]/?stock/candle""")
    url_re = re.compile(r"""finnhub\.io[^'\"\n]*stock/candle""")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if call_re.search(text) or url_re.search(text):
            offenders.append(str(path.relative_to(root.parent)))
        if path.name == "finnhub.py":
            assert "FinnhubCandleForbidden" in text
            assert "FINNHUB_CANDLE_PATH" in text
            assert "raise FinnhubCandleForbidden" in text
    assert offenders == []


def test_candles_use_yfinance_when_twelve_key_missing(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    seen = {"twelve": 0, "yf": 0}
    bar = _bar()

    def twelve(*_a, **_k):
        seen["twelve"] += 1
        return [bar]

    def yf(*_a, **_k):
        seen["yf"] += 1
        return [bar]

    monkeypatch.setattr("simple_gains.data.candles.twelve_data_ohlcv", twelve)
    monkeypatch.setattr("simple_gains.data.candles.yfinance_ohlcv", yf)
    out = fetch_ohlcv("FIVE", FIVE_SESSION, "5")
    assert out == [bar]
    assert seen["twelve"] == 0
    assert seen["yf"] == 1


def test_candles_use_twelve_data_when_key_set(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "td-test-key")
    seen = {"twelve": 0, "yf": 0}
    bar = _bar()

    def twelve(*_a, **_k):
        seen["twelve"] += 1
        return [bar]

    def yf(*_a, **_k):
        seen["yf"] += 1
        return [_bar(close="99")]

    monkeypatch.setattr("simple_gains.data.candles.twelve_data_ohlcv", twelve)
    monkeypatch.setattr("simple_gains.data.candles.yfinance_ohlcv", yf)
    out = fetch_ohlcv("FIVE", FIVE_SESSION, "5")
    assert out == [bar]
    assert seen["twelve"] == 1
    assert seen["yf"] == 0


def test_blank_twelve_key_uses_yfinance(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "   ")
    seen = {"twelve": 0, "yf": 0}

    def twelve(*_a, **_k):
        seen["twelve"] += 1
        return [_bar()]

    def yf(*_a, **_k):
        seen["yf"] += 1
        return [_bar()]

    monkeypatch.setattr("simple_gains.data.candles.twelve_data_ohlcv", twelve)
    monkeypatch.setattr("simple_gains.data.candles.yfinance_ohlcv", yf)
    fetch_ohlcv("FIVE", FIVE_SESSION, "5")
    assert seen["twelve"] == 0
    assert seen["yf"] == 1


def test_twelve_empty_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "td-test-key")
    bar = _bar()
    monkeypatch.setattr("simple_gains.data.candles.twelve_data_ohlcv", lambda *_a, **_k: [])
    monkeypatch.setattr("simple_gains.data.candles.yfinance_ohlcv", lambda *_a, **_k: [bar])
    assert fetch_ohlcv("FIVE", FIVE_SESSION, "5") == [bar]


def test_finnhub_candles_never_touch_finnhub_http(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    bar = _bar()
    monkeypatch.setattr("simple_gains.data.candles.yfinance_ohlcv", lambda *_a, **_k: [bar])
    client = _FailIfCandleClient()
    data = FinnhubData(api_key="test-key", client=client)
    out = data.candles("FIVE", FIVE_SESSION, "5")
    assert out == [bar]
    assert client.urls == []


def test_orb_snapshot_never_requests_finnhub_candle(monkeypatch):
    """Reproduce the 2026-09-03 FIVE confirm path without /stock/candle."""
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    bar = _bar()

    def ohlcv(ticker, session, resolution, **_k):
        assert ticker in {"FIVE", "SPY", "QQQ"}
        assert session == FIVE_SESSION
        return [bar]

    monkeypatch.setattr("simple_gains.data.finnhub.fetch_ohlcv", ohlcv)
    client = _FailIfCandleClient()
    data = FinnhubData(api_key="test-key", client=client)
    snap = data.snapshot("FIVE", FIVE_SESSION, True)
    assert snap.five_min == [bar]
    assert snap.quote.last == Decimal("27.41")
    assert snap.five_min[0].close != snap.quote.last
    assert all("/stock/candle" not in u for u in client.urls)
    assert any(u.endswith("/quote") or "/quote" in u for u in client.urls)


def test_quote_last_is_not_used_as_close(monkeypatch):
    monkeypatch.setattr("simple_gains.data.finnhub.fetch_ohlcv", lambda *_a, **_k: [])
    client = _FailIfCandleClient()
    data = FinnhubData(api_key="test-key", client=client)
    snap = data.snapshot("FIVE", FIVE_SESSION, True)
    assert snap.five_min == []
    assert snap.fifteen_min == []
    assert snap.daily == []
    assert snap.quote.last == Decimal("27.41")


def test_twelve_data_http_hits_twelvedata_not_finnhub():
    recorded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(str(request.url))
        assert "finnhub.io" not in str(request.url)
        assert "/stock/candle" not in str(request.url)
        assert "api.twelvedata.com" in str(request.url)
        assert request.url.path == "/time_series"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "values": [
                    {
                        "datetime": "2026-09-03 09:45:00",
                        "open": "10.40",
                        "high": "10.70",
                        "low": "10.30",
                        "close": "10.50",
                        "volume": "1500",
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bars = twelve_data_ohlcv("FIVE", FIVE_SESSION, "5", api_key="td-key", client=client)
    assert len(bars) == 1
    assert bars[0].close == Decimal("10.50")
    assert bars[0].ts == datetime(2026, 9, 3, 8, 45, tzinfo=CHICAGO)
    assert recorded and "twelvedata.com" in recorded[0]


def test_yfinance_maps_ohlcv_and_never_calls_finnhub(monkeypatch):
    import pandas as pd

    idx = pd.DatetimeIndex([datetime(2026, 9, 3, 9, 45, tzinfo=NEW_YORK)])
    frame = pd.DataFrame(
        {"Open": [10.4], "High": [10.7], "Low": [10.3], "Close": [10.5], "Volume": [1500]},
        index=idx,
    )
    monkeypatch.setattr("simple_gains.data.candles._yfinance_download", lambda *_a, **_k: frame)
    bars = yfinance_ohlcv("FIVE", FIVE_SESSION, "5")
    assert len(bars) == 1
    assert bars[0].close == Decimal("10.5")
    assert bars[0].ts.tzinfo is not None
    assert bars[0].ts == datetime(2026, 9, 3, 8, 45, tzinfo=CHICAGO)


def test_engine_evaluate_uses_helper_candles_not_finnhub(store, monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    from simple_gains.clock import Clock
    from simple_gains.data.fixtures import FixtureData
    from pathlib import Path

    # Fixture path still used for a full evaluate; spy that Finnhub candles stay unused.
    called = {"finnhub_get": 0}

    def boom(self, path, params):
        called["finnhub_get"] += 1
        if "candle" in path:
            raise FinnhubCandleForbidden(path)
        raise AssertionError(f"evaluate must not use Finnhub HTTP: {path}")

    monkeypatch.setattr(FinnhubData, "_get", boom)
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    clock = Clock()
    clock.freeze(chicago(10, 0))
    eng = Engine(store, build_broker(store, "paper"), data, clock)
    eng.scan(session=date(2024, 3, 15))
    out = eng.evaluate_ticker("AAPL", date(2024, 3, 15))
    assert out["decision"] == "filled"
    assert called["finnhub_get"] == 0
