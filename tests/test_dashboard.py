from fastapi.testclient import TestClient

from simple_gains.clock import Clock
from simple_gains.dashboard import app, bind_engine
from simple_gains.data.fixtures import FixtureData
from simple_gains.engine import Engine, build_broker
from tests.conftest import SESSION, chicago


def test_dashboard_home_renders(store):
    from pathlib import Path

    broker = build_broker(store, "paper")
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    clock = Clock()
    clock.freeze(chicago(10, 0))
    eng = Engine(store, broker, data, clock)
    eng.scan(session=SESSION)
    bind_engine(eng)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Simple Gains" in r.text
    assert "Watchlist" in r.text
    assert "Breaker status" in r.text
    state = client.get("/api/state")
    assert state.status_code == 200
    body = state.json()
    assert body["mode"] == "paper"
    assert body["starting_equity"] == "1000"
    assert body["equity"] == "1000"
    assert body["entry_cutoff"] == "11:00"
    assert "entries open until 11:00 CDT" in r.text
    assert "2:00 PM" not in r.text
    assert "14:00" not in r.text


def test_dashboard_manage_only_after_1100_cdt(store):
    from pathlib import Path

    broker = build_broker(store, "paper")
    data = FixtureData(path=Path(__file__).parent / "fixtures" / "session_orb.json")
    clock = Clock()
    clock.freeze(chicago(11, 0))
    eng = Engine(store, broker, data, clock)
    eng.scan(session=SESSION)
    bind_engine(eng)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "manage only · after 11:00 CDT" in r.text
    assert "2:00 PM" not in r.text
    state = client.get("/api/state")
    assert state.json()["can_enter"] is False
    assert state.json()["entry_cutoff"] == "11:00"
