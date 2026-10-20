"""The options wheel: sell cash-secured puts; if assigned, sell covered calls; repeat.

The planner is stateless: everything it needs is read from the account each run (shares
held, short options open), so it can't drift out of sync with the broker.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from trader.broker import Broker, Position
from trader.data import load_prices
from trader.live import strategy_equity
from trader.options import OptionQuote, annualised_yield, parse_occ, pick_contract, round_to_tick
from trader.tradelog import TradeLog

NAME = "wheel"


@dataclass(frozen=True)
class WheelConfig:
    # Liquid options, share prices that a ~$10k account can secure (100 shares per contract).
    underlyings: tuple[str, ...] = ("XLF", "EEM", "XLE", "XLU", "XLP", "KRE", "BAC", "T", "VZ", "PFE", "CSCO", "KO")
    put_delta: float = 0.25
    call_delta: float = 0.30
    delta_band: float = 0.10
    min_dte: int = 21
    max_dte: int = 45
    take_profit: float = 0.50  # buy back once this fraction of the premium is earned
    max_spread: float = 0.20  # skip contracts whose bid-ask spread exceeds 20% of mid
    min_premium: float = 0.10  # per share, i.e. $10 a contract
    trend_sma_days: int = 200  # only sell puts on underlyings above this average
    max_position: float = 0.50  # max collateral per put as a fraction of equity (diversification)


@dataclass(frozen=True)
class WheelAction:
    kind: str  # "sell_put" | "sell_call" | "buy_to_close"
    symbol: str
    qty: int
    limit: float
    reason: str

    @property
    def side(self) -> str:
        return "buy" if self.kind == "buy_to_close" else "sell"


def plan_wheel(cfg: WheelConfig, equity: float, positions: dict[str, Position],
               chains: dict[str, list[OptionQuote]], held_quotes: dict[str, OptionQuote],
               uptrend: dict[str, bool], today: date) -> list[WheelAction]:
    shares = {s: p for s, p in positions.items() if parse_occ(s) is None and s in cfg.underlyings}
    shorts = [(parse_occ(s), p) for s, p in positions.items() if parse_occ(s) and p.qty < 0]
    actions = []

    # 1. Take profits on short options.
    for c, p in shorts:
        q = held_quotes.get(c.symbol)
        if q and q.ask > 0 and q.ask <= p.avg_entry_price * (1 - cfg.take_profit):
            actions.append(WheelAction("buy_to_close", c.symbol, int(-p.qty), round_to_tick(q.ask, up=True),
                                       f"{1 - q.ask / p.avg_entry_price:.0%} of premium captured"))

    # 2. Covered calls on assigned shares, never below what the shares cost.
    for u, p in shares.items():
        covered = sum(-sp.qty for c, sp in shorts if c.underlying == u and c.kind == "call")
        uncovered = int(p.qty // 100) - int(covered)
        if uncovered <= 0:
            continue
        q = pick_contract(chains.get(u, []), "call", cfg.call_delta, cfg.delta_band, cfg.min_dte,
                          cfg.max_dte, cfg.max_spread, cfg.min_premium, today, min_strike=p.avg_entry_price)
        if q:
            actions.append(WheelAction("sell_call", q.contract.symbol, uncovered, round_to_tick(q.mid, up=False),
                                       f"covered call, cost basis ${p.avg_entry_price:.2f}"))

    # 3. New cash-secured puts, best yield first, while the cash cap allows.
    in_use = sum(p.market_value for p in shares.values()) + \
        sum(c.strike * 100 * -p.qty for c, p in shorts if c.kind == "put")
    free = equity - in_use
    busy = set(shares) | {c.underlying for c, _ in shorts}
    candidates = []
    for u in cfg.underlyings:
        if u in busy or not uptrend.get(u, False):
            continue
        q = pick_contract(chains.get(u, []), "put", cfg.put_delta, cfg.delta_band, cfg.min_dte,
                          cfg.max_dte, cfg.max_spread, cfg.min_premium, today)
        if q:
            candidates.append(q)
    for q in sorted(candidates, key=lambda q: annualised_yield(q, today), reverse=True):
        collateral = q.contract.strike * 100
        if collateral > free or collateral > cfg.max_position * equity:
            continue
        free -= collateral
        actions.append(WheelAction("sell_put", q.contract.symbol, 1, round_to_tick(q.mid, up=False),
                                   f"{annualised_yield(q, today):.0%}/yr on ${collateral:,.0f} collateral, "
                                   f"delta {q.delta:.2f}"))
    return actions


def run_wheel(cfg: WheelConfig, broker: Broker, log: TradeLog, dry_run: bool = True,
              capital: float | None = None) -> list[WheelAction]:
    if not broker.market_open():
        if not dry_run:
            print("US market is closed: option quotes are stale, not trading. Run during US market hours.")
            return []
        print("Warning: US market is closed, quotes are stale; this plan is indicative only.")
    elif not dry_run:
        broker.cancel_open_orders()  # yesterday's unfilled limits; we re-plan from scratch

    today = date.today()
    positions = broker.positions()
    account_equity = broker.equity()
    equity = strategy_equity(account_equity, log.baseline(broker.name, account_equity), capital)

    closes = load_prices(list(cfg.underlyings), start=today - timedelta(days=cfg.trend_sma_days * 2))
    uptrend = {u: bool(closes[u].dropna().iloc[-1] > closes[u].dropna().iloc[-cfg.trend_sma_days:].mean())
               for u in cfg.underlyings if closes[u].notna().sum() >= cfg.trend_sma_days}
    chains = {u: broker.option_quotes(u, today + timedelta(days=cfg.min_dte), today + timedelta(days=cfg.max_dte))
              for u in cfg.underlyings if uptrend.get(u) or u in positions}
    held = broker.option_snapshots([s for s in positions if parse_occ(s)])

    actions = plan_wheel(cfg, equity, positions, chains, held, uptrend, today)
    print(f"wheel: equity ${equity:,.2f}; in uptrend: {sorted(u for u, up in uptrend.items() if up)}")
    for a in actions:
        desc = f"{a.kind} {a.qty} x {a.symbol} @ ${a.limit:.2f} ({a.reason})"
        if dry_run:
            print(f"  [dry run] {desc}")
            continue
        print(f"  submitted {desc} ({broker.submit_limit_order(a.symbol, a.qty, a.side, a.limit, NAME)})")
    if not actions:
        print("  nothing to do")
    return actions
