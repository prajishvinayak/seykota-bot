"""
Paper/live orchestrator for Capital.com (demo by default).

One daily cycle (cadence = daily bars):
  1. login / keepalive; honour the kill switch (flatten + halt)
  2. read account equity (broker = source of truth)
  3. refresh CLOSED daily candles for the universe (+ spike-clean)
  4. update peak/drawdown; roll the trading day
  5. ratchet trailing (chandelier) stops on open positions -> amend server-side
  6. open new breakouts that pass EVERY risk gate (heat, cluster, DD pause,
     daily-loss limit, margin, min size) — ALWAYS with a server-side stop
  7. write the EOD report + refresh the dashboard

Identical indicator/stop/sizing maths to the backtest (engine.build_indicators).
Nothing here moves REAL money unless mode.environment is explicitly 'live'.
"""
from __future__ import annotations
import os
from datetime import date, datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd

from .engine import Config, build_indicators
from .data import clean_spikes
from .capital_client import CapitalClient, CapitalError
from .store import Store
from .live_dashboard import build_live_dashboard


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


class LiveTrader:
    def __init__(self, client: CapitalClient, cfg: Config, settings: dict,
                 universe: list[dict], store: Store, log=print):
        self.c = client
        self.cfg = cfg
        self.s = settings
        self.universe = universe                       # [{epic, group, corr}]
        self.corr = {u["epic"]: u.get("corr") or u.get("group") for u in universe}
        self.store = store
        self.log = log
        self.data_cache = settings["paths"].get("data_cache", "cache_capital")
        self.actions: list[str] = []                   # human-readable cycle log

    # ------------------------------------------------------------------ #
    def kill_switch_active(self) -> bool:
        return os.path.exists(self.s["execution"].get("kill_switch_file", "KILL_SWITCH"))

    # ------------------------------------------------------------------ #
    def select_account(self):
        """Switch the session to the configured target account (e.g. 'Ed Seykota' £21k)
        so two bots on one login can NEVER touch each other's positions. Returns the dict."""
        target = self.s["mode"].get("account_name")
        accts = self.c.accounts()
        if not accts:
            raise CapitalError("No accounts returned for this login.")
        if not target:
            return next((a for a in accts if a.get("preferred")), accts[0])
        match = next((a for a in accts if a.get("accountName") == target), None)
        if not match:
            names = [a.get("accountName") for a in accts]
            raise CapitalError(f"Target account '{target}' not found. Available: {names}")
        self.c.switch_account(match["accountId"])   # idempotent — always assert the right account
        self.log(f"Account: {match.get('accountName')} [{match.get('accountId')}] "
                 f"{match.get('currency')}")
        return match

    def account_equity(self, acct: dict | None = None):
        """(cash_balance, available, equity, currency) for the selected account."""
        if acct is None:
            accts = self.c.accounts()
            target = self.s["mode"].get("account_name")
            acct = (next((a for a in accts if a.get("accountName") == target), None)
                    or next((a for a in accts if a.get("preferred")), accts[0] if accts else {}))
        bal = acct.get("balance", {}) or {}
        cash = float(bal.get("balance", 0.0) or 0.0)
        available = float(bal.get("available", cash) or cash)
        upl = float(bal.get("profitLoss", 0.0) or 0.0)   # open P&L if provided
        equity = cash + upl
        return cash, available, equity, acct.get("currency", "")

    def broker_positions(self) -> list[dict]:
        """Normalise GET /positions into flat dicts keyed by what we need."""
        out = []
        for item in self.c.positions():
            p = item.get("position", item) or {}
            mk = item.get("market", {}) or {}
            direction = 1 if str(p.get("direction", "")).upper() == "BUY" else -1
            out.append({
                "dealId": p.get("dealId"),
                "epic": mk.get("epic") or p.get("epic"),
                "direction": direction,
                "size": float(p.get("size", 0) or 0),
                "level": float(p.get("level", 0) or 0),          # entry
                "stopLevel": p.get("stopLevel"),
                "upl": float(p.get("upl", 0) or 0),
                "marketStatus": mk.get("marketStatus"),
                "bid": mk.get("bid"), "offer": mk.get("offer"),
            })
        return out

    # ------------------------------------------------------------------ #
    def refresh_candles(self, epic: str) -> pd.DataFrame | None:
        """Pull daily candles, cache, spike-clean, and drop today's partial bar."""
        try:
            rows = self.c.price_history(epic, resolution="DAY")
        except CapitalError as e:
            self.log(f"  ! {epic}: price_history failed ({str(e)[:80]})")
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["snapshotTime"])
        df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna()
        df, _ = clean_spikes(df)
        # use only CLOSED bars (exclude today's still-forming candle) — no look-ahead
        df = df[df.index.date < _today_utc()]
        if len(df) < max(self.cfg.ema_slow, self.cfg.donchian_n) + 5:
            return None
        os.makedirs(self.data_cache, exist_ok=True)
        df.to_pickle(os.path.join(self.data_cache, f"{epic}.pkl"))
        return df

    def market_rules(self, epic: str):
        """(min_size, step, status) from GET /markets/{epic}."""
        try:
            m = self.c.market(epic)
        except CapitalError:
            return 1.0, 1.0, None
        dr = m.get("dealingRules", {}) or {}
        snap = m.get("snapshot", {}) or {}
        min_size = float((dr.get("minDealSize", {}) or {}).get("value", 1.0) or 1.0)
        step = float((dr.get("minSizeIncrement", {}) or dr.get("minDealSize", {}) or {}).get("value", min_size) or min_size)
        return min_size, step, snap.get("marketStatus")

    # ------------------------------------------------------------------ #
    def run_cycle(self, dry_run: bool = False) -> str:
        self.actions = []
        tag = "DRY-RUN" if dry_run else self.s["mode"]["environment"].upper()
        self.log(f"\n=== Seykota cycle [{tag}] {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ===")
        self.c.login()
        acct = self.select_account()   # CRITICAL: pin this bot to its own account

        # 1) kill switch
        if self.kill_switch_active():
            self.log("KILL SWITCH present -> flattening all and halting.")
            self.flatten_all(dry_run)
            self.store.state["halted"] = True
            self.store.save()
            return self._write_eod_report(*self._snapshot(), halted=True)

        # 2) account + positions
        cash, available, equity, ccy = self.account_equity(acct)
        positions = self.broker_positions()
        self.store.roll_day(equity)
        peak = self.store.update_peak(equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        day_pnl = self.store.day_pnl_pct(equity)
        self.log(f"Equity {equity:,.0f} {ccy} | available {available:,.0f} | "
                 f"peak {peak:,.0f} | drawdown {dd*100:.1f}% | day P&L {day_pnl*100:.2f}% | "
                 f"open {len(positions)}")

        # 3) candles + indicators (only universe instruments)
        ind = {}
        for u in self.universe:
            df = self.refresh_candles(u["epic"])
            if df is not None:
                ind[u["epic"]] = build_indicators(df, self.cfg)

        # 4) ratchet trailing stops on open positions
        self.manage_trailing(positions, ind, dry_run)

        # 5) entry gates
        paused = dd >= self.cfg.max_drawdown_pause
        day_blocked = day_pnl <= -abs(self.s["risk"].get("daily_loss_limit_pct", 0.06))
        if paused:
            self.actions.append(f"NEW ENTRIES PAUSED: drawdown {dd*100:.1f}% ≥ "
                                f"{self.cfg.max_drawdown_pause*100:.0f}%")
        if day_blocked:
            self.actions.append(f"NEW ENTRIES BLOCKED: day P&L {day_pnl*100:.2f}% ≤ "
                                f"-{self.s['risk']['daily_loss_limit_pct']*100:.0f}%")
        if not (paused or day_blocked):
            self.open_new(positions, ind, equity, available, dry_run)

        # prune stale trailing-stop memory
        self.store.prune_stops({p["dealId"] for p in positions if p.get("dealId")})
        self.store.save()
        return self._write_eod_report(cash, available, equity, ccy, peak, dd, day_pnl,
                                      self.broker_positions() if not dry_run else positions, ind)

    # ------------------------------------------------------------------ #
    def manage_trailing(self, positions, ind, dry_run):
        cfg = self.cfg
        for p in positions:
            epic = p["epic"]
            if epic not in ind:
                continue
            bar = ind[epic].iloc[-1]
            if np.isnan(bar.get("atr_chan", np.nan)):
                continue
            if p["direction"] > 0:
                chan = bar["hh_chan"] - cfg.chandelier_mult * bar["atr_chan"]
            else:
                chan = bar["ll_chan"] + cfg.chandelier_mult * bar["atr_chan"]
            if np.isnan(chan):
                continue
            cur = p["stopLevel"]
            cur = float(cur) if cur not in (None, "") else None
            remembered = self.store.get_stop(p["dealId"])
            ref = max([x for x in [cur, remembered] if x is not None], default=None) if p["direction"] > 0 \
                else min([x for x in [cur, remembered] if x is not None], default=None)
            tighter = (ref is None) or (p["direction"] > 0 and chan > ref + 1e-9) \
                or (p["direction"] < 0 and chan < ref - 1e-9)
            if not tighter:
                continue
            new_stop = round(float(chan), 4)
            self.actions.append(f"TRAIL {epic}: stop -> {new_stop} "
                                f"({'higher' if p['direction']>0 else 'lower'})")
            if not dry_run:
                try:
                    self.c.amend_position(p["dealId"], stop_level=new_stop)
                    self.store.set_stop(p["dealId"], new_stop)
                except CapitalError as e:
                    self.actions.append(f"  ! amend {epic} failed: {str(e)[:80]}")
            else:
                self.store.set_stop(p["dealId"], new_stop)

    # ------------------------------------------------------------------ #
    def _open_risk_and_clusters(self, positions, ind):
        """Current portfolio heat ($) and per-cluster (risk$, count)."""
        heat = 0.0
        g_heat = defaultdict(float)
        g_count = defaultdict(int)
        for p in positions:
            epic = p["epic"]
            stop = p["stopLevel"]
            px = p.get("bid") or p.get("offer")
            if epic in ind:
                px = float(ind[epic].iloc[-1]["close"])
            if stop in (None, "") or px is None:
                continue
            risk = abs(float(px) - float(stop)) * p["size"]   # value/point = 1 unit
            cg = self.corr.get(epic, p.get("epic"))
            heat += risk
            g_heat[cg] += risk
            g_count[cg] += 1
        return heat, g_heat, g_count

    def open_new(self, positions, ind, equity, available, dry_run):
        cfg = self.cfg
        held = {p["epic"] for p in positions}
        sizing_equity = self._sizing_equity(equity)
        heat, g_heat, g_count = self._open_risk_and_clusters(positions, ind)
        max_size = float(self.s["execution"].get("max_size_per_trade", 1e9))

        for u in self.universe:
            epic = u["epic"]
            if epic in held or epic not in ind:
                continue
            bar = ind[epic].iloc[-1]
            if any(np.isnan(bar.get(k, np.nan)) for k in ("donchian_hi", "atr", "ema_slow")) or bar["atr"] <= 0:
                continue

            direction = 0
            if bool(bar["uptrend"]) and bar["close"] >= bar["donchian_hi"]:
                direction = 1
            elif cfg.allow_short and (not bool(bar["uptrend"])) and bar["close"] <= bar["donchian_lo"]:
                direction = -1
            if direction == 0:
                continue

            min_size, step, status = self.market_rules(epic)
            if self.s["execution"].get("respect_market_status", True) and status and status != "TRADEABLE":
                continue

            n = bar["atr"]
            entry_ref = float(bar["close"])
            stop = entry_ref - direction * cfg.initial_stop_atr_mult * n
            if cfg.use_structure_stop:
                if direction > 0:
                    stop = max(stop, bar["swing_lo"] - 0.1 * n)
                else:
                    stop = min(stop, bar["swing_hi"] + 0.1 * n)
            stop_dist = abs(entry_ref - stop)
            if stop_dist <= 0:
                continue

            risk_amount = sizing_equity * cfg.risk_pct
            size = risk_amount / stop_dist                    # value/point = 1 unit
            size = np.floor(size / step) * step
            size = min(size, max_size)
            if size < min_size or size <= 0:
                continue
            new_risk = stop_dist * size

            # ---- risk gates ----
            if equity > 0 and (heat + new_risk) / equity > cfg.heat_cap:
                continue
            cg = self.corr.get(epic, u.get("group"))
            if cfg.max_positions_per_group and g_count[cg] >= cfg.max_positions_per_group:
                continue
            if cfg.group_heat_cap and equity > 0 and (g_heat[cg] + new_risk) / equity > cfg.group_heat_cap:
                continue
            # light margin guard
            if available is not None and entry_ref * size * 0.05 > available:
                continue

            stop_level = round(float(stop), 4)
            side = "BUY" if direction > 0 else "SELL"
            self.actions.append(f"OPEN {side} {epic} size {size:g} @~{entry_ref:.4f} "
                                f"stop {stop_level} (risk ~{new_risk:,.0f}, cluster {cg})")
            if not dry_run:
                try:
                    deal_id = self.c.open_position(epic, side, size, stop_level=stop_level)
                    self.store.set_stop(deal_id, stop_level)
                    self.store.journal({"ts": str(datetime.now(timezone.utc)), "event": "OPEN",
                                        "epic": epic, "side": side, "size": size,
                                        "entry_ref": entry_ref, "stop": stop_level,
                                        "risk": new_risk, "dealId": deal_id})
                except CapitalError as e:
                    self.actions.append(f"  ! open {epic} failed: {str(e)[:90]}")
                    continue

            heat += new_risk
            g_heat[cg] += new_risk
            g_count[cg] += 1

    # ------------------------------------------------------------------ #
    def flatten_all(self, dry_run):
        for p in self.broker_positions():
            self.actions.append(f"FLATTEN {p['epic']} ({p['dealId']})")
            if not dry_run and p.get("dealId"):
                try:
                    self.c.close_position(p["dealId"])
                    self.store.journal({"ts": str(datetime.now(timezone.utc)),
                                        "event": "FLATTEN", "epic": p["epic"], "dealId": p["dealId"]})
                except CapitalError as e:
                    self.actions.append(f"  ! close failed: {str(e)[:80]}")

    def _sizing_equity(self, equity):
        if not self.cfg.drawdown_derisk:
            return equity
        peak = self.store.state.get("peak_equity", equity) or equity
        dd = max(0.0, (peak - equity) / peak) if peak > 0 else 0.0
        return equity * max(0.2, 1.0 - 2.0 * (dd // 0.10) * 0.10)

    def _snapshot(self):
        cash, available, equity, ccy = self.account_equity()
        peak = self.store.state.get("peak_equity", equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        return cash, available, equity, ccy, peak, dd, self.store.day_pnl_pct(equity), \
            self.broker_positions(), {}

    # ------------------------------------------------------------------ #
    def _write_eod_report(self, cash, available, equity, ccy, peak, dd, day_pnl,
                          positions, ind, halted=False) -> str:
        heat, g_heat, g_count = self._open_risk_and_clusters(positions, ind)
        L = [f"# Seykota Bot — Daily Report ({date.today()})  [{self.s['mode']['environment'].upper()}]",
             "_Paste this whole report back to Claude for review & tuning._\n",
             "## Account",
             f"- Equity: **{equity:,.2f} {ccy}**  (cash {cash:,.2f}, available {available:,.2f})",
             f"- Peak: {peak:,.2f} | Drawdown: **{dd*100:.2f}%** | Day P&L: **{day_pnl*100:.2f}%**",
             f"- Portfolio heat (open risk/equity): **{(heat/equity*100) if equity else 0:.2f}%**",
             f"- Open positions: **{len(positions)}**" + ("  |  **HALTED (kill switch)**" if halted else ""),
             "\n## This cycle's actions"]
        L += [f"- {a}" for a in (self.actions or ["- (no actions)"])] if self.actions else ["- (no actions)"]
        L.append("\n## Open positions")
        if positions:
            L.append("| Epic | Side | Size | Entry | Stop | uP&L |")
            L.append("|---|---|---|---|---|---|")
            for p in positions:
                L.append(f"| {p['epic']} | {'LONG' if p['direction']>0 else 'SHORT'} | {p['size']:g} | "
                         f"{p['level']} | {p.get('stopLevel')} | {p.get('upl',0):,.2f} |")
        else:
            L.append("_Flat — no open positions._")
        L.append("\n## Cluster exposure")
        for cg in sorted(set(self.corr.values())):
            L.append(f"- {cg}: {g_count.get(cg,0)} / {self.cfg.max_positions_per_group} positions, "
                     f"heat {(g_heat.get(cg,0)/equity*100) if equity else 0:.2f}%")
        L.append("\n## For Claude")
        L.append("Review each new/closed trade: was the entry a clean trend breakout? Are losers "
                 "WHIPSAW (expected) or something to tune? Levers: `chandelier_mult`, `donchian_n`, "
                 "`risk_pct`/`heat_cap`, `max_positions_per_group`, `ema_fast/ema_slow` in config.yaml.")
        report = "\n".join(L)
        out_dir = self.s["paths"].get("output_dir", "output")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "eod_report_live.md"), "w", encoding="utf-8") as f:
            f.write(report)

        # record the daily equity point and refresh the live HTML dashboard
        self.store.record_equity(equity, dd)
        self.store.save()
        clusters = [{"name": cg, "count": g_count.get(cg, 0),
                     "cap": self.cfg.max_positions_per_group,
                     "heat": (g_heat.get(cg, 0) / equity * 100) if equity else 0.0}
                    for cg in sorted(set(self.corr.values()))]
        try:
            build_live_dashboard(
                os.path.join(out_dir, "dashboard_live.html"),
                env=self.s["mode"]["environment"], ccy=ccy, equity=equity, cash=cash,
                available=available, peak=peak, dd=dd, day_pnl=day_pnl, heat=heat,
                positions=positions, clusters=clusters, actions=self.actions,
                equity_history=self.store.state.get("equity_history", []),
                journal_file=self.store.journal_file, halted=halted)
        except Exception as e:
            self.log(f"  ! dashboard build failed: {str(e)[:100]}")
        return report
