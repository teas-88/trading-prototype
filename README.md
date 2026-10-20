# trading-prototype

Personal trading prototype: a rules-based core that backtests, paper trades on Alpaca, and logs every fill (with AUD values) for tax.

## Setup

1. Install [uv](https://docs.astral.sh/uv/), then `uv sync`.
2. Copy `.env.example` to `.env` and add your Alpaca **paper** Trading API keys. Give each strategy its own paper account
   (`ALPACA_SWING_US_API_KEY` etc.); strategies without one use the default keys. `ALPACA_[<STRATEGY>_]CAPITAL`
   sizes trades as if the account held only that much.

## Usage

```
uv run trader backtest trend-spy --start 2010-01-01   # vs buy-and-hold; also momentum-us, swing-us, trend-asx, swing-asx
uv run trader run trend-spy                           # dry run: print today's orders
uv run trader run trend-spy --execute                 # submit them to the paper account
uv run trader wheel                                   # options wheel plan (dry run); --execute in US market hours
uv run trader sync                                    # pull fills + option assignments/expiries into data/trades.db
uv run trader export                                  # CSV of all fills, incl. AUD values
uv run pytest
```

Run `run` while the US market is closed (Australian morning), so signals use a final daily close. Orders queue and fill at the next US open. Run `sync` afterwards.

The wheel is different: option quotes are only meaningful while the US market is open (about 00:30-07:00 AEDT),
so `wheel --execute` refuses to trade when it's closed. Each run cancels unfilled orders from the previous run and re-plans.

## Scheduling (Windows)

```
powershell -ExecutionPolicy Bypass -File scripts/install-tasks.ps1          # register (re-run to update)
powershell -ExecutionPolicy Bypass -File scripts/install-tasks.ps1 -Remove  # unregister
```

Tue-Sat, Sydney time: 05:00 `wheel --execute`, 09:00 `run trend-spy` + `run swing-us-stocks --execute`, 09:30 `sync`.
Output goes to `data/logs/<date>-<job>.log`. Tasks run while you're logged in (a locked screen is fine; keep the PC
plugged in with sleep off) and catch up after a missed run.

## Layout

| Module | Role |
|---|---|
| `strategies.py` | price history → target weights. The same code runs in backtests and live |
| `backtest.py` | daily simulation: signal at the close, fill at the next open, costs, metrics |
| `portfolio.py` | rebalancing rule (drift tolerance) shared by backtest and live |
| `broker.py` | broker interface plus Alpaca adapter (an IBKR adapter for ASX goes here) |
| `live.py` | diffs the target against the account, plans and submits orders, syncs fills |
| `options.py` | OCC symbols, option quotes, contract selection by delta/DTE/spread |
| `wheel.py` | options wheel: cash-secured puts, covered calls above cost basis, 50% take-profit |
| `tradelog.py` | SQLite fill log with AUD conversion (x100 for options), option events; CSV export |

## Notes

- One strategy per Alpaca paper account. Each run only touches its own symbols. The capital cap compounds the
  account's P&L since its first run (baseline stored in the trade DB).
- Live trading needs both `ALPACA_PAPER=false` and `--live`.
- `data/` (the trade log) is git-ignored. Back it up separately.
- Data comes from yfinance (free, unofficial). Use a proper feed before trading real money.
- The wheel has no backtest (historical option quotes aren't free), so it's validated by forward paper trading.
  When a written put is assigned the premium generally adjusts the shares' cost base; confirm treatment with an accountant.
- Backtests assume cash earns 0%, which slightly understates the strategies that sit in cash.
