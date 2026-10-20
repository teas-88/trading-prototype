"""Broker interface. Strategies never talk to a broker directly; only live.py and wheel.py
do, through this interface, so an IBKR adapter (for ASX) can be added without touching
strategy code."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol

import pandas as pd

from trader.options import OptionQuote, parse_occ

# client_order_id = "<strategy>--<random>", which lets us attribute fills to strategies later
TAG_SEP = "--"


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float  # negative for short options
    market_value: float
    avg_entry_price: float = 0.0  # per share; for options, the premium per share


@dataclass(frozen=True)
class Fill:
    broker_order_id: str
    strategy: str | None
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    fees: float
    currency: str
    filled_at: datetime  # UTC
    multiplier: int = 1  # 100 for option contracts


class Broker(Protocol):
    name: str
    paper: bool

    def equity(self) -> float: ...
    def positions(self) -> dict[str, Position]: ...
    def submit_market_order(self, symbol: str, qty: float, side: str, strategy: str) -> str: ...
    def submit_limit_order(self, symbol: str, qty: int, side: str, limit: float, strategy: str) -> str: ...
    def close_position(self, symbol: str) -> str: ...
    def cancel_open_orders(self) -> None: ...
    def market_open(self) -> bool: ...
    def option_quotes(self, underlying: str, expiry_from: date, expiry_to: date) -> list[OptionQuote]: ...
    def option_snapshots(self, symbols: list[str]) -> dict[str, OptionQuote]: ...
    def filled_orders(self, since: datetime) -> list[Fill]: ...
    def option_events(self, since: datetime) -> list[dict]: ...
    def equity_history(self, since: date) -> pd.Series: ...


def _tag(strategy: str) -> str:
    return f"{strategy}{TAG_SEP}{uuid.uuid4().hex[:12]}"


def _quote(symbol: str, snap) -> OptionQuote | None:
    contract, q = parse_occ(symbol), snap.latest_quote
    if contract is None or q is None:
        return None
    return OptionQuote(contract, float(q.bid_price or 0), float(q.ask_price or 0),
                       snap.greeks.delta if snap.greeks else None)


class AlpacaBroker:
    def __init__(self, key: str, secret: str, paper: bool = True, account: str = "default"):
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self.name = f"alpaca:{account}"
        self.paper = paper
        self.client = TradingClient(key, secret, paper=paper)
        self.options_data = OptionHistoricalDataClient(key, secret)

    def equity(self) -> float:
        return float(self.client.get_account().equity)

    def positions(self) -> dict[str, Position]:
        return {p.symbol: Position(p.symbol, float(p.qty), float(p.market_value), float(p.avg_entry_price))
                for p in self.client.get_all_positions()}

    def submit_market_order(self, symbol: str, qty: float, side: str, strategy: str) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        order = self.client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=round(qty, 4),  # fractional shares; outside market hours this queues for the open
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=_tag(strategy),
        ))
        return str(order.id)

    def submit_limit_order(self, symbol: str, qty: int, side: str, limit: float, strategy: str) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        order = self.client.submit_order(LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=limit,
            client_order_id=_tag(strategy),
        ))
        return str(order.id)

    def close_position(self, symbol: str) -> str:
        return str(self.client.close_position(symbol).id)

    def cancel_open_orders(self) -> None:
        self.client.cancel_orders()

    def market_open(self) -> bool:
        return self.client.get_clock().is_open

    def option_quotes(self, underlying: str, expiry_from: date, expiry_to: date) -> list[OptionQuote]:
        from alpaca.data.requests import OptionChainRequest

        chain = self.options_data.get_option_chain(OptionChainRequest(
            underlying_symbol=underlying, expiration_date_gte=expiry_from, expiration_date_lte=expiry_to))
        return [q for sym, snap in chain.items() if (q := _quote(sym, snap))]

    def option_snapshots(self, symbols: list[str]) -> dict[str, OptionQuote]:
        from alpaca.data.requests import OptionSnapshotRequest

        if not symbols:
            return {}
        snaps = self.options_data.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=symbols))
        return {sym: q for sym, snap in snaps.items() if (q := _quote(sym, snap))}

    def filled_orders(self, since: datetime) -> list[Fill]:
        from alpaca.trading.enums import AssetClass, QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=since, limit=500))
        fills = []
        for o in orders:
            if not o.filled_qty or float(o.filled_qty) <= 0 or o.filled_at is None:
                continue
            tag = o.client_order_id or ""
            fills.append(Fill(
                broker_order_id=str(o.id),
                strategy=tag.split(TAG_SEP)[0] if TAG_SEP in tag else None,
                symbol=o.symbol,
                side=o.side.value,
                qty=float(o.filled_qty),
                price=float(o.filled_avg_price),
                # Alpaca is commission-free; small SEC/FINRA fees on sells aren't on the order
                # object and would need reconciling from account activities once live.
                fees=0.0,
                currency="USD",
                filled_at=o.filled_at,
                multiplier=100 if o.asset_class == AssetClass.US_OPTION else 1,
            ))
        return fills

    def option_events(self, since: datetime) -> list[dict]:
        """Assignments, exercises and expiries. These move shares without an order, so they
        must be logged separately for tax. Returned raw; the format is Alpaca's."""
        return list(self.client.get("/account/activities", {
            "activity_types": "OPASN,OPEXC,OPEXP", "after": since.isoformat()}) or [])

    def equity_history(self, since: date) -> pd.Series:
        """Daily account equity from `since`, indexed by date."""
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        # Alpaca rejects a start after the last completed session, so ask from a week earlier.
        h = self.client.get_portfolio_history(GetPortfolioHistoryRequest(
            start=datetime.combine(since - timedelta(days=7), time()), timeframe="1D"))
        s = pd.Series(h.equity, index=pd.to_datetime(h.timestamp, unit="s").normalize(), dtype=float)
        s = s[s > 0]  # zeros are days before the paper account was funded
        return s[s.index >= pd.Timestamp(since)]
