"""Criterion 3: no look-ahead in signals or backtest."""
from __future__ import annotations
import numpy as np
from seykota_bot.engine import Config, Instrument, Backtest, build_indicators
from .conftest import make_df


def test_donchian_uses_prior_bars_only():
    """donchian_hi/lo at bar t must exclude bar t (shifted), else entries self-match."""
    df = make_df(n=120, seed=3)
    cfg = Config(donchian_n=10)
    ind = build_indicators(df, cfg)
    # the donchian high at t should equal the max high over the PRIOR 10 bars
    for t in range(15, 120):
        expected = df["high"].iloc[t - 10:t].max()
        assert np.isclose(ind["donchian_hi"].iloc[t], expected)


def test_one_bar_shift_does_not_improve():
    """Acting on 1-bar-staler data must NOT beat the base run (no leakage)."""
    inst = {"X": Instrument(epic="X", value_per_point=1.0, min_size=0.01, size_step=0.01)}
    cfg = Config(ema_fast=5, ema_slow=15, donchian_n=10, atr_period=5, chandelier_period=5)
    base = Backtest(cfg, inst, {"X": make_df(seed=5, reverse_at=300)}).run()
    shifted = Backtest(cfg, inst, {"X": make_df(seed=5, reverse_at=300).shift(1).dropna()}).run()
    base_ret = base["metrics"]["total_return_pct"]
    lag_ret = shifted["metrics"]["total_return_pct"]
    assert lag_ret <= base_ret + 5.0, f"staler data improved results ({lag_ret} vs {base_ret})"
