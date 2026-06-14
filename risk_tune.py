#!/usr/bin/env python3
"""
Tune the DRAWDOWN controls (brief §3.3-3.4) without wrecking return. Tests
correlated-cluster caps + overall heat/risk on BOTH the Capital.com feed (primary)
and the Yahoo feed, full-sample and out-of-sample. Goal: raise MAR (return/maxDD)
and cut maxDD while keeping a similar return and holding up OOS.

Uses cached data (run capital_backtest.py and multi_market.py first). Run: python risk_tune.py
"""
from __future__ import annotations
import os, copy
import pandas as pd
from seykota_bot.engine import Config, Instrument, Backtest
from capital_backtest import WANT
from multi_market import UNIVERSE as YF_UNIVERSE

CAP_CACHE, YF_CACHE = "cache_capital", "cache"


def _load(cache, group_of):
    data, groups = {}, {}
    for label, g in group_of.items():
        p = os.path.join(cache, f"{label}.pkl")
        if os.path.exists(p):
            data[label] = pd.read_pickle(p); groups[label] = g
    return data, groups


def _instruments(data, groups):
    inst = {}
    for e, df in data.items():
        px = float(df["close"].median()); g = groups[e]
        spread_bp = {"index": 0.0002, "stock": 0.0003, "metal": 0.0002,
                     "energy": 0.0004, "ag": 0.0004}.get(g, 0.0003)
        inst[e] = Instrument(epic=e, group=g, value_per_point=1.0, min_size=0.01,
                             size_step=0.01, spread_points=px * spread_bp,
                             financing_daily=px * 0.00008,
                             corr_group="equity" if g in ("index", "stock") else g)
    return inst


def _oos(data, frac=0.6):
    dates = sorted(set().union(*[set(d.index) for d in data.values()]))
    split = dates[int(len(dates) * frac)]
    return {e: d[d.index >= split] for e, d in data.items()}


def run(cfg, inst, data):
    return Backtest(cfg, {k: v for k, v in inst.items() if k in data}, data).run()["metrics"]


VARIANTS = [
    ("baseline (no cluster caps)", dict()),
    ("max 4 / cluster",            dict(max_positions_per_group=4)),
    ("max 3 / cluster",            dict(max_positions_per_group=3)),
    ("cluster heat 6%",            dict(group_heat_cap=0.06)),
    ("cluster heat 6% + max3",     dict(group_heat_cap=0.06, max_positions_per_group=3)),
    ("cluster heat 4% + max3",     dict(group_heat_cap=0.04, max_positions_per_group=3)),
    ("overall heat 10% (no clstr)",dict(heat_cap=0.10)),
    ("risk 0.5% (half)",           dict(risk_pct=0.005)),
]


def sweep(name, data, groups):
    inst = _instruments(data, groups)
    oos = _oos(data)
    print(f"\n################  {name}  ({len(data)} markets)  ################")
    hdr = f"{'variant':30} | {'ret':>7} {'maxDD':>7} {'MAR':>5} {'Sh':>5} {'PF':>5} {'trd':>4} | {'OOS ret':>8} {'OOSdd':>7} {'OOS MAR':>7}"
    print(hdr); print("-" * len(hdr))
    for label, over in VARIANTS:
        cfg = Config(**over)
        f = run(cfg, inst, data)
        o = run(cfg, inst, oos)
        print(f"{label:30} | {f['total_return_pct']:>6.1f}% {f['max_drawdown_pct']:>6.1f}% "
              f"{f['mar_calmar']:>5} {f['sharpe']:>5} {str(f['profit_factor']):>5} {f['num_trades']:>4} | "
              f"{o['total_return_pct']:>7.1f}% {o['max_drawdown_pct']:>6.1f}% {o['mar_calmar']:>7}")


def main():
    cap, capg = _load(CAP_CACHE, {k: v[1] for k, v in WANT.items()})
    yf, yfg = _load(YF_CACHE, {k: v[1] for k, v in YF_UNIVERSE.items() if v[1] != "fx"})
    if cap:
        sweep("CAPITAL.COM data (primary)", cap, capg)
    if yf:
        sweep("YAHOO data (cross-check)", yf, yfg)
    print("\nPick the row that cuts maxDD most while keeping return & holding up OOS MAR.")


if __name__ == "__main__":
    main()
