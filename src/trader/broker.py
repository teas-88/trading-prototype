"""Broker interface. Strategies never talk to a broker directly; only live.py does, through
this interface, so an IBKR adapter (for ASX) can be added without touching strategy code."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# client_order_id = "<strategy>--<random>", which lets us attribute fills to strategies later
TAG_SEP = "--"


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    market_value: float


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


class Broker(Protocol):
    name: str
    paper: bool

    def equity(self) -> float: ...
    def positions(self) -> dict[str, Position]: ...
    def submit_market_order(self, symbol: str, qty: float, side: str, strategy: str) -> str: ...
    def close_position(self, symbol: str) -> str: ...
    def filled_orders(self, since: datetime) -> list[Fill]: ...


class AlpacaBroker:
    def __init__(self, key: str, secret: str, paper: bool = True, account: str = "default"):
        from alpaca.trading.client import TradingClient

        self.name = f"alpaca:{account}"
        self.paper = paper
        self.client = TradingClient(key, secret, paper=paper)

    def equity(self) -> float:
        return float(self.client.get_account().equity)

    def positions(self) -> dict[str, Position]:
        return {p.symbol: Position(p.symbol, float(p.qty), float(p.market_value))
                for p in self.client.get_all_positions()}

    def submit_market_order(self, symbol: str, qty: float, side: str, strategy: str) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        order = self.client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=round(qty, 4),  # fractional shares; outside market hours this queues for the open
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=f"{strategy}{TAG_SEP}{uuid.uuid4().hex[:12]}",
        ))
        return str(order.id)

    def close_position(self, symbol: str) -> str:
        return str(self.client.close_position(symbol).id)

    def filled_orders(self, since: datetime) -> list[Fill]:
        from alpaca.trading.enums import QueryOrderStatus
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
            ))
        return fills
