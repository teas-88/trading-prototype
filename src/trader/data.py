"""Market data. yfinance is free and covers US and ASX (".AX") daily bars, but it is
unofficial and can break; swap for a paid feed before relying on it with real money."""

from datetime import date, timedelta
from functools import lru_cache

import pandas as pd
import yfinance as yf


def _field(df: pd.DataFrame, field: str, symbols: list[str]) -> pd.DataFrame:
    out = df[field]
    if isinstance(out, pd.Series):
        out = out.to_frame(symbols[0])
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out


def load_bars(symbols: list[str], start: date | str,
              end: date | str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily (opens, closes) adjusted for splits and dividends (i.e. total-return prices)."""
    symbols = sorted(set(symbols))
    df = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    if df.empty:
        raise RuntimeError(f"No price data returned for {symbols}")
    closes = _field(df, "Close", symbols)[symbols].dropna(how="all")
    opens = _field(df, "Open", symbols)[symbols].reindex(closes.index)
    return opens, closes


def load_prices(symbols: list[str], start: date | str, end: date | str | None = None) -> pd.DataFrame:
    return load_bars(symbols, start, end)[1]


@lru_cache(maxsize=1024)
def aud_per_unit(currency: str, day: date) -> float:
    """How many AUD one unit of `currency` was worth on `day` (last close on or before it)."""
    if currency == "AUD":
        return 1.0
    pair = f"AUD{currency}=X"  # quoted as units of `currency` per 1 AUD
    df = yf.download(pair, start=day - timedelta(days=7), end=day + timedelta(days=1),
                     auto_adjust=False, progress=False)
    rates = _field(df, "Close", [pair]).iloc[:, 0].dropna()
    if rates.empty:
        raise RuntimeError(f"No FX rate for {pair} around {day}")
    return 1.0 / float(rates.iloc[-1])
