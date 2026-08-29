"""SQLite persistence for the paper book and append-only journal."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from simple_gains.clock import as_chicago
from simple_gains.config import MODE_PAPER, STARTING_EQUITY
from simple_gains.models import (
    AccountState,
    Alert,
    BreakerState,
    Fill,
    GraderCard,
    JournalEvent,
    JournalKind,
    OrderTicket,
    Position,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    kind TEXT NOT NULL,
    ticker TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    shares INTEGER NOT NULL,
    fill_price TEXT NOT NULL,
    initial_stop TEXT NOT NULL,
    live_stop TEXT NOT NULL,
    theme TEXT NOT NULL,
    sector TEXT NOT NULL,
    risk_pct TEXT NOT NULL,
    r_value TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    stop_stage TEXT NOT NULL,
    last_price TEXT NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0,
    close_price TEXT,
    close_reason TEXT,
    realized_pnl TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    session TEXT NOT NULL,
    shares INTEGER NOT NULL,
    fill_price TEXT NOT NULL,
    close_price TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    close_reason TEXT NOT NULL,
    theme TEXT NOT NULL,
    sector TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    equity TEXT NOT NULL,
    cash TEXT NOT NULL,
    starting_equity TEXT NOT NULL,
    high_water TEXT NOT NULL,
    mode TEXT NOT NULL,
    slippage_bps INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS breakers (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vetoes (
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (session, ticker)
);

CREATE TABLE IF NOT EXISTS watchlist (
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    theme TEXT NOT NULL DEFAULT 'other',
    sector TEXT NOT NULL DEFAULT 'other',
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session, ticker)
);

CREATE TABLE IF NOT EXISTS grader_cards (
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session, ticker)
);

CREATE TABLE IF NOT EXISTS equity_marks (
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    equity TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    message TEXT NOT NULL,
    ticket TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
"""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"cannot serialize {type(obj)}")


