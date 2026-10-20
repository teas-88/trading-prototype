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
uv run trader sync                                    # pull fills from every account into data/trades.db
uv run trader export                                  # CSV of all fills, incl. AUD values
uv run pytest
```

Run `run` while the US market is closed (Australian morning), so signals use a final daily close. Orders queue and fill at the next US open. Run `sync` afterwards.

## Layout

| Module | Role |
|---|---|
| `strategies.py` | price history → target weights. The same code runs in backtests and live |
| `backtest.py` | daily simulation: signal at the close, fill at the next open, costs, metrics |
| `portfolio.py` | rebalancing rule (drift tolerance) shared by backtest and live |
| `broker.py` | broker interface plus Alpaca adapter (an IBKR adapter for ASX goes here) |
| `live.py` | diffs the target against the account, plans and submits orders, syncs fills |
| `tradelog.py` | SQLite fill log with AUD conversion; CSV export |

## Notes

- One strategy per Alpaca paper account. Each run only touches its own symbols. The capital cap compounds the
  account's P&L since its first run (baseline stored in the trade DB).
- Live trading needs both `ALPACA_PAPER=false` and `--live`.
- `data/` (the trade log) is git-ignored. Back it up separately.
- Data comes from yfinance (free, unofficial). Use a proper feed before trading real money.
- Backtests assume cash earns 0%, which slightly understates the strategies that sit in cash.
