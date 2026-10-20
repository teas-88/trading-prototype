from datetime import date, datetime, timezone

import pytest

from trader.broker import Fill, Position
from trader.options import OptionQuote, parse_occ, pick_contract, round_to_tick
from trader.tradelog import TradeLog
from trader.wheel import WheelConfig, plan_wheel

TODAY = date(2026, 10, 20)
CFG = WheelConfig(underlyings=("XLF", "KO"))


def quote(symbol, bid, ask, delta):
    return OptionQuote(parse_occ(symbol), bid, ask, delta)


def test_parse_occ():
    c = parse_occ("XLF261120P00049000")
    assert (c.underlying, c.expiry, c.kind, c.strike) == ("XLF", date(2026, 11, 20), "put", 49.0)
    assert parse_occ("XLF261120C00052500").strike == 52.5
    assert parse_occ("SPY") is None


def test_round_to_tick():
    assert round_to_tick(1.234, up=False) == 1.23
    assert round_to_tick(1.231, up=True) == 1.24
    assert round_to_tick(3.42, up=False) == 3.40
    assert round_to_tick(3.42, up=True) == 3.45
    assert round_to_tick(0.004, up=False) == 0.01  # never zero


def test_pick_contract_filters_and_prefers_target_delta():
    quotes = [
        quote("XLF261120P00049000", 0.40, 0.44, -0.20),
        quote("XLF261120P00050000", 0.60, 0.64, -0.26),  # closest to 0.25
        quote("XLF261120P00051000", 0.50, 1.20, -0.25),  # spread too wide
        quote("XLF261030P00050000", 0.30, 0.32, -0.25),  # only 10 DTE
        quote("XLF261120C00055000", 0.50, 0.54, 0.25),  # wrong kind
    ]
    q = pick_contract(quotes, "put", 0.25, 0.10, 21, 45, 0.20, 0.10, TODAY)
    assert q.contract.symbol == "XLF261120P00050000"


def test_sells_put_when_flat_and_respects_cash_cap():
    chains = {"XLF": [quote("XLF261120P00050000", 0.60, 0.64, -0.25)],
              "KO": [quote("KO261120P00065000", 0.90, 0.96, -0.25)]}
    no_position_cap = WheelConfig(underlyings=("XLF", "KO"), max_position=1.0)
    actions = plan_wheel(no_position_cap, 10_000, {}, chains, {}, {"XLF": True, "KO": True}, TODAY)
    # $10k secures one of them ($5k or $6.5k) but not both; KO has the better yield.
    assert [(a.kind, a.symbol) for a in actions] == [("sell_put", "KO261120P00065000")]
    assert actions[0].limit == 0.93
    # With room for both, both are sold, best yield first.
    actions = plan_wheel(CFG, 14_000, {}, chains, {}, {"XLF": True, "KO": True}, TODAY)
    assert [a.symbol for a in actions] == ["KO261120P00065000", "XLF261120P00050000"]


def test_no_single_put_above_max_position():
    chains = {"KO": [quote("KO261120P00065000", 0.90, 0.96, -0.25)]}
    assert plan_wheel(CFG, 10_000, {}, chains, {}, {"KO": True}, TODAY) == []  # $6.5k > 50% of $10k


def test_no_puts_in_downtrend_or_when_already_wheeling():
    chains = {"XLF": [quote("XLF261120P00050000", 0.60, 0.64, -0.25)]}
    assert plan_wheel(CFG, 10_000, {}, chains, {}, {"XLF": False}, TODAY) == []
    open_put = {"XLF261113P00049000": Position("XLF261113P00049000", -1, -40.0, 0.55)}
    assert plan_wheel(CFG, 50_000, open_put, chains, {}, {"XLF": True}, TODAY) == []


def test_covered_call_after_assignment_at_or_above_cost_basis():
    positions = {"XLF": Position("XLF", 100, 4800.0, 49.40)}
    chains = {"XLF": [quote("XLF261120C00049000", 0.90, 0.95, 0.31),  # below basis: never
                      quote("XLF261120C00050000", 0.55, 0.60, 0.28)]}
    actions = plan_wheel(CFG, 10_000, positions, chains, {}, {"XLF": True}, TODAY)
    assert [(a.kind, a.symbol, a.qty) for a in actions] == [("sell_call", "XLF261120C00050000", 1)]


def test_buy_to_close_at_half_profit():
    sym = "XLF261120P00049000"
    positions = {sym: Position(sym, -1, -25.0, 0.60)}
    held = {sym: quote(sym, 0.24, 0.26, -0.10)}
    actions = plan_wheel(CFG, 10_000, positions, {}, held, {}, TODAY)
    assert [(a.kind, a.qty, a.limit) for a in actions] == [("buy_to_close", 1, 0.26)]
    held = {sym: quote(sym, 0.40, 0.42, -0.18)}  # only 30% captured: hold
    assert plan_wheel(CFG, 10_000, positions, {}, held, {}, TODAY) == []


def test_option_fill_value_uses_multiplier(tmp_path):
    log = TradeLog(tmp_path / "t.db", fx=lambda ccy, day: 1.5)
    fill = Fill("o1", "wheel", "XLF261120P00049000", "sell", 1, 0.60, 0.0, "USD",
                datetime(2026, 10, 21, 15, tzinfo=timezone.utc), multiplier=100)
    log.record(fill, "alpaca:default", paper=True)
    assert log.conn.execute("SELECT value_aud FROM fills").fetchone()[0] == pytest.approx(90.0)


def test_tradelog_migrates_old_schema(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE fills (broker_order_id TEXT PRIMARY KEY, broker TEXT, paper INTEGER, strategy TEXT, "
                "symbol TEXT, side TEXT, qty REAL, price REAL, fees REAL, currency TEXT, filled_at TEXT, "
                "aud_per_unit REAL, value_aud REAL, fees_aud REAL, recorded_at TEXT)")
    old.commit()
    old.close()
    log = TradeLog(path, fx=lambda ccy, day: 1.0)
    assert "multiplier" in {r[1] for r in log.conn.execute("PRAGMA table_info(fills)")}


def test_option_events_dedupe(tmp_path):
    log = TradeLog(tmp_path / "t.db", fx=lambda ccy, day: 1.0)
    event = {"id": "e1", "activity_type": "OPASN", "date": "2026-11-20"}
    assert log.record_option_event("alpaca:default", event)
    assert not log.record_option_event("alpaca:default", event)
