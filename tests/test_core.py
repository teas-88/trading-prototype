import os
from datetime import datetime, timezone

import pandas as pd
import pytest

from trader.backtest import metrics, run_backtest
from trader.broker import Fill, Position
from trader.live import CASH_BUFFER, plan_orders
from trader.portfolio import apply_target
from trader.strategies import MeanReversionSwing, MomentumRotation, Strategy, TrendFollowing
from trader.tradelog import TradeLog


def frame(**cols):
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.bdate_range("2020-01-01", periods=n))


class Always(Strategy):
    name, benchmark = "always", "X"
    symbols = ["X"]
    warmup_days = 0

    def target_weights(self, prices, current):
        return {"X": 1.0}


class AfterJump(Strategy):
    """Buys only once it has *seen* the jump, so it must not earn it."""
    name, benchmark = "after-jump", "X"
    symbols = ["X"]
    warmup_days = 0

    def target_weights(self, prices, current):
        return {"X": 1.0} if prices["X"].iloc[-1] > 1.5 else {}


def test_fills_at_next_open():
    closes = frame(X=[1.0, 1.1, 1.21])
    opens = frame(X=[1.0, 1.05, 1.21])
    res = run_backtest(Always(), opens, closes, start=closes.index[0], cost_bps=0)
    # Signal at the first close, bought at the 1.05 open: misses the overnight move.
    assert res.equity.iloc[-1] == pytest.approx(1.21 / 1.05)


def test_no_lookahead():
    closes = frame(X=[1.0, 1.0, 2.0, 2.0])
    res = run_backtest(AfterJump(), closes, closes, start=closes.index[0], cost_bps=0)
    assert res.equity.iloc[-1] == pytest.approx(1.0)


def test_costs_charged_on_turnover():
    closes = frame(X=[1.0, 1.0])
    res = run_backtest(Always(), closes, closes, start=closes.index[0], cost_bps=10)
    assert res.equity.iloc[-1] == pytest.approx(1 - 0.001)
    assert res.trades == 1


def test_trend_signal():
    up = frame(SPY=list(range(1, 11)))
    down = frame(SPY=list(range(10, 0, -1)))
    s = TrendFollowing("SPY", sma_days=5)
    assert s.target_weights(up, {}) == {"SPY": 1.0}
    assert s.target_weights(down, {}) == {}
    assert s.target_weights(up.iloc[:3], {}) == {}  # not enough history


def test_momentum_fixed_within_month_and_filters_losers():
    idx = pd.bdate_range("2020-01-01", "2020-04-30")
    n = len(idx)
    prices = pd.DataFrame({"A": [1 + 0.02 * i for i in range(n)],
                           "B": [1 + 0.01 * i for i in range(n)],
                           "C": [1 - 0.001 * i for i in range(n)]}, index=idx)
    s = MomentumRotation(universe=("A", "B", "C"), lookback_days=20, top_n=2)
    april = [s.target_weights(prices.loc[:d], {}) for d in idx[idx.month == 4]]
    assert all(t == april[0] for t in april)
    assert april[0] == {"A": 0.5, "B": 0.5}  # C is falling -> cash, not held


def test_metrics_drawdown():
    eq = pd.Series([1.0, 2.0, 1.0, 1.5], index=pd.bdate_range("2020-01-01", periods=4))
    assert metrics(eq)["Max drawdown"] == pytest.approx(-0.5)


def test_plan_orders():
    positions = {"A": Position("A", 10, 1000.0), "B": Position("B", 5, 500.0)}
    orders = plan_orders({"A": 0.5, "C": 0.5}, ["A", "B", "C"], 10_000, positions,
                         {"A": 100.0, "B": 100.0, "C": 50.0})
    by_symbol = {o.symbol: o for o in orders}
    assert [o.side for o in orders] == ["sell", "buy", "buy"]  # sells first
    assert by_symbol["B"].close_all
    assert by_symbol["A"].qty == pytest.approx((5000 * (1 - CASH_BUFFER) - 1000) / 100)
    assert by_symbol["C"].qty == pytest.approx(5000 * (1 - CASH_BUFFER) / 50)


