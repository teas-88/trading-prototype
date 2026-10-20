"""Performance report: each strategy's paper account against buy-and-hold SPY over the same
period, measured from the account's baseline (its first run) and sized to its capital cap."""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from trader.broker import Broker
from trader.data import aud_per_unit, load_prices
from trader.tradelog import TradeLog

MIN_DAYS_FOR_RATIOS = 20  # Sharpe on fewer daily points is noise


def strategy_curve(account_equity: pd.Series, baseline: float, capital: float | None) -> pd.Series:
    """The strategy's own value over time: capital plus the account's P&L since the baseline."""
    return account_equity if capital is None else capital + account_equity - baseline


@dataclass(frozen=True)
class Summary:
    start: date
    days: int
    basis: float
    value: float
    pnl: float
    ret: float
    max_drawdown: float
    sharpe: float | None
    spy_ret: float | None


def summarize(curve: pd.Series, basis: float, spy: pd.Series | None) -> Summary:
    start, end = curve.index[0], curve.index[-1]
    rets = curve.pct_change().dropna()
    sharpe = None
    if len(rets) >= MIN_DAYS_FOR_RATIOS and rets.std() > 0:
        sharpe = rets.mean() / rets.std() * math.sqrt(252)
    spy_ret = None
    if spy is not None:
        window = spy.loc[start:end].dropna()
        if len(window):
            # Benchmark from the last close at or before the start, so day one counts.
            before = spy.loc[:start].dropna()
            first = before.iloc[-1] if len(before) else window.iloc[0]
            spy_ret = window.iloc[-1] / first - 1
    value = float(curve.iloc[-1])
    return Summary(start.date(), (end - start).days, basis, value, value - basis, value / basis - 1,
                   float((curve / curve.cummax() - 1).min()), sharpe, spy_ret)


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.2%}"


def build_report(accounts: list[tuple[str, Broker, float | None]], log: TradeLog) -> str:
    """`accounts` is (label, broker, capital cap) per strategy."""
    started = {}
    for _, broker, _ in accounts:
        row = log.conn.execute("SELECT equity, recorded_at FROM baselines WHERE account = ?", (broker.name,)).fetchone()
        if row:
            started[broker.name] = (row[0], datetime.fromisoformat(row[1]).date())
    if not started:
        return "No strategy has run yet (no baselines recorded)."

    earliest = min(d for _, d in started.values())
    spy = load_prices(["SPY"], start=earliest - timedelta(days=7))["SPY"]
    aud = aud_per_unit("USD", date.today())

    lines = [f"Performance report {date.today()}  (USD; vs buy-and-hold SPY over the same dates)", ""]
    header = f"{'strategy':18}{'since':>12}{'days':>6}{'capital':>11}{'value':>11}{'P&L':>10}{'return':>9}" \
             f"{'SPY':>9}{'max DD':>9}{'Sharpe':>8}{'fills':>7}{'open':>6}"
    lines += [header, "-" * len(header)]
    total_pnl, notes = 0.0, []
    for label, broker, capital in accounts:
        if broker.name not in started:
            lines.append(f"{label:18}{'not started':>12}")
            continue
        baseline, since = started[broker.name]
        history = broker.equity_history(since)
        now = pd.Timestamp(datetime.now(timezone.utc).date())
        history.loc[now] = broker.equity()  # include today's live value
        curve = strategy_curve(history.sort_index(), baseline, capital)
        s = summarize(curve, capital or baseline, spy)
        fills = log.conn.execute("SELECT COUNT(*) FROM fills WHERE broker = ?", (broker.name,)).fetchone()[0]
        positions = broker.positions()
        total_pnl += s.pnl
        sharpe = "-" if s.sharpe is None else f"{s.sharpe:.2f}"
        lines.append(f"{label:18}{str(s.start):>12}{s.days:>6}{s.basis:>11,.0f}{s.value:>11,.2f}{s.pnl:>+10,.2f}"
                     f"{_pct(s.ret):>9}{_pct(s.spy_ret):>9}{s.max_drawdown:>9.1%}{sharpe:>8}{fills:>7}{len(positions):>6}")

        premium = log.conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN side = 'sell' THEN 1 ELSE -1 END * qty * price * multiplier), 0) "
            "FROM fills WHERE broker = ? AND multiplier > 1", (broker.name,)).fetchone()[0]
        events = dict(log.conn.execute("SELECT type, COUNT(*) FROM option_events WHERE account = ? GROUP BY type",
                                       (broker.name,)).fetchall())
        if premium or events:
            notes.append(f"{label}: net option premium ${premium:,.2f}; assignments {events.get('OPASN', 0)}, "
                         f"expiries {events.get('OPEXP', 0)}")

    lines += ["", f"Total P&L: ${total_pnl:+,.2f} USD (about A${total_pnl * aud:+,.2f} at today's rate)"]
    lines += notes
    if any((date.today() - d).days < 60 for _, d in started.values()):
        lines.append("Note: under ~2-3 months of results is mostly noise; judge against the pre-agreed criteria, not day to day.")
    return "\n".join(lines)
