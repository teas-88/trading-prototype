import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from trader import config
from trader.strategies import STRATEGIES


def _broker(settings: config.Settings, account: config.AlpacaAccount, allow_live: bool):
    from trader.broker import AlpacaBroker

    if not settings.alpaca_paper and not allow_live:
        sys.exit("ALPACA_PAPER=false but --live not given; refusing to trade real money")
    return AlpacaBroker(account.key, account.secret, paper=settings.alpaca_paper, account=account.name)


def cmd_backtest(args):
    from trader.backtest import metrics, run_backtest
    from trader.data import load_bars

    strategy = STRATEGIES[args.strategy]()
    warmup_start = pd.Timestamp(args.start) - pd.Timedelta(days=int(strategy.warmup_days * 1.6) + 60)
    opens, closes = load_bars(strategy.symbols + [strategy.benchmark], start=warmup_start.date(), end=args.end)
    result = run_backtest(strategy, opens, closes, start=args.start, cost_bps=args.cost_bps)

    table = pd.DataFrame({s.name: metrics(s) for s in (result.equity, result.benchmark)})
    fmt = {"CAGR": "{:.1%}", "Volatility": "{:.1%}", "Sharpe": "{:.2f}", "Max drawdown": "{:.1%}", "Growth of $1": "{:.2f}x"}
    print(f"\n{result.equity.index[0].date()} -> {result.equity.index[-1].date()}  "
          f"(costs {args.cost_bps} bps, prices in local currency, before tax)\n")
    print(table.apply(lambda row: row.map(fmt[row.name].format), axis=1).to_string())
    years = (result.equity.index[-1] - result.equity.index[0]).days / 365.25
    print(f"\nTrades: {result.trades} ({result.trades / years:.0f}/yr)   Turnover: {result.turnover:.1f}x   "
          f"Avg invested: {result.exposure:.0%}")


def cmd_run(args):
    from trader.live import run_once
    from trader.tradelog import TradeLog

    settings = config.load()
    account = settings.account_for(args.strategy)
    if account is None:
        sys.exit("No Alpaca keys in .env (see .env.example)")
    if account.name != args.strategy:
        print(f"Note: no dedicated account for {args.strategy}; using '{account.name}'")
    run_once(STRATEGIES[args.strategy](), _broker(settings, account, args.live), TradeLog(settings.db_path),
             dry_run=not args.execute, capital=account.capital)


def cmd_sync(args):
    from trader.live import sync_fills
    from trader.tradelog import TradeLog

    settings = config.load()
    log = TradeLog(settings.db_path)
    for account in settings.accounts.values():
        fills, events = sync_fills(_broker(settings, account, allow_live=True), log, days=args.days)
        print(f"{account.name}: logged {fills} new fill(s), {events} option event(s) to {settings.db_path}")


def cmd_wheel(args):
    from trader.tradelog import TradeLog
    from trader.wheel import NAME, WheelConfig, run_wheel

    settings = config.load()
    account = settings.account_for(NAME)
    if account is None:
        sys.exit("No Alpaca keys in .env (see .env.example)")
    run_wheel(WheelConfig(), _broker(settings, account, args.live), TradeLog(settings.db_path),
              dry_run=not args.execute, capital=account.capital)


def cmd_export(args):
    from trader.tradelog import TradeLog

    settings = config.load()
    n = TradeLog(settings.db_path).export_csv(Path(args.out))
    print(f"Exported {n} fill(s) to {args.out}")


def main():
    p = argparse.ArgumentParser(prog="trader")
    sub = p.add_subparsers(required=True)

    b = sub.add_parser("backtest", help="backtest a strategy against buy-and-hold")
    b.add_argument("strategy", choices=STRATEGIES)
    b.add_argument("--start", default="2010-01-01")
    b.add_argument("--end", default=None)
    b.add_argument("--cost-bps", type=float, default=5.0, help="cost per unit turnover, in basis points")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("run", help="compute today's orders (dry run unless --execute)")
    r.add_argument("strategy", choices=[k for k in STRATEGIES if not k.endswith("asx")])
    r.add_argument("--execute", action="store_true", help="actually submit orders")
    r.add_argument("--live", action="store_true", help="required in addition to ALPACA_PAPER=false")
    r.set_defaults(func=cmd_run)

    w = sub.add_parser("wheel", help="options wheel: plan puts/calls (dry run unless --execute)")
    w.add_argument("--execute", action="store_true", help="actually submit orders (US market hours only)")
    w.add_argument("--live", action="store_true", help="required in addition to ALPACA_PAPER=false")
    w.set_defaults(func=cmd_wheel)

    s = sub.add_parser("sync", help="pull filled orders from the broker into the trade log")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(func=cmd_sync)

    e = sub.add_parser("export", help="export the trade log to CSV")
    e.add_argument("--out", default=f"data/fills-{date.today()}.csv")
    e.set_defaults(func=cmd_export)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