def test_plan_orders_skips_small_rebalances():
    positions = {"A": Position("A", 49, 4900.0)}
    assert plan_orders({"A": 0.5}, ["A"], 10_000, positions, {"A": 100.0}) == []


def test_tradelog_converts_to_aud_and_dedupes(tmp_path):
    log = TradeLog(tmp_path / "t.db", fx=lambda ccy, day: 1.5)
    fill = Fill("o1", "trend-SPY", "SPY", "buy", 2, 100.0, 1.0, "USD",
                datetime(2026, 1, 5, 15, tzinfo=timezone.utc))
    assert log.record(fill, "alpaca", paper=True)
    assert not log.record(fill, "alpaca", paper=True)
    value_aud, fees_aud = log.conn.execute("SELECT value_aud, fees_aud FROM fills").fetchone()
    assert (value_aud, fees_aud) == (300.0, 1.5)
    assert log.export_csv(tmp_path / "out.csv") == 1


def test_apply_target_ignores_small_drift_and_closes_zeros():
    after = apply_target({"A": 0.2, "B": 0.0, "C": 0.2, "D": 0.01}, {"A": 0.22, "B": 0.3})
    assert after == {"A": 0.22, "C": 0.2}  # A drift kept, B closed, D too small to open


def swing_prices(last_moves):
    """250 days of steady uptrend, then the given daily moves."""
    p = [100 * 1.001 ** i for i in range(250)]
    for m in last_moves:
        p.append(p[-1] * (1 + m))
    return frame(A=p)


def test_swing_enters_on_sharp_dip_in_uptrend():
    s = MeanReversionSwing(universe=("A",), max_positions=4)
    assert s.target_weights(swing_prices([]), {}) == {}
    assert s.target_weights(swing_prices([-0.02, -0.02]), {}) == {"A": 0.25}


def test_swing_holds_until_bounce_then_exits():
    s = MeanReversionSwing(universe=("A",), max_positions=4)
    assert s.target_weights(swing_prices([-0.02, -0.02]), {"A": 0.24}) == {"A": 0.24}
    assert s.target_weights(swing_prices([-0.02, -0.02, 0.05]), {"A": 0.24}) == {}


def test_swing_skips_dips_in_downtrend():
    p = [100 * 0.999 ** i for i in range(250)] + [90, 88]
    assert MeanReversionSwing(universe=("A",)).target_weights(frame(A=p), {}) == {}


def test_per_strategy_accounts(monkeypatch):
    from trader import config
    for var in list(os.environ):
        if var.startswith("ALPACA_"):
            monkeypatch.delenv(var)
    monkeypatch.setenv("ALPACA_API_KEY", "k0")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s0")
    monkeypatch.setenv("ALPACA_SWING_US_API_KEY", "k1")
    monkeypatch.setenv("ALPACA_SWING_US_SECRET_KEY", "s1")
    settings = config.load()
    assert settings.account_for("swing-us").key == "k1"
    assert settings.account_for("trend-spy").name == "default"


def test_capital_cap_compounds_account_pnl():
    from trader.live import strategy_equity
    assert strategy_equity(1_000_000, 1_000_000, None) == 1_000_000
    assert strategy_equity(1_000_000, 1_000_000, 10_000) == 10_000
    assert strategy_equity(1_000_500, 1_000_000, 10_000) == 10_500  # gains compound
    assert strategy_equity(999_000, 1_000_000, 10_000) == 9_000  # and so do losses


def test_baseline_recorded_once(tmp_path):
    log = TradeLog(tmp_path / "t.db", fx=lambda ccy, day: 1.0)
    assert log.baseline("alpaca:x", 100.0) == 100.0
    assert log.baseline("alpaca:x", 150.0) == 100.0
