import pandas as pd
import pytest

from trader.report import strategy_curve, summarize


def series(values, start="2026-10-20"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_strategy_curve_applies_capital_cap():
    account = series([1_000_000, 1_000_200, 999_900])
    assert list(strategy_curve(account, 1_000_000, 10_000)) == [10_000, 10_200, 9_900]
    assert list(strategy_curve(account, 1_000_000, None)) == list(account)


def test_summarize_returns_drawdown_and_benchmark():
    curve = series([10_000, 10_200, 9_900, 10_100])
    spy = series([500, 505, 510, 515, 520], start="2026-10-19")
    s = summarize(curve, 10_000, spy)
    assert s.pnl == pytest.approx(100)
    assert s.ret == pytest.approx(0.01)
    assert s.max_drawdown == pytest.approx(9_900 / 10_200 - 1)
    assert s.spy_ret == pytest.approx(520 / 505 - 1)  # from the start day's close, like the curve
    assert s.sharpe is None  # too few days to mean anything


def test_benchmark_starts_from_prior_close_when_start_is_not_a_trading_day():
    curve = pd.Series([10_000.0, 10_100.0], index=pd.to_datetime(["2026-10-24", "2026-10-26"]))  # Sat, Mon
    spy = series([500, 510], start="2026-10-23")  # Fri, Mon
    assert summarize(curve, 10_000, spy).spy_ret == pytest.approx(510 / 500 - 1)


def test_summarize_sharpe_after_enough_days():
    curve = series([10_000 * (1 + 0.001 * (i % 3 - 0.5)) ** i for i in range(30)])
    assert summarize(curve, 10_000, None).sharpe is not None
