"""Daily backtester that trades at the next open.

Timing: the target is computed from data up to the close of day t, and the trades fill at the
open of day t+1, which is what happens live when the bot runs while the US market is closed.
Weights drift with prices; trades follow the same drift-tolerance rule as live trading, and
costs are charged on turnover.
"""

import math
from dataclasses import dataclass

import pandas as pd

from trader.portfolio import apply_target
from trader.strategies import Strategy


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    trades: int
    turnover: float
    exposure: float  # average fraction of equity invested


def _grow(weights: dict[str, float], r: pd.Series) -> tuple[dict[str, float], float]:
    growth = {s: w * (1 + r[s]) for s, w in weights.items()}
    port = (1 - sum(weights.values())) + sum(growth.values())
    return {s: g / port for s, g in growth.items()}, port


def run_backtest(strategy: Strategy, opens: pd.DataFrame, closes: pd.DataFrame,
                 start: str | pd.Timestamp, cost_bps: float = 5.0) -> BacktestResult:
    start = pd.Timestamp(start)
    first = closes.index.searchsorted(start)
    if first >= len(closes):
        raise ValueError(f"No price data on or after {start.date()}")
    opens = opens.fillna(closes)
    overnight = (opens / closes.shift(1) - 1).fillna(0.0)
    intraday = (closes / opens - 1).fillna(0.0)

    value, weights, pending = 1.0, {}, {}
    trades, turnover_total = 0, 0.0
    curve, exposure = [], []
    for i in range(first, len(closes)):
        if i > first:
            weights, port = _grow(weights, overnight.iloc[i])
            value *= port
            after = apply_target(pending, weights)
            changed = [s for s in after.keys() | weights.keys() if after.get(s, 0.0) != weights.get(s, 0.0)]
            turnover = sum(abs(after.get(s, 0.0) - weights.get(s, 0.0)) for s in changed)
            value *= 1 - turnover * cost_bps / 10_000
            trades += len(changed)
            turnover_total += turnover
            weights, port = _grow(after, intraday.iloc[i])
            value *= port
        curve.append(value)
        exposure.append(sum(weights.values()))
        pending = strategy.target_weights(closes.iloc[: i + 1], weights)

    index = closes.index[first:]
    bench = closes[strategy.benchmark].iloc[first:].ffill()
    return BacktestResult(
        equity=pd.Series(curve, index=index, name=strategy.name),
        benchmark=(bench / bench.iloc[0]).rename(f"buy&hold {strategy.benchmark}"),
        trades=trades,
        turnover=turnover_total,
        exposure=sum(exposure) / len(exposure),
    )


def metrics(equity: pd.Series) -> dict[str, float]:
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    growth = equity.iloc[-1] / equity.iloc[0]
    sd = rets.std()
    return {
        "CAGR": growth ** (1 / years) - 1 if years > 0 else float("nan"),
        "Volatility": sd * math.sqrt(252),
        "Sharpe": rets.mean() / sd * math.sqrt(252) if sd > 0 else float("nan"),
        "Max drawdown": (equity / equity.cummax() - 1).min(),
        "Growth of $1": growth,
    }
