"""Rebalancing rule shared by the backtest and live trading so they make the same trades."""

DRIFT_TOLERANCE = 0.05  # don't trade a position unless it's off target by >= 5% of equity


def apply_target(target: dict[str, float], current: dict[str, float],
                 tolerance: float = DRIFT_TOLERANCE) -> dict[str, float]:
    """Weights after trading `current` toward `target`. Symbols with a zero target are closed;
    positions within `tolerance` of their target are left alone to avoid churn."""
    after = {}
    for s in target.keys() | current.keys():
        want, have = target.get(s, 0.0), current.get(s, 0.0)
        if want == 0:
            continue
        w = want if abs(want - have) >= tolerance else have
        if w > 0:
            after[s] = w
    return after
