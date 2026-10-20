"""SQLite log of every fill, with AUD values for ATO reporting.

FX uses the market AUD/<ccy> close on the fill's UTC date. That's a reasonable approximation;
check with your accountant whether they want RBA rates instead before lodging anything.
"""

import csv
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from trader.broker import Fill
from trader.data import aud_per_unit

SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    broker_order_id TEXT PRIMARY KEY,
    broker          TEXT NOT NULL,
    paper           INTEGER NOT NULL,
    strategy        TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             REAL NOT NULL,
    price           REAL NOT NULL,
    fees            REAL NOT NULL,
    currency        TEXT NOT NULL,
    filled_at       TEXT NOT NULL,
    aud_per_unit    REAL NOT NULL,
    value_aud       REAL NOT NULL,
    fees_aud        REAL NOT NULL,
    recorded_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baselines (
    account     TEXT PRIMARY KEY,
    equity      REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
"""


class TradeLog:
    def __init__(self, path: Path, fx: Callable[[str, date], float] = aud_per_unit):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.fx = fx

    def baseline(self, account: str, equity_now: float) -> float:
        """Account equity when we first traded it; recorded on first call."""
        row = self.conn.execute("SELECT equity FROM baselines WHERE account = ?", (account,)).fetchone()
        if row:
            return row[0]
        with self.conn:
            self.conn.execute("INSERT INTO baselines VALUES (?,?,?)",
                              (account, equity_now, datetime.now(timezone.utc).isoformat()))
        return equity_now

    def record(self, fill: Fill, broker: str, paper: bool) -> bool:
        """Insert a fill; returns False if it was already logged."""
        if self.conn.execute("SELECT 1 FROM fills WHERE broker_order_id = ?", (fill.broker_order_id,)).fetchone():
            return False
        rate = self.fx(fill.currency, fill.filled_at.astimezone(timezone.utc).date())
        with self.conn:
            self.conn.execute(
                "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fill.broker_order_id, broker, int(paper), fill.strategy, fill.symbol, fill.side,
                 fill.qty, fill.price, fill.fees, fill.currency, fill.filled_at.isoformat(),
                 rate, fill.qty * fill.price * rate, fill.fees * rate,
                 datetime.now(timezone.utc).isoformat()),
            )
        return True

    def export_csv(self, path: Path) -> int:
        cur = self.conn.execute("SELECT * FROM fills ORDER BY filled_at")
        rows = cur.fetchall()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cur.description])
            w.writerows(rows)
        return len(rows)
