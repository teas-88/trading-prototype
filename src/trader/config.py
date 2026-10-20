import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class AlpacaAccount:
    name: str  # "default", or a strategy name such as "swing-us"
    key: str
    secret: str
    capital: float | None = None  # size trades as if the account held only this much


@dataclass(frozen=True)
class Settings:
    accounts: dict[str, AlpacaAccount]
    alpaca_paper: bool
    db_path: Path

    def account_for(self, strategy: str) -> AlpacaAccount | None:
        """The strategy's own account if configured, else the default one."""
        return self.accounts.get(strategy) or self.accounts.get("default")


def _accounts() -> dict[str, AlpacaAccount]:
    """ALPACA_API_KEY/ALPACA_SECRET_KEY is the default account; per-strategy accounts use
    e.g. ALPACA_SWING_US_API_KEY / ALPACA_SWING_US_SECRET_KEY. An optional
    ALPACA_[<STRATEGY>_]CAPITAL caps the money each strategy trades with."""
    found = {}
    for var, key in os.environ.items():
        m = re.fullmatch(r"ALPACA_(?:(\w+)_)?API_KEY", var)
        if not m or not key:
            continue
        prefix = f"ALPACA_{m[1]}_" if m[1] else "ALPACA_"
        secret = os.getenv(prefix + "SECRET_KEY")
        if secret:
            name = m[1].lower().replace("_", "-") if m[1] else "default"
            capital = os.getenv(prefix + "CAPITAL")
            found[name] = AlpacaAccount(name, key, secret, float(capital) if capital else None)
    return found


def load() -> Settings:
    return Settings(
        accounts=_accounts(),
        # Anything other than an explicit "false" keeps us on paper.
        alpaca_paper=os.getenv("ALPACA_PAPER", "true").strip().lower() != "false",
        db_path=ROOT / os.getenv("TRADE_DB", "data/trades.db"),
    )
