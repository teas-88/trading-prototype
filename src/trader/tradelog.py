"""SQLite log of every fill, with AUD values for ATO reporting.

FX uses the market AUD/<ccy> close on the fill's UTC date. That's a reasonable approximation;
check with your accountant whether they want RBA rates instead before lodging anything.
Option assignments/expiries are kept raw in `option_events`: when a written put is assigned,
the premium generally adjusts the cost base of the shares acquired, which needs both records.
"""

import csv
import json
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
    recorded_at     TEXT NOT NULL,
    multiplier      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS baselines (
    account     TEXT PRIMARY KEY,
    equity      REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS option_events (
    id          TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    type        TEXT NOT NULL,
    occurred_at TEXT,
    raw         TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
"""


class TradeLog:
    def __init__(self, path: Path, fx: Callable[[str, date], float] = aud_per_unit):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(fills)")}
        if "multiplier" not in columns:  # databases created before options support
            self.conn.execute("ALTER TABLE fills ADD COLUMN multiplier INTEGER NOT NULL DEFAULT 1")
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
        row = {
            "broker_order_id": fill.broker_order_id, "broker": broker, "paper": int(paper),
            "strategy": fill.strategy, "symbol": fill.symbol, "side": fill.side, "qty": fill.qty,
            "price": fill.price, "fees": fill.fees, "currency": fill.currency,
            "filled_at": fill.filled_at.isoformat(), "aud_per_unit": rate,
            "value_aud": fill.qty * fill.price * fill.multiplier * rate, "fees_aud": fill.fees * rate,
            "recorded_at": datetime.now(timezone.utc).isoformat(), "multiplier": fill.multiplier,
        }
        with self.conn:
            self.conn.execute(f"INSERT INTO fills ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values()))
        return True

    def record_option_event(self, account: str, event: dict) -> bool:
        if self.conn.execute("SELECT 1 FROM option_events WHERE id = ?", (event["id"],)).fetchone():
            return False
        with self.conn:
            self.conn.execute("INSERT INTO option_events VALUES (?,?,?,?,?,?)",
                              (event["id"], account, event.get("activity_type", "?"),
                               event.get("date") or event.get("transaction_time"),
                               json.dumps(event), datetime.now(timezone.utc).isoformat()))
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
