#!/usr/bin/env python3
"""
Refine the UNIVERSE and trade DIRECTION based on the multi-market evidence.
Each variant is scored on FULL 3y data AND out-of-sample (last 40%) so we don't
fool ourselves. Run:  python refine_universe.py
"""
from __future__ import annotations
import copy
import pandas as pd
from seykota_bot.engine import Config
from multi_market import load_all, make_instruments, GROUP, run


def slice_oos(data, frac=0.6):
    all_dates = sorted(set().union(*[set(d.index) for d in data.values()]))
    split = all_dates[int(len(all_dates) * frac)]
    oos = {e: d[d.index >= split] for e, d in data.items()}
    return {e: d for e, d in oos.items() if len(d) > 60}, split


def subset(data, drop_groups=()):
    return {e: d for e, d in data.items() if GROUP[e] not in drop_groups}


def score(cfg, instruments, data):
    m = run(cfg, instruments, data)["metrics"]
    return m


def main():
    data = load_all()
    inst = make_instruments(data)
    oos, split = slice_oos(data)
    print(f"Loaded {len(data)} markets. OOS split @ {split.date()}\n")

    variants = [
        ("ALL 39  · shorts on", data, dict(allow_short=True)),
        ("ALL 39  · long-only", data, dict(allow_short=False)),
        ("no FX   · long-only", subset(data, ("fx",)), dict(allow_short=False)),
        ("no FX+energy · long-only", subset(data, ("fx", "energy")), dict(allow_short=False)),
        ("no FX   · shorts on", subset(data, ("fx",)), dict(allow_short=True)),
        ("no FX+energy · shorts on", subset(data, ("fx", "energy")), dict(allow_short=True)),
        ("index+metal+stock · long-only", subset(data, ("fx", "energy", "ag")), dict(allow_short=False)),
    ]

    hdr = f"{'variant':32} {'mkts':>4} | {'FULL ret':>9} {'maxDD':>7} {'PF':>5} {'Sh':>5} {'MAR':>5} | {'OOS ret':>8} {'OOS PF':>6}"
    print(hdr); print("-" * len(hdr))
    for name, d, over in variants:
        cfg = Config(**over)
        full = score(cfg, inst, d)
        odata = {e: x for e, x in oos.items() if e in d}
        o = score(cfg, inst, odata)
        mar = full["mar_calmar"]
        print(f"{name:32} {len(d):>4} | {full['total_return_pct']:>8.1f}% "
              f"{full['max_drawdown_pct']:>6.1f}% {str(full['profit_factor']):>5} "
              f"{full['sharpe']:>5} {mar:>5} | {o['total_return_pct']:>7.1f}% {str(o['profit_factor']):>6}")

    print("\nRead: we want high MAR (return/maxDD) that HOLDS in the OOS column, "
          "with a structural reason for any market we drop — not just 'it lost'.")


if __name__ == "__main__":
    main()
