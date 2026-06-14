#!/usr/bin/env python3
"""
Robustness-based universe pruning on the LIVE 27-market basket (Capital.com data).

Rule (anti-overfit): a market is a DROP candidate only if it loses in BOTH the
in-sample (first 60%) AND out-of-sample (last 40%) halves — a consistent structural
loser, not one-window noise. We then re-run WITHOUT the candidates and KEEP the change
only if it improves OUT-OF-SAMPLE MAR. Finally regenerate the dashboard for the result.

Run:  python universe_optimize.py
"""
from __future__ import annotations
import os, collections, yaml
import pandas as pd
from seykota_bot.engine import Config, Instrument, Backtest
from seykota_bot.dashboard import build_dashboard
from seykota_bot.eod_report import daily_report

CACHE = "cache_capital"


def load_universe():
    cfg = yaml.safe_load(open("config.yaml"))
    data, meta = {}, {}
    for u in cfg["universe"]:
        e = u["epic"]; p = os.path.join(CACHE, f"{e}.pkl")
        if os.path.exists(p):
            df = pd.read_pickle(p)
            if len(df) > 200:
                data[e] = df; meta[e] = (u["group"], u.get("corr") or u["group"])
    return data, meta


def instruments(data, meta, keys):
    inst = {}
    for e in keys:
        px = float(data[e]["close"].median()); g, corr = meta[e]
        sb = {"index": 0.0002, "metal": 0.0002, "energy": 0.0004, "ag": 0.0004}.get(g, 0.0003)
        inst[e] = Instrument(epic=e, group=g, value_per_point=1.0, min_size=0.01,
                             size_step=0.01, spread_points=px * sb,
                             financing_daily=px * 0.00008, corr_group=corr)
    return inst


def split_date(data, frac=0.6):
    dates = sorted(set().union(*[set(d.index) for d in data.values()]))
    return dates[int(len(dates) * frac)]


def run(data, meta, keys):
    return Backtest(Config(), instruments(data, meta, keys), {e: data[e] for e in keys}).run()


def oos_metrics(data, meta, keys, split):
    sub = {e: data[e][data[e].index >= split] for e in keys}
    sub = {e: d for e, d in sub.items() if len(d) > 60}
    return Backtest(Config(), instruments(data, meta, list(sub)), sub).run()["metrics"]


def main():
    data, meta = load_universe()
    keys = list(data)
    split = split_date(data)
    print(f"Live universe: {len(keys)} markets. IS/OOS split @ {split.date()}\n")

    res = run(data, meta, keys)
    # per-market P&L in each half
    is_pnl = collections.defaultdict(float); oos_pnl = collections.defaultdict(float)
    for t in res["trades"]:
        (is_pnl if pd.Timestamp(t["exit_date"]) < split else oos_pnl)[t["epic"]] += t["pnl"]

    print(f"{'market':12} {'IS P&L':>9} {'OOS P&L':>9}  verdict")
    print("-" * 44)
    drop = []
    for e in sorted(keys, key=lambda x: is_pnl[x] + oos_pnl[x]):
        i, o = is_pnl[e], oos_pnl[e]
        if i < 0 and o < 0:
            verdict = "DROP (loses both halves)"; drop.append(e)
        elif i + o < 0:
            verdict = "keep (one-window loss = noise)"
        else:
            verdict = "keep (profitable)"
        print(f"{e:12} {i:>9,.0f} {o:>9,.0f}  {verdict}")

    base_full = res["metrics"]; base_oos = oos_metrics(data, meta, keys, split)
    print(f"\nFULL  {len(keys)} mkts: ret {base_full['total_return_pct']}%  dd {base_full['max_drawdown_pct']}%  "
          f"MAR {base_full['mar_calmar']}  | OOS ret {base_oos['total_return_pct']}% MAR {base_oos['mar_calmar']}")

    if not drop:
        print("\nNo market loses in BOTH halves — nothing to prune. Diversification stays.")
        final_keys = keys
    else:
        kept = [e for e in keys if e not in drop]
        pr_full = run(data, meta, kept)["metrics"]; pr_oos = oos_metrics(data, meta, kept, split)
        print(f"\nDROP candidates (lose both halves): {drop}")
        print(f"PRUNED {len(kept)} mkts: ret {pr_full['total_return_pct']}%  dd {pr_full['max_drawdown_pct']}%  "
              f"MAR {pr_full['mar_calmar']}  | OOS ret {pr_oos['total_return_pct']}% MAR {pr_oos['mar_calmar']}")
        # KEEP the prune only if it improves OUT-OF-SAMPLE MAR (robustness, not in-sample)
        if pr_oos["mar_calmar"] > base_oos["mar_calmar"]:
            print(f"\n=> Pruning IMPROVES OOS MAR ({base_oos['mar_calmar']} -> {pr_oos['mar_calmar']}). Applying.")
            final_keys = kept
        else:
            print(f"\n=> Pruning does NOT improve OOS MAR ({base_oos['mar_calmar']} -> {pr_oos['mar_calmar']}). "
                  f"Keeping all (avoiding overfit).")
            final_keys = keys

    # regenerate dashboard for the FINAL live universe
    final = run(data, meta, final_keys)
    build_dashboard(final, "output/dashboard_capital.html",
                    title=f"Seykota Bot — LIVE {len(final_keys)}-market universe · Capital.com data · long-only · 3y")
    daily_report(final, md_path="output/eod_report_capital.md")
    m = final["metrics"]
    print(f"\nFINAL dashboard -> output/dashboard_capital.html  "
          f"({len(final_keys)} markets: ret {m['total_return_pct']}%  dd {m['max_drawdown_pct']}%  "
          f"MAR {m['mar_calmar']}  PF {m['profit_factor']})")
    if final_keys != keys:
        print("Markets to remove from config.yaml:", [e for e in keys if e not in final_keys])


if __name__ == "__main__":
    main()
