"""Indicator correctness + bad-tick cleaning."""
from __future__ import annotations
import numpy as np
import pandas as pd
from seykota_bot.engine import Config, atr_wilder, ema, build_indicators
from seykota_bot.data import clean_spikes
from .conftest import make_df


def test_atr_wilder_matches_definition():
    df = make_df(n=60, seed=2)
    period = 5
    pc = df["close"].shift(1)
    tr = pd.concat([(df.high - df.low), (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    expected = tr.ewm(alpha=1 / period, adjust=False).mean()
    got = atr_wilder(df, period)
    assert np.allclose(got.values, expected.values, equal_nan=True)
    assert (got.dropna() > 0).all()


def test_ema_and_uptrend_flag():
    df = make_df(n=100, seed=4)
    cfg = Config(ema_fast=5, ema_slow=15)
    ind = build_indicators(df, cfg)
    assert np.allclose(ind["ema_fast"], ema(df["close"], 5))
    assert (ind["uptrend"] == (ind["ema_fast"] > ind["ema_slow"])).all()


def test_clean_spikes_repairs_bad_tick():
    """A single 10x-down bad tick (the NVDA glitch) must be repaired to ~neighbours."""
    df = make_df(n=50, seed=1)
    df.iloc[25, df.columns.get_loc("close")] = df["close"].iloc[25] / 10.0   # bad tick
    df.iloc[25, df.columns.get_loc("low")] = df["low"].iloc[25] / 10.0
    cleaned, n = clean_spikes(df)
    assert n == 1
    neighbour_avg = (df["close"].iloc[24] + df["close"].iloc[26]) / 2
    assert np.isclose(cleaned["close"].iloc[25], neighbour_avg)


def test_clean_spikes_leaves_clean_data_untouched():
    df = make_df(n=80, seed=6)
    cleaned, n = clean_spikes(df)
    assert n == 0
    assert np.allclose(cleaned["close"], df["close"])
