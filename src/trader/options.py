"""Option contract helpers: OCC symbols, quotes, contract selection."""

import math
import re
from dataclasses import dataclass
from datetime import date

# OCC symbol: root, YYMMDD expiry, C/P, strike x 1000 (8 digits), e.g. XLF261120P00049000
OCC = re.compile(r"^([A-Z.]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiry: date
    kind: str  # "put" | "call"
    strike: float


def parse_occ(symbol: str) -> OptionContract | None:
    m = OCC.match(symbol)
    if not m:
        return None
    root, yy, mm, dd, cp, strike = m.groups()
    return OptionContract(symbol, root, date(2000 + int(yy), int(mm), int(dd)),
                          "call" if cp == "C" else "put", int(strike) / 1000)


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: float
    ask: float
    delta: float | None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        """Bid-ask spread as a fraction of the mid price."""
        return (self.ask - self.bid) / self.mid if self.mid > 0 else math.inf


def round_to_tick(price: float, up: bool) -> float:
    """US options tick in $0.01 below $3 and $0.05 above (penny-pilot rules, simplified)."""
    tick = 0.01 if price < 3 else 0.05
    steps = price / tick
    steps = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(max(steps, 1) * tick, 2)


def pick_contract(quotes: list[OptionQuote], kind: str, target_delta: float, delta_band: float,
                  min_dte: int, max_dte: int, max_spread: float, min_premium: float,
                  today: date, min_strike: float = 0.0) -> OptionQuote | None:
    """Contract closest to `target_delta` (absolute) within the filters; ties go to ~30 DTE."""
    eligible = []
    for q in quotes:
        c = q.contract
        dte = (c.expiry - today).days
        if (c.kind != kind or not (min_dte <= dte <= max_dte) or q.delta is None or q.bid <= 0
                or q.spread > max_spread or q.mid < min_premium or c.strike < min_strike):
            continue
        miss = abs(abs(q.delta) - target_delta)
        if miss <= delta_band:
            eligible.append((miss, abs(dte - 30), q))
    return min(eligible, key=lambda e: (e[0], e[1]))[2] if eligible else None


def annualised_yield(q: OptionQuote, today: date) -> float:
    """Premium as a yearly return on the cash the contract ties up (strike x 100)."""
    dte = max((q.contract.expiry - today).days, 1)
    return q.mid / q.contract.strike * 365 / dte
