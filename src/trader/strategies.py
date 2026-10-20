"""Strategies turn price history into target portfolio weights.

Contract: `target_weights(prices, current)` sees only rows up to and including "today's"
close, plus the weights currently held, and returns {symbol: weight}. Weights sum to <= 1;
the remainder is cash. The same code runs in backtests and live, so a strategy must never
look at anything beyond what it is given.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


class Strategy(ABC):
    name: str
    benchmark: str

    @property
    @abstractmethod
    def symbols(self) -> list[str]: ...

    @property
    @abstractmethod
    def warmup_days(self) -> int:
        """Trading days of history needed before the first signal."""

    @abstractmethod
    def target_weights(self, prices: pd.DataFrame, current: dict[str, float]) -> dict[str, float]: ...


@dataclass
class TrendFollowing(Strategy):
    """Hold `symbol` while it closes above its moving average, otherwise sit in cash."""

    symbol: str = "SPY"
    sma_days: int = 200

    def __post_init__(self):
        self.name = f"trend-{self.symbol}"
        self.benchmark = self.symbol

    @property
    def symbols(self) -> list[str]:
        return [self.symbol]

    @property
    def warmup_days(self) -> int:
        return self.sma_days

    def target_weights(self, prices, current):
        p = prices[self.symbol].dropna()
        if len(p) < self.sma_days:
            return {}
        return {self.symbol: 1.0} if p.iloc[-1] > p.iloc[-self.sma_days:].mean() else {}


@dataclass
class MomentumRotation(Strategy):
    """Monthly: hold the top N assets by trailing return, equal weight. Assets with a
    negative trailing return are replaced by cash (absolute-momentum filter)."""

    universe: tuple[str, ...] = ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ")
    lookback_days: int = 126
    top_n: int = 3
    benchmark: str = "SPY"

    def __post_init__(self):
        self.name = f"momentum-top{self.top_n}"

    @property
    def symbols(self) -> list[str]:
        return list(self.universe)

    @property
    def warmup_days(self) -> int:
        return self.lookback_days + 25

    def target_weights(self, prices, current):
        # Rank on data up to the previous month-end only, so the target is fixed for the
        # whole month and identical whether computed in a backtest or a live daily run.
        month_start = prices.index[-1].to_period("M").start_time
        hist = prices.loc[prices.index < month_start, list(self.universe)]
        if len(hist) <= self.lookback_days:
            return {}
        window = hist.iloc[-(self.lookback_days + 1):]
        trailing = (window.iloc[-1] / window.iloc[0] - 1).dropna()
        winners = trailing[trailing > 0].nlargest(self.top_n)
        return {s: 1.0 / self.top_n for s in winners.index}


def rsi(series: pd.Series, days: int) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / days, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / days, adjust=False).mean()
    return 100 - 100 / (1 + up / down)


@dataclass
class MeanReversionSwing(Strategy):
    """Buy short, sharp dips in assets that are in a long-term uptrend; sell on the bounce.

    Entry: close above the `trend_sma_days` average and RSI(`rsi_days`) below `entry_rsi`
    (most oversold first). Exit: close above the `exit_sma_days` average (the bounce), or
    below the trend average (the uptrend broke). Each position is 1/`max_positions` of equity.
    """

    universe: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU")
    rsi_days: int = 2
    entry_rsi: float = 10.0
    exit_sma_days: int = 5
    trend_sma_days: int = 200
    max_positions: int = 5
    benchmark: str = "SPY"
    label: str = "us"

    def __post_init__(self):
        self.name = f"swing-{self.label}"

    @property
    def symbols(self) -> list[str]:
        return list(self.universe)

    @property
    def warmup_days(self) -> int:
        return self.trend_sma_days + 20

    def target_weights(self, prices, current):
        # Slice rows before columns: selecting columns first copies the whole history daily.
        recent = prices.iloc[-(self.trend_sma_days + 50):][list(self.universe)]
        enough = recent.notna().sum() >= self.trend_sma_days
        recent = recent.ffill()
        close = recent.iloc[-1]
        in_uptrend = enough & (close > recent.iloc[-self.trend_sma_days:].mean())
        bounced = close > recent.iloc[-self.exit_sma_days:].mean()
        latest_rsi = rsi(recent, self.rsi_days).iloc[-1]

        target, candidates = {}, []
        for s in self.universe:
            if current.get(s, 0) > 0:
                if in_uptrend[s] and not bounced[s]:
                    target[s] = current[s]  # still waiting for the bounce: hold as-is
            elif in_uptrend[s] and latest_rsi[s] < self.entry_rsi:
                candidates.append((latest_rsi[s], s))

        room = 1.0 - sum(target.values())
        for _, s in sorted(candidates)[: self.max_positions - len(target)]:
            w = min(1.0 / self.max_positions, room)
            if w <= 1e-9:
                break
            target[s] = w
            room -= w
        return target


# Roughly the S&P 100 as of 2026 (BRK.B left out: Yahoo and Alpaca spell it differently).
# Using today's members in backtests flatters results (survivorship bias): companies that
# shrank or failed since 2006 aren't in the list. Paper trading is the honest test.
US_LARGE_CAPS = (
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT", "AMZN", "AVGO", "AXP", "BA", "BAC",
    "BK", "BKNG", "BLK", "BMY", "C", "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO",
    "CVS", "CVX", "DE", "DHR", "DIS", "DUK", "EMR", "F", "FDX", "GD", "GE", "GILD", "GM", "GOOGL", "GS",
    "HD", "HON", "IBM", "INTC", "INTU", "ISRG", "JNJ", "JPM", "KHC", "KO", "LIN", "LLY", "LMT", "LOW",
    "MA", "MCD", "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE", "NFLX", "NKE",
    "NOW", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM", "PYPL", "QCOM", "RTX", "SBUX", "SCHW", "SO",
    "SPG", "T", "TGT", "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC", "WMT",
    "XOM",
)

STRATEGIES = {
    "trend-spy": lambda: TrendFollowing("SPY"),
    "momentum-us": lambda: MomentumRotation(),
    "swing-us": lambda: MeanReversionSwing(),
    "swing-us-stocks": lambda: MeanReversionSwing(universe=US_LARGE_CAPS, label="us-stocks"),
    # Backtest only: Alpaca can't trade ASX. Today's largest stocks -> survivorship bias.
    "trend-asx": lambda: TrendFollowing("VAS.AX"),
    "swing-asx": lambda: MeanReversionSwing(
        universe=("STW.AX", "BHP.AX", "CBA.AX", "CSL.AX", "NAB.AX", "WBC.AX", "ANZ.AX",
                  "WES.AX", "MQG.AX", "RIO.AX", "WOW.AX", "TLS.AX", "FMG.AX"),
        benchmark="STW.AX", label="asx"),
}
