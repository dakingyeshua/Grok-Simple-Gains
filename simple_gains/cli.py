"""CLI: scan → watchlist → grade → risk → paper fill/alert → journal."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path

from simple_gains.clock import CHICAGO, Clock
from simple_gains.config import MODE_PAPER, STARTING_EQUITY
from simple_gains.engine import DEFAULT_DB, Engine, build_broker, build_data, default_store


def _engine(args: argparse.Namespace) -> Engine:
    db = Path(args.db) if getattr(args, "db", None) else DEFAULT_DB
    store = default_store(db)
    mode = getattr(args, "mode", None) or os.environ.get("SIMPLE_GAINS_MODE") or MODE_PAPER
    broker = build_broker(store, mode)
    data = build_data(use_fixtures=getattr(args, "fixtures", False), fixture_path=_fixture_path(args))
    clock = Clock()
    if getattr(args, "asof", None):
        clock.freeze(datetime.fromisoformat(args.asof).replace(tzinfo=CHICAGO))
    elif getattr(args, "fixtures", False) and hasattr(data, "session"):
        # Replay the fixture session at 10:00 CDT so entries are in window.
        clock.freeze(datetime.combine(data.session, datetime.strptime("10:00", "%H:%M").time(), tzinfo=CHICAGO))
    return Engine(store, broker, data, clock)


def _fixture_path(args: argparse.Namespace) -> Path | None:
    raw = getattr(args, "fixture_path", None)
    return Path(raw) if raw else None


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_scan(args: argparse.Namespace) -> int:
    eng = _engine(args)
    tickers = args.tickers.split(",") if args.tickers else None
    session = date.fromisoformat(args.date) if args.date else None
    _print(eng.scan(tickers, session))
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    eng = _engine(args)
    session = date.fromisoformat(args.date) if args.date else None
    tickers = args.tickers.split(",") if args.tickers else None
    _print(eng.run_session(session, tickers))
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    eng = _engine(args)
    session = date.fromisoformat(args.date) if args.date else None
    tickers = [args.ticker]
    eng.scan(tickers, session)
    _print(eng.evaluate_ticker(args.ticker, session))
    return 0


def cmd_manage(args: argparse.Namespace) -> int:
    eng = _engine(args)
    session = date.fromisoformat(args.date) if args.date else None
    _print(eng.manage_open(session))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    eng = _engine(args)
    payload = eng.dashboard_payload()
    slim = {k: payload[k] for k in ("philosophy", "mode", "session", "now", "can_enter", "equity", "breakers", "live_disabled")}
    slim["open_positions"] = payload["positions"]
    slim["watchlist"] = payload["watchlist"]
    _print(slim)
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    eng = _engine(args)
    session = date.fromisoformat(args.date) if args.date else None
    events = eng.store.journal(limit=args.limit, session=session)
    _print([e.model_dump(mode="json") for e in events])
    return 0


def cmd_postmortem(args: argparse.Namespace) -> int:
    eng = _engine(args)
    eng.file_postmortem(args.text)
    print("post-mortem filed")
    return 0


def cmd_weekly_review(args: argparse.Namespace) -> int:
    eng = _engine(args)
    eng.file_weekly_review(args.text)
    print("weekly review filed — weekly halt cleared")
    return 0


def cmd_authorize(args: argparse.Namespace) -> int:
    eng = _engine(args)
    eng.reauthorize()
    print("portfolio re-authorized")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from simple_gains.dashboard import app, bind_engine

    eng = _engine(args)
    bind_engine(eng)
    host = args.host or os.environ.get("SIMPLE_GAINS_HOST") or "127.0.0.1"
    port = int(args.port or os.environ.get("SIMPLE_GAINS_PORT") or 8000)
    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    store = default_store(Path(args.db) if args.db else DEFAULT_DB)
    acct = store.ensure_account()
    print(f"paper book ready at {store.path}")
    print(f"starting equity {acct.starting_equity} (default {STARTING_EQUITY})")
    print("this is paper/sim only — no live orders")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="simple-gains",
        description="Simple Gains paper ORB/ORR engine. Quality over quantity.",
    )
    p.add_argument("--db", help="SQLite path (default data/simple_gains.sqlite)")
    p.add_argument("--mode", choices=["paper", "hitl", "live"], help="paper | hitl | live-stub")
    p.add_argument("--fixtures", action="store_true", help="Use offline fixture data (no API key)")
    p.add_argument("--fixture-path", help="Custom fixture JSON")
    p.add_argument("--asof", help="Freeze clock to an ISO datetime in America/Chicago")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="Create the local paper book ($1,000 default)")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("scan", help="Premarket/context scan → watchlist (no orders)")
    s.add_argument("--tickers", help="Comma tickers (ignored when --fixtures)")
    s.add_argument("--date", help="Session date YYYY-MM-DD")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("session", help="Run scan → grade → risk → paper fill → manage")
    s.add_argument("--tickers", help="Comma tickers")
    s.add_argument("--date", help="Session date YYYY-MM-DD")
    s.set_defaults(func=cmd_session)

    s = sub.add_parser("grade", help="Evaluate one ticker through the four lanes")
    s.add_argument("ticker")
    s.add_argument("--date")
    s.set_defaults(func=cmd_grade)

    s = sub.add_parser("manage", help="Trail stops / stop-out open paper positions")
    s.add_argument("--date")
    s.set_defaults(func=cmd_manage)

    s = sub.add_parser("status", help="Equity, breakers, open paper positions")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("journal", help="Append-only journal tail")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--date")
    s.set_defaults(func=cmd_journal)

    s = sub.add_parser("postmortem", help="File the written post-mortem after a day-stop")
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_postmortem)

    s = sub.add_parser("weekly-review", help="File weekly review and clear the weekly halt")
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_weekly_review)

    s = sub.add_parser("authorize", help="Explicit re-authorize after a portfolio stop")
    s.set_defaults(func=cmd_authorize)

    s = sub.add_parser("serve", help="Open the local dashboard")
    s.add_argument("--host")
    s.add_argument("--port")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
