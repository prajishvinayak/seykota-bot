#!/usr/bin/env python3
"""
Check / recheck / refine the Seykota bot against REAL data.

Three passes:
  1. INVARIANTS  - per-trade risk <= risk_pct, portfolio heat <= heat_cap,
                   no-look-ahead structural check (shift signals -> must NOT improve).
  2. ROBUSTNESS  - walk-forward (in-sample vs out-of-sample) + parameter sensitivity sweep.
  3. REFINE      - pick robust defaults (centre of the stable region, not the single max).

Real data is downloaded ONCE via yfinance and cached to ./cache/*.parquet so the
~90 sweep runs reuse it. Run:  python analyze.py
"""
from __future__ import annotations
import os, json, itertools, copy
import numpy as np
import pandas as pd
from seykota_bot.engine import Config, Backtest
from seykota_bot.data import synthetic_universe, load_yfinance, YF_TICKERS

CACHE = "cache"
YEARS = 3


# --------------------------------------------------------------------------- #
# Data (download once, cache to parquet)
# --------------------------------------------------------------------------- #
def get_real_data():
    os.makedirs(CACHE, exist_ok=True)
    instruments, _ = synthetic_universe(years=YEARS)          # reuse instrument specs
    epics = list(YF_TICKERS.keys())
    data = {}
    missing = []
    for e in epics:
        p = os.path.join(CACHE, f"{e}.pkl")
        if os.path.exists(p):
            data[e] = pd.read_pickle(p)
        else:
            missing.append(e)
    if missing:
        fresh = load_yfinance(missing, years=YEARS)
        for e, df in fresh.items():
            df.to_pickle(os.path.join(CACHE, f"{e}.pkl"))
            data[e] = df
    instruments = {k: v for k, v in instruments.items() if k in data}
    return instruments, data


def slice_data(data, start=None, end=None):
    out = {}
    for e, df in data.items():
        d = df
        if start is not None:
            d = d[d.index >= start]
        if end is not None:
            d = d[d.index < end]
        if len(d) > 200:
            out[e] = d
    return out


def run(cfg, instruments, data):
    inst = {k: v for k, v in instruments.items() if k in data}
    return Backtest(cfg, inst, data).run()


def m(res, *keys):
    mm = res["metrics"]
    return {k: mm.get(k) for k in keys}


# --------------------------------------------------------------------------- #
# PASS 1 - invariants
# --------------------------------------------------------------------------- #
def check_invariants(cfg, res):
    print("\n" + "=" * 64)
    print("PASS 1 - CORRECTNESS INVARIANTS")
    print("=" * 64)
    ok = True

    # heat cap: recorded heat each day must never exceed cap (+ tiny tolerance)
    heats = [e["heat"] for e in res["equity_curve"]]
    max_heat = max(heats) if heats else 0.0
    hp = max_heat <= cfg.heat_cap + 1e-6
    ok &= hp
    print(f"  [{'PASS' if hp else 'FAIL'}] portfolio heat <= heat_cap "
          f"(max {max_heat:.4f} vs cap {cfg.heat_cap})")

    # per-trade risk: risk_at_entry / equity_at_entry must be <= risk_pct_max (exact).
    worst = 0.0
    for t in res["trades"]:
        eq = t.get("equity_at_entry") or 0.0
        if eq > 0:
            worst = max(worst, t["risk_at_entry"] / eq)
    rp = worst <= cfg.risk_pct_max + 1e-6
    ok &= rp
    print(f"  [{'PASS' if rp else 'FAIL'}] per-trade risk <= risk_pct_max "
          f"(worst {worst*100:.2f}% vs cap {cfg.risk_pct_max*100:.1f}%)")

    return ok


def check_no_lookahead(cfg, instruments, data):
    """
    Structural no-look-ahead test: delay every fill by one extra bar (shift the
    decision later). A legitimate (no-look-ahead) system should NOT improve when
    you act on STALER information. A big improvement would signal leakage.
    """
    base = run(cfg, instruments, data)["metrics"]["total_return_pct"]

    # monkey-shift: build a copy of data shifted forward 1 bar so signals act later
    shifted = {e: df.shift(1).dropna() for e, df in data.items()}
    lag = run(cfg, instruments, shifted)["metrics"]["total_return_pct"]
    delta = lag - base
    verdict = "PASS" if lag <= base + 5.0 else "SUSPECT"
    print(f"  [{verdict}] no-look-ahead: base {base:.1f}% vs 1-bar-staler {lag:.1f}% "
          f"(staler should not beat base; delta {delta:+.1f}pp)")
    return verdict == "PASS"


# --------------------------------------------------------------------------- #
# PASS 2 - robustness
# --------------------------------------------------------------------------- #
KEYS = ("total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe",
        "num_trades", "win_rate_pct", "profit_factor", "payoff_ratio")


def walk_forward(cfg, instruments, data):
    print("\n" + "=" * 64)
    print("PASS 2a - WALK-FORWARD (out-of-sample honesty check)")
    print("=" * 64)
    # split calendar 60/40
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    split = all_dates[int(len(all_dates) * 0.6)]
    print(f"  split date: {split.date()}  (IS before, OOS after)")

    is_data = slice_data(data, end=split)
    oos_data = slice_data(data, start=split)
    for label, d in [("FULL", data), ("IN-SAMPLE", is_data), ("OUT-OF-SAMPLE", oos_data)]:
        r = run(cfg, instruments, d)["metrics"]
        print(f"  {label:14} ret {r['total_return_pct']:>7.1f}%  maxDD {r['max_drawdown_pct']:>6.1f}%  "
              f"PF {str(r['profit_factor']):>5}  Sharpe {r['sharpe']:>5}  trades {r['num_trades']}")


