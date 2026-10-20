"""One live/paper cycle: compute today's target, diff against the account, place orders.

Run it while the US market is closed (e.g. Australian morning) so the signal uses a final
daily close; market orders then queue and fill at the next US open, matching the backtest.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from trader.broker import Broker, Position
from trader.data import load_prices
from trader.portfolio import apply_target
from trader.strategies import Strategy
from trader.tradelog import TradeLog

CASH_BUFFER = 0.02  # keep 2% in cash so price moves before the open don't overdraw the account


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    side: str
    qty: float  # ignored when close_all
    close_all: bool = False


def current_weights(universe: list[str], equity: float, positions: dict[str, Position]) -> dict[str, float]:
    return {s: positions[s].market_value / equity for s in universe if s in positions}


def plan_orders(target: dict[str, float], universe: list[str], equity: float,
                positions: dict[str, Position], last_price: dict[str, float]) -> list[PlannedOrder]:
    """Orders that move the strategy's universe to `target`. Sells come first to free cash."""
    current = current_weights(universe, equity, positions)
    after = apply_target({s: w * (1 - CASH_BUFFER) for s, w in target.items()}, current)
    sells, buys = [], []
    for symbol in universe:
        want, have = after.get(symbol, 0.0), current.get(symbol, 0.0)
        if want == have:
            continue
        if want == 0:
            sells.append(PlannedOrder(symbol, "sell", positions[symbol].qty, close_all=True))
            continue
        delta = (want - have) * equity
        pos = positions.get(symbol)
        price = last_price.get(symbol) or pos.market_value / pos.qty
        order = PlannedOrder(symbol, "buy" if delta > 0 else "sell", abs(delta) / price)
        (buys if delta > 0 else sells).append(order)
    return sells + buys


def strategy_equity(account_equity: float, baseline: float, capital: float | None) -> float:
    """Money the strategy trades with. With a cap it's the cap plus the account's P&L since
    the baseline was recorded, so gains and losses compound as if it started with `capital`.
    Assumes one strategy per account."""
    if capital is None:
        return account_equity
    return min(account_equity, capital + account_equity - baseline)


def run_once(strategy: Strategy, broker: Broker, log: TradeLog, dry_run: bool = True,
             capital: float | None = None) -> list[PlannedOrder]:
    lookback = max(730, strategy.warmup_days * 2)
    prices = load_prices(strategy.symbols, start=date.today() - timedelta(days=lookback))
    # A symbol can come back empty (bad download); strategies skip it, and we fall back to
    # the broker's own valuation if we need to trim a position in it.
    last_price = {s: float(prices[s].dropna().iloc[-1]) for s in strategy.symbols if prices[s].notna().any()}
    missing = set(strategy.symbols) - set(last_price)
    if missing:
        print(f"Warning: no price data for {sorted(missing)}")

    positions = broker.positions()
    account_equity = broker.equity()
    baseline = log.baseline(broker.name, account_equity)
    equity = strategy_equity(account_equity, baseline, capital)
    if equity < account_equity:
        print(f"Sizing with ${equity:,.2f} capital cap (account holds ${account_equity:,.2f})")
    unmanaged = set(positions) - set(strategy.symbols)
    if unmanaged:
        print(f"Note: leaving positions outside this strategy untouched: {sorted(unmanaged)}")

    target = strategy.target_weights(prices, current_weights(strategy.symbols, equity, positions))
    orders = plan_orders(target, strategy.symbols, equity, positions, last_price)
    print(f"{strategy.name} as of {prices.index[-1].date()}: target {target or 'all cash'}; equity ${equity:,.2f}")
    for o in orders:
        desc = f"close {o.symbol}" if o.close_all else f"{o.side} {o.qty:.4f} {o.symbol}"
        if dry_run:
            print(f"  [dry run] {desc}")
            continue
        order_id = broker.close_position(o.symbol) if o.close_all else \
            broker.submit_market_order(o.symbol, o.qty, o.side, strategy.name)
        print(f"  submitted {desc} ({order_id})")
    if not orders:
        print("  no changes needed")
    return orders


def sync_fills(broker: Broker, log: TradeLog, days: int = 30) -> tuple[int, int]:
    """Returns (new fills, new option events) logged."""
    since = datetime.now(timezone.utc) - pd.Timedelta(days=days)
    fills = sum(log.record(f, broker.name, broker.paper) for f in broker.filled_orders(since))
    events = sum(log.record_option_event(broker.name, e) for e in broker.option_events(since))
    return fills, events
