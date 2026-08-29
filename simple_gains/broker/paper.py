"""Paper broker: market-style fills at the 5-minute close plus optional slippage bps."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from simple_gains.broker.base import Broker
from simple_gains.config import MODE_PAPER
from simple_gains.models import AccountState, Fill, OrderTicket, Position
from simple_gains.persist import Store


def apply_slippage(price: Decimal, side: str, bps: int) -> Decimal:
    if bps <= 0:
        return price
    adj = price * Decimal(bps) / Decimal("10000")
    raw = price + adj if side == "buy" else price - adj
    return raw.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class PaperBroker(Broker):
    mode = MODE_PAPER

    def __init__(self, store: Store, account: AccountState) -> None:
        self.store = store
        self.account = account
        self._positions: dict[str, Position] = {p.ticker: p for p in store.open_positions()}

    def equity(self) -> Decimal:
        # Cash is reduced on buys. Mark-to-market = cash + last*shares, not cash + P&L.
        mtm = sum(
            ((p.last_price or p.fill_price) * Decimal(p.shares) for p in self._positions.values() if not p.closed),
            Decimal("0"),
        )
        return self.account.cash + mtm

    def cash(self) -> Decimal:
        return self.account.cash

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def mark(self, ticker: str, last: Decimal) -> None:
        pos = self._positions.get(ticker)
        if pos and not pos.closed:
            pos.last_price = last
            self.store.save_position(pos)
            self._sync_equity()

    def place_market_buy(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill:
        px = apply_slippage(fill_price, "buy", self.account.slippage_bps)
        notional = px * Decimal(ticket.shares)
        if notional > self.account.cash:
            # Still fill what cash allows? Constitution says full size or skip.
            # Fail closed: refuse rather than scale in.
            raise ValueError("paper cash insufficient for full-size ticket")
        self.account.cash -= notional
        r_value = px - ticket.stop
        pos = Position(
            ticker=ticket.ticker,
            session=ticket.session,
            shares=ticket.shares,
            fill_price=px,
            initial_stop=ticket.stop,
            live_stop=ticket.stop,
            theme=ticket.theme,
            sector=ticket.sector,
            risk_pct=ticket.risk_pct,
            r_value=r_value if r_value > 0 else Decimal("0"),
            opened_at=ts,
            stop_stage="entry_candle_low",
            last_price=px,
        )
        self._positions[ticket.ticker] = pos
        self.store.save_position(pos)
        self._sync_equity()
        return Fill(
            ticker=ticket.ticker,
            side="buy",
            shares=ticket.shares,
            price=px,
            ts=ts,
            session=ticket.session,
            slippage_bps=self.account.slippage_bps,
            note="paper_market_on_5m_close",
        )

    def place_market_sell(self, ticket: OrderTicket, ts: datetime, fill_price: Decimal) -> Fill:
        pos = self._positions.get(ticket.ticker)
        if pos is None:
            raise ValueError(f"no open paper position in {ticket.ticker}")
        px = apply_slippage(fill_price, "sell", self.account.slippage_bps)
        shares = ticket.shares if ticket.shares > 0 else pos.shares
        proceeds = px * Decimal(shares)
        self.account.cash += proceeds
        pnl = (px - pos.fill_price) * Decimal(shares)
        pos.closed = True
        pos.close_price = px
        pos.close_reason = ticket.reason or "sell"
        pos.realized_pnl = pnl
        pos.last_price = px
        self.store.record_closed_trade(pos, ts)
        self.store.delete_position(ticket.ticker)
        self._positions.pop(ticket.ticker, None)
        self._sync_equity()
        return Fill(
            ticker=ticket.ticker,
            side="sell",
            shares=shares,
            price=px,
            ts=ts,
            session=ticket.session,
            slippage_bps=self.account.slippage_bps,
            note=ticket.reason or "paper_sell",
        )

    def flatten_all(self, ts: datetime, prices: dict[str, Decimal], reason: str) -> list[Fill]:
        fills = []
        for ticker in list(self._positions):
            pos = self._positions[ticker]
            px = prices.get(ticker, pos.last_price or pos.fill_price)
            ticket = OrderTicket(
                ticker=ticker,
                side="sell",
                shares=pos.shares,
                session=pos.session,
                intended_price=px,
                stop=pos.live_stop,
                theme=pos.theme,
                sector=pos.sector,
                risk_pct=pos.risk_pct,
                grader_total=0,
                tier="",
                reason=reason,
            )
            fills.append(self.place_market_sell(ticket, ts, px))
        return fills

    def reduce_shares(self, ticker: str, new_shares: int, ts: datetime, last: Decimal) -> None:
        """Post-fill cut: sell the excess immediately at the fill price (no scale-in)."""
        pos = self._positions[ticker]
        if new_shares >= pos.shares:
            return
        excess = pos.shares - new_shares
        ticket = OrderTicket(
            ticker=ticker,
            side="sell",
            shares=excess,
            session=pos.session,
            intended_price=last,
            stop=pos.live_stop,
            theme=pos.theme,
            sector=pos.sector,
            risk_pct=pos.risk_pct,
            grader_total=0,
            tier="",
            reason="post_fill_2pct_ceiling",
        )
        # Partial sell without closing the remainder.
        px = apply_slippage(last, "sell", 0)
        self.account.cash += px * Decimal(excess)
        pos.shares = new_shares
        self.store.save_position(pos)
        self._sync_equity()
        del ticket

    def update_stop(self, ticker: str, new_stop: Decimal, stage: str) -> Position:
        pos = self._positions[ticker]
        pos.live_stop = new_stop
        pos.stop_stage = stage
        self.store.save_position(pos)
        return pos

    def _sync_equity(self) -> None:
        marked = self.equity()
        self.account.equity = marked
        if marked > self.account.high_water:
            self.account.high_water = marked
        self.store.save_account(self.account)