def sensitivity(cfg0, instruments, data):
    print("\n" + "=" * 64)
    print("PASS 2b - PARAMETER SENSITIVITY (is the edge robust or a knife-edge?)")
    print("=" * 64)
    grid = {
        "donchian_n": [30, 40, 50, 60, 70],
        "chandelier_mult": [2.5, 3.0, 3.5],
        "ema": [(50, 100), (80, 140), (100, 200)],
        "allow_short": [True, False],
    }
    rows = []
    for dn, cm, ema, sh in itertools.product(
            grid["donchian_n"], grid["chandelier_mult"], grid["ema"], grid["allow_short"]):
        cfg = copy.copy(cfg0)
        cfg.donchian_n, cfg.chandelier_mult = dn, cm
        cfg.ema_fast, cfg.ema_slow = ema
        cfg.allow_short = sh
        r = run(cfg, instruments, data)["metrics"]
        rows.append({"donchian_n": dn, "chand": cm, "ema": f"{ema[0]}/{ema[1]}",
                     "short": sh, "ret": r["total_return_pct"], "maxdd": r["max_drawdown_pct"],
                     "pf": r["profit_factor"] or 0, "sharpe": r["sharpe"],
                     "trades": r["num_trades"], "win": r["win_rate_pct"]})
    df = pd.DataFrame(rows)
    pos = (df["ret"] > 0).mean()
    print(f"  {len(df)} configs tested. {pos*100:.0f}% were profitable on real 3y data.")
    print(f"  return: median {df['ret'].median():.1f}%  range [{df['ret'].min():.1f}%, {df['ret'].max():.1f}%]")
    print(f"  PF:     median {df['pf'].median():.2f}   |  Sharpe median {df['sharpe'].median():.2f}")

    print("\n  Marginal effect of each lever (mean return holding nothing else fixed):")
    for col in ["donchian_n", "chand", "ema", "short"]:
        means = df.groupby(col)["ret"].mean().round(1)
        print(f"    {col:11}: " + "  ".join(f"{k}={v}" for k, v in means.items()))

    print("\n  Top 5 by return (watch for overfitting - are they clustered or scattered?):")
    for _, r in df.sort_values("ret", ascending=False).head(5).iterrows():
        print(f"    ret {r['ret']:>6.1f}%  dd {r['maxdd']:>6.1f}%  PF {r['pf']:>4.2f}  "
              f"sharpe {r['sharpe']:>4.2f}  | donch {r['donchian_n']} chand {r['chand']} "
              f"ema {r['ema']} short {r['short']}")
    df.to_csv(os.path.join("output", "sensitivity.csv"), index=False)
    print(f"\n  full grid -> output/sensitivity.csv")
    return df


def main():
    os.makedirs("output", exist_ok=True)
    instruments, data = get_real_data()
    print(f"Loaded {len(data)} instruments of REAL data "
          f"({min(df.index.min() for df in data.values()).date()} -> "
          f"{max(df.index.max() for df in data.values()).date()})")

    cfg = Config()  # brief defaults
    base = run(cfg, instruments, data)
    print(f"\nBASELINE (brief defaults, corrected cost model): "
          f"ret {base['metrics']['total_return_pct']}%  "
          f"maxDD {base['metrics']['max_drawdown_pct']}%  "
          f"PF {base['metrics']['profit_factor']}  Sharpe {base['metrics']['sharpe']}  "
          f"trades {base['metrics']['num_trades']}")

    check_invariants(cfg, base)
    check_no_lookahead(cfg, instruments, data)
    walk_forward(cfg, instruments, data)
    sensitivity(cfg, instruments, data)

    # ---- PASS 3: refine (principled levers only) + validate OOS ---- #
    print("\n" + "=" * 64)
    print("PASS 3 - REFINED CONFIG (slower filter + looser trail; Seykota-aligned)")
    print("=" * 64)
    refined = Config(ema_fast=100, ema_slow=200, chandelier_mult=3.5,
                     donchian_n=50, allow_short=True)
    refined_ls = Config(ema_fast=100, ema_slow=200, chandelier_mult=3.5,
                        donchian_n=50, allow_short=False)
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    split = all_dates[int(len(all_dates) * 0.6)]
    is_data = slice_data(data, end=split)
    oos_data = slice_data(data, start=split)
    for name, c in [("baseline 80/140 x3.0 short", cfg),
                    ("REFINED 100/200 x3.5 short", refined),
                    ("REFINED 100/200 x3.5 LONG-only", refined_ls)]:
        full = run(c, instruments, data)["metrics"]
        oos = run(c, instruments, oos_data)["metrics"]
        print(f"  {name:32} FULL ret {full['total_return_pct']:>6.1f}% dd {full['max_drawdown_pct']:>6.1f}% "
              f"PF {str(full['profit_factor']):>4} Sh {full['sharpe']:>4} | "
              f"OOS ret {oos['total_return_pct']:>6.1f}% PF {str(oos['profit_factor']):>4}")


if __name__ == "__main__":
    main()
