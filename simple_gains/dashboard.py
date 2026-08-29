"""Local dashboard: watchlist, open paper positions, equity curve, breakers, journal."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from simple_gains.engine import Engine

_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_DIR / "templates"))

app = FastAPI(title="Simple Gains", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")

_engine: Engine | None = None


def bind_engine(engine: Engine) -> None:
    global _engine
    _engine = engine


def get_engine() -> Engine:
    if _engine is None:
        from simple_gains.engine import build_broker, build_data, default_store

        store = default_store()
        bind_engine(Engine(store, build_broker(store), build_data(use_fixtures=True)))
    return _engine


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    payload = get_engine().dashboard_payload()
    return templates.TemplateResponse(request, "index.html", {"d": payload})


@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(get_engine().dashboard_payload())


@app.post("/api/scan")
def api_scan() -> JSONResponse:
    return JSONResponse(get_engine().scan())


@app.post("/api/session")
def api_session() -> JSONResponse:
    return JSONResponse(get_engine().run_session())


@app.post("/api/manage")
def api_manage() -> JSONResponse:
    return JSONResponse(get_engine().manage_open())
