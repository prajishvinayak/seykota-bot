"""Shared fixtures: deterministic OHLC generators + a fake Capital.com client."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from seykota_bot.engine import Config, Instrument


def make_df(n=400, start=100.0, trend=0.4, vol=0.6, seed=0, reverse_at=None) -> pd.DataFrame:
    """Deterministic OHLC with a clean uptrend (so breakouts + trailing exits fire).
    If reverse_at is set, the trend flips down after that bar (produces stop-outs)."""
    rng = np.random.default_rng(seed)
    closes = np.empty(n)
    px = start
    for i in range(n):
        slope = trend if (reverse_at is None or i < reverse_at) else -trend * 1.5
        px = max(1.0, px + slope + rng.normal(0, vol))
        closes[i] = px
    high = closes + np.abs(rng.normal(0, vol, n))
    low = closes - np.abs(rng.normal(0, vol, n))
    open_ = np.concatenate([[start], closes[:-1]])
    high = np.maximum.reduce([high, open_, closes])
    low = np.minimum.reduce([low, open_, closes])
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": closes,
                         "volume": rng.integers(1000, 5000, n)}, index=idx)


@pytest.fixture
def small_cfg():
    """Fast params so trades happen in a few-hundred-bar test series."""
    return Config(ema_fast=5, ema_slow=15, donchian_n=10, atr_period=5,
                  chandelier_period=5, allow_short=False,
                  group_heat_cap=0.0, max_positions_per_group=0)


@pytest.fixture
def correlated_universe():
    """6 instruments, all in one 'equity' cluster, all trending up then reversing."""
    instruments, data = {}, {}
    for i in range(6):
        epic = f"EQ{i}"
        instruments[epic] = Instrument(epic=epic, group="index", corr_group="equity",
                                       value_per_point=1.0, min_size=0.01, size_step=0.01)
        data[epic] = make_df(seed=i + 1, reverse_at=300)
    return instruments, data


# --------------------------------------------------------------------------- #
# Fake Capital.com client (no network) for live-loop tests
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, data_rows: dict, positions=None, status="TRADEABLE",
                 equity=21000.0):
        self.environment = "demo"
        self.base = "fake"
        self._data = data_rows                 # epic -> list[row dicts]
        self._positions = positions or []      # list of {position, market}
        self.status = status
        self.equity = equity
        self.opened, self.amended, self.closed = [], [], []
        self.logged_in = False

    def login(self):
        self.logged_in = True

    def keepalive(self):
        return True

    def accounts(self):
        return [{"accountId": "X", "preferred": True, "currency": "GBP",
                 "balance": {"balance": self.equity, "available": self.equity,
                             "profitLoss": 0.0}}]

    def positions(self):
        return list(self._positions)

    def price_history(self, epic, resolution="DAY", **kw):
        return list(self._data.get(epic, []))

    def market(self, epic):
        return {"dealingRules": {"minDealSize": {"value": 0.01},
                                 "minSizeIncrement": {"value": 0.01}},
                "snapshot": {"marketStatus": self.status, "bid": 100, "offer": 100}}

    def open_position(self, epic, direction, size, stop_distance=None,
                      stop_level=None, **kw):
        assert stop_level is not None or stop_distance is not None, \
            "open_position called WITHOUT a protective stop"
        deal_id = f"deal-{epic}-{len(self.opened)}"
        self.opened.append({"epic": epic, "direction": direction, "size": size,
                            "stop_level": stop_level})
        return deal_id

    def amend_position(self, deal_id, stop_level=None, trailing=None):
        self.amended.append({"deal_id": deal_id, "stop_level": stop_level})
        return {}

    def close_position(self, deal_id):
        self.closed.append(deal_id)
        return {}


def df_to_rows(df: pd.DataFrame) -> list[dict]:
    return [{"snapshotTime": ts.strftime("%Y-%m-%dT%H:%M:%S"),
             "open": r.open, "high": r.high, "low": r.low, "close": r.close,
             "volume": r.volume} for ts, r in df.iterrows()]