def _D(v: Any) -> Decimal:
    return Decimal(str(v))


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def ensure_account(
        self,
        starting: Decimal = STARTING_EQUITY,
        mode: str = MODE_PAPER,
        slippage_bps: int = 0,
    ) -> AccountState:
        row = self.conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        if row is None:
            self.conn.execute(
                """INSERT INTO account (id, equity, cash, starting_equity, high_water, mode, slippage_bps)
                   VALUES (1, ?, ?, ?, ?, ?, ?)""",
                (str(starting), str(starting), str(starting), str(starting), mode, slippage_bps),
            )
            self.conn.commit()
            return AccountState(
                equity=starting,
                cash=starting,
                starting_equity=starting,
                high_water=starting,
                mode=mode,
                slippage_bps=slippage_bps,
            )
        return AccountState(
            equity=_D(row["equity"]),
            cash=_D(row["cash"]),
            starting_equity=_D(row["starting_equity"]),
            high_water=_D(row["high_water"]),
            mode=row["mode"],
            slippage_bps=int(row["slippage_bps"]),
        )

    def save_account(self, acct: AccountState) -> None:
        self.conn.execute(
            """UPDATE account SET equity=?, cash=?, starting_equity=?, high_water=?, mode=?, slippage_bps=?
               WHERE id=1""",
            (
                str(acct.equity),
                str(acct.cash),
                str(acct.starting_equity),
                str(acct.high_water),
                acct.mode,
                acct.slippage_bps,
            ),
        )
        self.conn.commit()

    def load_breakers(self) -> BreakerState:
        row = self.conn.execute("SELECT payload FROM breakers WHERE id=1").fetchone()
        if row is None:
            state = BreakerState()
            self.save_breakers(state)
            return state
        return BreakerState.model_validate(json.loads(row["payload"]))

    def save_breakers(self, state: BreakerState) -> None:
        payload = state.model_dump(mode="json")
        self.conn.execute(
            "INSERT INTO breakers (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (_dumps(payload),),
        )
        self.conn.commit()

    def append_journal(self, event: JournalEvent) -> JournalEvent:
        cur = self.conn.execute(
            "INSERT INTO journal (ts, session, kind, ticker, payload) VALUES (?, ?, ?, ?, ?)",
            (
                as_chicago(event.ts).isoformat(),
                event.session.isoformat(),
                event.kind.value,
                event.ticker,
                _dumps(event.payload),
            ),
        )
        self.conn.commit()
        event.id = int(cur.lastrowid)
        return event

    def journal(self, limit: int = 200, session: date | None = None) -> list[JournalEvent]:
        if session:
            rows = self.conn.execute(
                "SELECT * FROM journal WHERE session=? ORDER BY id DESC LIMIT ?",
                (session.isoformat(), limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        events = []
        for row in rows:
            events.append(
                JournalEvent(
                    id=row["id"],
                    ts=datetime.fromisoformat(row["ts"]),
                    session=date.fromisoformat(row["session"]),
                    kind=JournalKind(row["kind"]),
                    ticker=row["ticker"],
                    payload=json.loads(row["payload"]),
                )
            )
        return events

    def journal_kinds_for(self, session: date, ticker: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT kind FROM journal WHERE session=? AND ticker=?",
            (session.isoformat(), ticker),
        ).fetchall()
        return {r["kind"] for r in rows}

    def save_position(self, pos: Position) -> None:
        self.conn.execute("DELETE FROM positions WHERE ticker=?", (pos.ticker,))
        if pos.closed:
            self.conn.commit()
            return
        self.conn.execute(
            """INSERT INTO positions (
                ticker, session, shares, fill_price, initial_stop, live_stop,
                theme, sector, risk_pct, r_value, opened_at, stop_stage,
                last_price, closed, close_price, close_reason, realized_pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, '', '0')""",
            (
                pos.ticker,
                pos.session.isoformat(),
                pos.shares,
                str(pos.fill_price),
                str(pos.initial_stop),
                str(pos.live_stop),
                pos.theme,
                pos.sector,
                str(pos.risk_pct),
                str(pos.r_value),
                pos.opened_at.isoformat(),
                pos.stop_stage,
                str(pos.last_price),
            ),
        )
        self.conn.commit()

    def delete_position(self, ticker: str) -> None:
        self.conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        self.conn.commit()

    def open_positions(self) -> list[Position]:
        rows = self.conn.execute("SELECT * FROM positions WHERE closed=0").fetchall()
        return [self._row_to_position(r) for r in rows]

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            ticker=row["ticker"],
            session=date.fromisoformat(row["session"]),
            shares=int(row["shares"]),
            fill_price=_D(row["fill_price"]),
            initial_stop=_D(row["initial_stop"]),
            live_stop=_D(row["live_stop"]),
            theme=row["theme"],
            sector=row["sector"],
            risk_pct=_D(row["risk_pct"]),
            r_value=_D(row["r_value"]),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            stop_stage=row["stop_stage"],
            last_price=_D(row["last_price"]),
            closed=bool(row["closed"]),
            close_price=_D(row["close_price"]) if row["close_price"] else None,
            close_reason=row["close_reason"] or "",
            realized_pnl=_D(row["realized_pnl"]),
        )

    def record_closed_trade(self, pos: Position, closed_at: datetime) -> None:
        self.conn.execute(
            """INSERT INTO closed_trades (
                ticker, session, shares, fill_price, close_price, opened_at,
                closed_at, realized_pnl, close_reason, theme, sector
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.ticker,
                pos.session.isoformat(),
                pos.shares,
                str(pos.fill_price),
                str(pos.close_price or pos.last_price),
                pos.opened_at.isoformat(),
                closed_at.isoformat(),
                str(pos.realized_pnl),
                pos.close_reason,
                pos.theme,
                pos.sector,
            ),
        )
        self.conn.commit()

    def closed_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM closed_trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_veto(self, session: date, ticker: str, reason: str, ts: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO vetoes (session, ticker, reason, ts) VALUES (?, ?, ?, ?)",
            (session.isoformat(), ticker, reason, ts.isoformat()),
        )
        self.conn.commit()

    def is_vetoed(self, session: date, ticker: str) -> str | None:
        row = self.conn.execute(
            "SELECT reason FROM vetoes WHERE session=? AND ticker=?",
            (session.isoformat(), ticker),
        ).fetchone()
        return row["reason"] if row else None

    def set_watchlist(self, session: date, rows: Iterable[tuple[str, str, str, str]]) -> None:
        self.conn.execute("DELETE FROM watchlist WHERE session=?", (session.isoformat(),))
        self.conn.executemany(
            "INSERT INTO watchlist (session, ticker, theme, sector, note) VALUES (?, ?, ?, ?, ?)",
            [(session.isoformat(), t, theme, sector, note) for t, theme, sector, note in rows],
        )
        self.conn.commit()

    def watchlist(self, session: date) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT * FROM watchlist WHERE session=? ORDER BY ticker",
            (session.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]

    def on_watchlist(self, session: date, ticker: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM watchlist WHERE session=? AND ticker=?",
            (session.isoformat(), ticker.upper()),
        ).fetchone()
        return row is not None

    def save_card(self, card: GraderCard) -> None:
        self.conn.execute(
            """INSERT INTO grader_cards (session, ticker, payload) VALUES (?, ?, ?)
               ON CONFLICT(session, ticker) DO UPDATE SET payload=excluded.payload""",
            (card.date.isoformat(), card.ticker, _dumps(card.model_dump(mode="json"))),
        )
        self.conn.commit()

    def cards(self, session: date) -> list[GraderCard]:
        rows = self.conn.execute(
            "SELECT payload FROM grader_cards WHERE session=?", (session.isoformat(),)
        ).fetchall()
        return [GraderCard.model_validate(json.loads(r["payload"])) for r in rows]

    def mark_equity(self, ts: datetime, session: date, equity: Decimal, reason: str = "") -> None:
        self.conn.execute(
            "INSERT INTO equity_marks (ts, session, equity, reason) VALUES (?, ?, ?, ?)",
            (ts.isoformat(), session.isoformat(), str(equity), reason),
        )
        self.conn.commit()

    def equity_curve(self, limit: int = 400) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT * FROM equity_marks ORDER BY ts ASC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_alert(self, alert: Alert) -> None:
        ticket = _dumps(alert.ticket.model_dump(mode="json")) if alert.ticket else None
        self.conn.execute(
            "INSERT INTO alerts (ts, session, ticker, message, ticket, acknowledged) VALUES (?, ?, ?, ?, ?, ?)",
            (
                alert.ts.isoformat(),
                alert.session.isoformat(),
                alert.ticker,
                alert.message,
                ticket,
                int(alert.acknowledged),
            ),
        )
        self.conn.commit()

    def alerts(self, session: date | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if session:
            rows = self.conn.execute(
                "SELECT * FROM alerts WHERE session=? ORDER BY id DESC LIMIT ?",
                (session.isoformat(), limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["ticket"] = json.loads(r["ticket"]) if r["ticket"] else None
            out.append(item)
        return out
