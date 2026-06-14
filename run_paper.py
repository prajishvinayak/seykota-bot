#!/usr/bin/env python3
"""
Run the Seykota bot against Capital.com (DEMO by default — see config.yaml).

  python run_paper.py --dry-run        # ONE cycle, places NO orders (validate first!)
  python run_paper.py --once           # ONE real demo cycle (opens/trails/reports)
  python run_paper.py --loop           # run once per day at mode.run_time_utc, forever
  python run_paper.py --flatten        # close ALL positions now (manual kill switch)

Strategy/risk come from config.yaml; secrets from .env. 'live' needs an explicit
typed confirmation. The daily report is written to output/eod_report_live.md —
paste it back to Claude for review.
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timezone, timedelta

import yaml

from seykota_bot.engine import Config
from seykota_bot.capital_client import CapitalClient
from seykota_bot.store import Store
from seykota_bot.live import LiveTrader


def load_config(path="config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build(settings: dict):
    st = settings["strategy"]
    rk = settings["risk"]
    cfg = Config(
        ema_fast=st["ema_fast"], ema_slow=st["ema_slow"], donchian_n=st["donchian_n"],
        atr_period=st["atr_period"], chandelier_period=st["chandelier_period"],
        chandelier_mult=st["chandelier_mult"], initial_stop_atr_mult=st["initial_stop_atr_mult"],
        use_structure_stop=st.get("use_structure_stop", True),
        swing_lookback=st.get("swing_lookback", 10), allow_short=st.get("allow_short", False),
        risk_pct=rk["risk_pct"], risk_pct_max=rk.get("risk_pct_max", 0.01),
        heat_cap=rk["heat_cap"], group_heat_cap=rk.get("group_heat_cap", 0.06),
        max_positions_per_group=rk.get("max_positions_per_group", 3),
        drawdown_derisk=rk.get("drawdown_derisk", True),
        max_drawdown_pause=rk.get("max_drawdown_pause", 0.25),
    )
    env = settings["mode"]["environment"].lower()
    os.environ["CAPITAL_ENVIRONMENT"] = env       # ensure client picks demo/live from config
    client = CapitalClient.from_env()
    store = Store(settings["paths"]["state_file"], settings["paths"]["journal_file"])
    trader = LiveTrader(client, cfg, settings, settings["universe"], store)
    return trader, env


def confirm_live(env: str):
    if env == "live":
        print("\n*** LIVE MODE — REAL MONEY ***")
        if input('Type exactly "TRADE LIVE" to proceed: ').strip() != "TRADE LIVE":
            print("Aborted."); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="one cycle, no orders")
    g.add_argument("--once", action="store_true", help="one real cycle")
    g.add_argument("--loop", action="store_true", help="daily loop forever")
    g.add_argument("--flatten", action="store_true", help="close all positions now")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    settings = load_config(args.config)
    trader, env = build(settings)

    if args.flatten:
        confirm_live(env)
        trader.c.login(); trader.select_account(); trader.flatten_all(dry_run=False)
        print("Flattened:\n  " + "\n  ".join(trader.actions or ["(nothing open)"]))
        return

    if args.dry_run:
        print(trader.run_cycle(dry_run=True))
        return

    if args.once:
        confirm_live(env)
        print(trader.run_cycle(dry_run=False))
        return

    if args.loop:
        confirm_live(env)
        run_at = settings["mode"].get("run_time_utc", "21:10")
        hh, mm = (int(x) for x in run_at.split(":"))
        print(f"Daily loop started — running each day at {run_at} UTC (Ctrl+C to stop).")
        while True:
            now = datetime.now(timezone.utc)
            nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            wait = (nxt - now).total_seconds()
            print(f"[{now:%Y-%m-%d %H:%M UTC}] next run {nxt:%Y-%m-%d %H:%M UTC} (in {wait/3600:.1f}h)")
            time.sleep(wait)
            try:
                trader.run_cycle(dry_run=False)
            except Exception as e:
                print(f"[cycle error] {e}")
                time.sleep(60)


if __name__ == "__main__":
    main()
