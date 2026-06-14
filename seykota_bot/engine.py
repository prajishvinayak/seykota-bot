"""
Seykota-style trend-following backtest engine.
Pure pandas/numpy. Implements the rules in STRATEGY_RULES.md.

Core ideas:
- Trend filter: EMA(fast) vs EMA(slow)
- Entry: Donchian N-bar breakout in the trend direction
- Initial stop: 2N (N = ATR), optionally tightened to swing structure
- Exit: chandelier ATR trailing stop (ride winners), no profit target
- Sizing: risk a fixed % of CURRENT equity, converted to size via stop distance
          (volatility-scaled => size shrinks when ATR rises)
- Portfolio heat cap, drawdown de-risking, per-instrument unit cap
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    # strategy
    # NOTE: ema 100/200 and chandelier 3.5 are BACKTEST-REFINED defaults (see
    # REFINEMENT_LOG.md). Brief defaults were 80/140 x3.0; on real 3y data the
    # slower filter + looser trail improved return AND lowered drawdown, and the
    # brief explicitly sanctions tuning these by backtest. Shorts kept ON for
    # all-weather robustness (long-only scored higher but partly fits a bull market).
    ema_fast: int = 100
    ema_slow: int = 200
    donchian_n: int = 50
    atr_period: int = 20
    chandelier_period: int = 22
    chandelier_mult: float = 3.5
    initial_stop_atr_mult: float = 2.0
    # MULTI-MARKET EVIDENCE (39 markets, see REFINEMENT_LOG.md): shorts lost money in
    # EVERY universe tested (-20% with shorts vs +67% long-only across all 39). Breakout
    # trend-following shorts are structurally weak (sharp V-recoveries whipsaw them) and
    # this 3y regime was broadly rising. Long-only by default; flip to True to re-test.
    allow_short: bool = False
    exit_on_trend_flip: bool = False
    use_structure_stop: bool = True
    swing_lookback: int = 10
    # risk
    starting_equity: float = 100_000.0
    risk_pct: float = 0.01
    risk_pct_max: float = 0.02
    heat_cap: float = 0.15
    max_units_per_instrument: int = 1   # pyramiding off by default in backtest
    drawdown_derisk: bool = True
    max_drawdown_pause: float = 0.25
    trend_strength_scaling: bool = False
    # correlation-aware caps (brief §3.3-3.4: heat matters more than entry timing).
    # 0 = off. group_heat_cap limits open risk within one correlated cluster; the
    # overall heat_cap treats positions as independent and under-counts a basket of
    # 8 indices all long at once -> these contain the cluster risk that drives drawdown.
    # TUNED on real data (risk_tune.py): max 3 / cluster + 6% cluster heat raised
    # return 62%->86% AND cut maxDD 30%->18% AND tripled OOS MAR on Capital.com data;
    # same pattern on Yahoo. Best risk improvement found — adopted as default.
    group_heat_cap: float = 0.06         # max 6% equity at risk within one correlated cluster
    max_positions_per_group: int = 3     # at most 3 concurrent positions / correlated cluster
    # costs (per instrument values can override via Instrument)
    slippage_points: float = 0.0        # added per side; default uses instrument tick
    # misc
    account_ccy: str = "USD"


@dataclass
class Instrument:
    epic: str
    group: str = "misc"
    value_per_point: float = 1.0   # account-ccy P&L per 1.0 price move per 1 unit of size
    min_size: float = 0.1
    size_step: float = 0.1
    spread_points: float = 0.0     # half-spread applied each side
    financing_daily: float = 0.0   # account-ccy per unit per day held (overnight swap)
    corr_group: str = ""           # correlation cluster (defaults to `group`); e.g.
                                   # index+stock share "equity" since they move together


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def atr_wilder(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    # Wilder smoothing = EMA with alpha = 1/period
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def build_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["atr"] = atr_wilder(out, cfg.atr_period)
    out["atr_chan"] = atr_wilder(out, cfg.chandelier_period)
    # Donchian breakout uses PRIOR N bars (exclude current) to avoid trivial self-match
    out["donchian_hi"] = out["high"].rolling(cfg.donchian_n).max().shift(1)
    out["donchian_lo"] = out["low"].rolling(cfg.donchian_n).min().shift(1)
    out["hh_chan"] = out["high"].rolling(cfg.chandelier_period).max()
    out["ll_chan"] = out["low"].rolling(cfg.chandelier_period).min()
    out["swing_lo"] = out["low"].rolling(cfg.swing_lookback).min()
    out["swing_hi"] = out["high"].rolling(cfg.swing_lookback).max()
    out["uptrend"] = out["ema_fast"] > out["ema_slow"]
    return out


# --------------------------------------------------------------------------- #
# Position / Trade records
# --------------------------------------------------------------------------- #
@dataclass
class Position:
    epic: str
    direction: int            # +1 long, -1 short
    size: float
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    value_per_point: float
    risk_at_entry: float      # account ccy
    extreme: float            # highest high (long) / lowest low (short) since entry
    equity_at_entry: float = 0.0   # equity at the moment of fill (for risk-cap invariant)
    heat_at_entry: float = 0.0     # total portfolio heat ratio just after this entry
    group_heat_at_entry: float = 0.0  # cluster heat ratio just after this entry
    bars_held: int = 0


@dataclass
class Trade:
    epic: str
    direction: int
    size: float
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl: float
    pnl_pct_equity: float
    bars_held: int
    exit_reason: str
    mae: float                # max adverse excursion (points)
    mfe: float                # max favourable excursion (points)
    risk_at_entry: float = 0.0     # account-ccy risk to the initial stop
    equity_at_entry: float = 0.0   # equity at fill (risk_at_entry/equity_at_entry <= risk_pct)
    heat_at_entry: float = 0.0     # portfolio heat ratio just after entry (<= heat_cap)
    group_heat_at_entry: float = 0.0  # cluster heat ratio just after entry (<= group_heat_cap)
    red_reason: str = ""      # populated for losers


# --------------------------------------------------------------------------- #
# Backtester (portfolio-level, daily)
# --------------------------------------------------------------------------- #
class Backtest:
    def __init__(self, cfg: Config, instruments: Dict[str, Instrument],
                 data: Dict[str, pd.DataFrame]):
        self.cfg = cfg
        self.instruments = instruments
        self.ind = {e: build_indicators(df, cfg) for e, df in data.items()}
        # global trading calendar
        idx = sorted(set().union(*[set(df.index) for df in data.values()]))
        self.dates: List[pd.Timestamp] = list(idx)
        self.balance = cfg.starting_equity
        self.peak_equity = cfg.starting_equity
        self.positions: Dict[str, Position] = {}
        self.pending: List[dict] = []      # entries queued for next bar open
        self.trades: List[Trade] = []
        self.equity_curve: List[dict] = []

    # -- helpers ----------------------------------------------------------- #
    def _bar(self, epic: str, date) -> Optional[pd.Series]:
        df = self.ind[epic]
        if date in df.index:
            return df.loc[date]
        return None

    def _round_size(self, size: float, inst: Instrument) -> float:
        if size < inst.min_size:
            return 0.0
        steps = np.floor(size / inst.size_step)
        return round(steps * inst.size_step, 8)

    def _unrealized(self, date) -> float:
        u = 0.0
        for p in self.positions.values():
            bar = self._bar(p.epic, date)
            if bar is None:
                continue
            u += (bar["close"] - p.entry_price) * p.direction * p.size * p.value_per_point
        return u

    def _equity(self, date) -> float:
        return self.balance + self._unrealized(date)

    def _sizing_equity(self, equity: float) -> float:
        """Drawdown de-risking: -20% sizing equity per -10% drawdown from peak."""
        if not self.cfg.drawdown_derisk or self.peak_equity <= 0:
            return equity
        dd = max(0.0, (self.peak_equity - equity) / self.peak_equity)
        factor = max(0.2, 1.0 - 2.0 * (dd // 0.10) * 0.10)  # step every 10%
        return equity * factor

    def _open_risk(self, date) -> float:
        """Current portfolio heat in account ccy (risk to stops)."""
        risk = 0.0
        for p in self.positions.values():
            bar = self._bar(p.epic, date)
            px = bar["close"] if bar is not None else p.entry_price
            dist = abs(px - p.stop_price)
            risk += dist * p.size * p.value_per_point
        return risk

    # -- main loop --------------------------------------------------------- #
    def run(self) -> dict:
        cfg = self.cfg
        for date in self.dates:
            equity_open = self._equity(date)

            # 1) fill pending entries at this bar's open
            self._fill_pending(date)

            # 2) manage open positions (trailing stops + exits) on this bar
            self._manage_positions(date)

            # 3) mark equity at close, update peak/drawdown
            equity = self._equity(date)
            self.peak_equity = max(self.peak_equity, equity)
            dd = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0.0

            # 4) generate new signals at close -> queue for next bar
            paused = dd >= cfg.max_drawdown_pause
            if not paused:
                self._generate_signals(date, equity)

            self.equity_curve.append({
                "date": str(pd.Timestamp(date).date()),
                "equity": round(equity, 2),
                "drawdown": round(-dd, 4),
                "open_positions": len(self.positions),
                "heat": round(self._open_risk(date) / equity, 4) if equity > 0 else 0.0,
            })

        # close any still-open positions at last bar close (mark-out)
        self._final_liquidation()
        return self._results()

    def _fill_pending(self, date):
        still = []
        for sig in self.pending:
            epic = sig["epic"]
            bar = self._bar(epic, date)
            inst = self.instruments[epic]
            if bar is None:
                # market closed that day; carry the signal forward up to 2 days, then drop
                if sig.get("age", 0) < 2:
                    sig["age"] = sig.get("age", 0) + 1
                    still.append(sig)
                continue
            direction = sig["direction"]
            cost = inst.spread_points + self.cfg.slippage_points  # half-spread + slippage, per side
            fill = bar["open"] + direction * cost
            stop = sig["stop_price"]
            size = sig["size"]
            # entry cost: half-spread already in fill; financing accrues daily later
            self.positions[epic] = Position(
                epic=epic, direction=direction, size=size,
                entry_date=date, entry_price=fill, stop_price=stop,
                value_per_point=inst.value_per_point,
                risk_at_entry=sig["risk_amount"],
                extreme=fill,
                equity_at_entry=self._equity(date),  # equity before this fill counts
                heat_at_entry=sig.get("heat_at_entry", 0.0),
                group_heat_at_entry=sig.get("group_heat_at_entry", 0.0),
            )
        self.pending = still

    def _manage_positions(self, date):
        cfg = self.cfg
        to_close = []
        for epic, p in self.positions.items():
            bar = self._bar(epic, date)
            if bar is None:
                continue
            inst = self.instruments[epic]
            p.bars_held += 1
            # financing cost (held overnight)
            self.balance -= inst.financing_daily * p.size

            # update extreme + trailing chandelier stop
            if p.direction > 0:
                p.extreme = max(p.extreme, bar["high"])
                chan = bar["hh_chan"] - cfg.chandelier_mult * bar["atr_chan"]
                if not np.isnan(chan):
                    p.stop_price = max(p.stop_price, chan)   # ratchet up only
            else:
                p.extreme = min(p.extreme, bar["low"])
                chan = bar["ll_chan"] + cfg.chandelier_mult * bar["atr_chan"]
                if not np.isnan(chan):
                    p.stop_price = min(p.stop_price, chan)   # ratchet down only

            exit_reason = None
            exit_price = None
            # stop hit intraday?
            if p.direction > 0 and bar["low"] <= p.stop_price:
                exit_reason = "STOP"
                exit_price = min(p.stop_price, bar["open"])  # gap-through => open
            elif p.direction < 0 and bar["high"] >= p.stop_price:
                exit_reason = "STOP"
                exit_price = max(p.stop_price, bar["open"])
            # trend flip exit (optional)
            elif cfg.exit_on_trend_flip and (
                (p.direction > 0 and not bool(bar["uptrend"])) or
                (p.direction < 0 and bool(bar["uptrend"]))
            ):
                exit_reason = "TREND_FLIP"
                exit_price = bar["close"]

            if exit_reason:
                cost = inst.spread_points + cfg.slippage_points  # half-spread + slippage, per side
                exit_price -= p.direction * cost
                to_close.append((epic, exit_price, exit_reason, bar))

        for epic, px, reason, bar in to_close:
            self._close(epic, bar.name, px, reason)

    def _close(self, epic, date, exit_price, reason):
        p = self.positions.pop(epic)
        pnl = (exit_price - p.entry_price) * p.direction * p.size * p.value_per_point
        self.balance += pnl
        equity_now = self._equity(date)
        # MFE in account ccy (favourable excursion); MAE proxied by stop distance below
        mfe = (p.extreme - p.entry_price) * p.direction * p.value_per_point
        adverse_pts = abs(p.entry_price - p.stop_price)
        t = Trade(
            epic=epic, direction=p.direction, size=p.size,
            entry_date=str(pd.Timestamp(p.entry_date).date()), entry_price=round(p.entry_price, 5),
            exit_date=str(pd.Timestamp(date).date()), exit_price=round(exit_price, 5),
            pnl=round(pnl, 2),
            pnl_pct_equity=round(pnl / equity_now, 4) if equity_now else 0.0,
            bars_held=p.bars_held, exit_reason=reason,
            mae=round(adverse_pts, 5), mfe=round(mfe, 2),
            risk_at_entry=round(p.risk_at_entry, 2),
            equity_at_entry=round(p.equity_at_entry, 2),
            heat_at_entry=round(p.heat_at_entry, 4),
            group_heat_at_entry=round(p.group_heat_at_entry, 4),
        )
        if t.pnl < 0:
            t.red_reason = self._classify_loss(t, p)
        self.trades.append(t)

    def _classify_loss(self, t: Trade, p: Position) -> str:
        """Reason a trade ended in the RED (see Rule 12)."""
        ind = self.ind[t.epic]
        # was it a quick reversal right after entry?
        if t.bars_held <= 5:
            return "WHIPSAW"
        # did the trend filter flip against us during the trade?
        try:
            seg = ind.loc[pd.Timestamp(t.entry_date):pd.Timestamp(t.exit_date)]
            flipped = ((seg["uptrend"].iloc[0]) != (seg["uptrend"].iloc[-1]))
        except Exception:
            flipped = False
        if flipped:
            return "TREND_REVERSAL"
        if t.exit_reason == "STOP":
            return "VOLATILITY_STOP"
        return "RULE_OK_LOSS"

    def _cg(self, epic) -> str:
        inst = self.instruments[epic]
        return inst.corr_group or inst.group

    def _generate_signals(self, date, equity):
        cfg = self.cfg
        sizing_equity = self._sizing_equity(equity)
        current_heat = self._open_risk(date)
        # per-correlated-cluster open risk ($) and position counts (incl. open positions)
        group_heat: Dict[str, float] = {}
        group_count: Dict[str, int] = {}
        for p in self.positions.values():
            bar = self._bar(p.epic, date)
            px = bar["close"] if bar is not None else p.entry_price
            cg = self._cg(p.epic)
            group_heat[cg] = group_heat.get(cg, 0.0) + abs(px - p.stop_price) * p.size * p.value_per_point
            group_count[cg] = group_count.get(cg, 0) + 1
        for sig in self.pending:  # already-queued entries count toward cluster limits too
            cg = self._cg(sig["epic"])
            group_heat[cg] = group_heat.get(cg, 0.0) + sig["risk_amount"]
            group_count[cg] = group_count.get(cg, 0) + 1
        for epic, inst in self.instruments.items():
            if epic in self.positions:
                continue
            if any(s["epic"] == epic for s in self.pending):
                continue
            bar = self._bar(epic, date)
            if bar is None or np.isnan(bar["donchian_hi"]) or np.isnan(bar["atr"]) \
               or np.isnan(bar["ema_slow"]) or bar["atr"] <= 0:
                continue

            direction = 0
            if bool(bar["uptrend"]) and bar["close"] >= bar["donchian_hi"]:
                direction = +1
            elif cfg.allow_short and (not bool(bar["uptrend"])) and bar["close"] <= bar["donchian_lo"]:
                direction = -1
            if direction == 0:
                continue

            n = bar["atr"]
            entry_ref = bar["close"]
            stop = entry_ref - direction * cfg.initial_stop_atr_mult * n
            # structure stop (tighten only)
            if cfg.use_structure_stop:
                if direction > 0:
                    stop = max(stop, bar["swing_lo"] - 0.1 * n)
                else:
                    stop = min(stop, bar["swing_hi"] + 0.1 * n)
            stop_dist = abs(entry_ref - stop)
            if stop_dist <= 0:
                continue

            risk_pct = cfg.risk_pct
            if cfg.trend_strength_scaling:
                spread_n = abs(bar["ema_fast"] - bar["ema_slow"]) / n
                scale = min(1.0, max(0.0, spread_n / 3.0))
                risk_pct = cfg.risk_pct + (cfg.risk_pct_max - cfg.risk_pct) * scale

            risk_amount = sizing_equity * risk_pct
            size = risk_amount / (stop_dist * inst.value_per_point)
            size = self._round_size(size, inst)
            if size <= 0:
                continue

            new_risk = stop_dist * size * inst.value_per_point
            if equity > 0 and (current_heat + new_risk) / equity > cfg.heat_cap:
                continue  # portfolio heat cap

            cg = self._cg(epic)
            # correlated-cluster caps (contain the basket risk that drives drawdown)
            if cfg.max_positions_per_group and group_count.get(cg, 0) >= cfg.max_positions_per_group:
                continue
            if cfg.group_heat_cap and equity > 0 and \
               (group_heat.get(cg, 0.0) + new_risk) / equity > cfg.group_heat_cap:
                continue

            current_heat += new_risk
            group_heat[cg] = group_heat.get(cg, 0.0) + new_risk
            group_count[cg] = group_count.get(cg, 0) + 1
            self.pending.append({
                "epic": epic, "direction": direction, "size": size,
                "stop_price": stop, "risk_amount": new_risk, "age": 0,
                "heat_at_entry": current_heat / equity if equity > 0 else 0.0,
                "group_heat_at_entry": group_heat[cg] / equity if equity > 0 else 0.0,
            })

    def _final_liquidation(self):
        if not self.positions:
            return
        last = self.dates[-1]
        for epic in list(self.positions.keys()):
            bar = self._bar(epic, last)
            if bar is None:
                # find last available
                df = self.ind[epic]
                bar = df.iloc[-1]
            self._close(epic, bar.name, bar["close"], "END")

    # -- results ----------------------------------------------------------- #
    def _results(self) -> dict:
        eq = pd.DataFrame(self.equity_curve)
        metrics = compute_metrics(eq, self.trades, self.cfg.starting_equity)
        return {
            "config": asdict(self.cfg),
            "equity_curve": self.equity_curve,
            "trades": [asdict(t) for t in self.trades],
            "metrics": metrics,
        }


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def compute_metrics(eq: pd.DataFrame, trades: List[Trade], start_equity: float) -> dict:
    if eq.empty:
        return {}
    eq = eq.copy()
    eq["equity"] = eq["equity"].astype(float)
    rets = eq["equity"].pct_change().fillna(0.0)
    days = len(eq)
    years = max(days / 252.0, 1e-9)
    end_equity = eq["equity"].iloc[-1]
    cagr = (end_equity / start_equity) ** (1 / years) - 1 if start_equity > 0 else 0.0
    max_dd = eq["drawdown"].min()  # negative
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = (rets.mean() / downside.std() * np.sqrt(252)) if len(downside) and downside.std() > 0 else 0.0
    mar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0

    pnl = np.array([t.pnl for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    n = len(pnl)
    win_rate = len(wins) / n if n else 0.0
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0
    expectancy = pnl.mean() if n else 0.0

    return {
        "start_equity": round(start_equity, 2),
        "end_equity": round(float(end_equity), 2),
        "total_return_pct": round((end_equity / start_equity - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(float(sharpe), 2),
        "sortino": round(float(sortino), 2),
        "mar_calmar": round(float(mar), 2),
        "num_trades": int(n),
        "win_rate_pct": round(win_rate * 100, 1),
        "profit_factor": round(float(profit_factor), 2) if profit_factor != float("inf") else None,
        "payoff_ratio": round(float(payoff), 2),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
        "expectancy_per_trade": round(float(expectancy), 2),
        "trading_days": int(days),
    }
