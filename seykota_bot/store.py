"""
Lightweight persistent state for the paper/live loop. The BROKER is the source of
truth for open positions, so we only persist what the broker can't tell us:
peak equity (for drawdown tracking), the day's realised P&L, and a trade journal.

JSON files (no DB dependency) — robust and easy to inspect. Atomic writes.
"""
from __future__ import annotations
import os, json, tempfile
from datetime import date


class Store:
    def __init__(self, state_file: str, journal_file: str):
        self.state_file = state_file
        self.journal_file = journal_file
        os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"peak_equity": 0.0, "day": "", "day_start_equity": 0.0,
                "stops": {}, "halted": False, "equity_history": []}

    def _atomic_write(self, path: str, text: str):
        d = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    def save(self):
        self._atomic_write(self.state_file, json.dumps(self.state, indent=2, default=str))

    # ---- equity / drawdown ---- #
    def update_peak(self, equity: float) -> float:
        self.state["peak_equity"] = max(self.state.get("peak_equity", 0.0) or 0.0, equity)
        return self.state["peak_equity"]

    def roll_day(self, equity: float):
        """Reset the day's start equity once per calendar day (for daily loss limit)."""
        today = str(date.today())
        if self.state.get("day") != today:
            self.state["day"] = today
            self.state["day_start_equity"] = equity
            self.save()

    def day_pnl_pct(self, equity: float) -> float:
        s = self.state.get("day_start_equity") or equity
        return (equity - s) / s if s else 0.0

    def record_equity(self, equity: float, drawdown: float):
        """Upsert one equity-curve point per calendar day (for the live dashboard)."""
        today = str(date.today())
        hist = self.state.setdefault("equity_history", [])
        if hist and hist[-1].get("date") == today:
            hist[-1] = {"date": today, "equity": round(equity, 2), "drawdown": round(drawdown, 4)}
        else:
            hist.append({"date": today, "equity": round(equity, 2), "drawdown": round(drawdown, 4)})

    # ---- per-position trailing stop memory (ratchet) ---- #
    def get_stop(self, deal_id: str):
        return self.state.get("stops", {}).get(deal_id)

    def set_stop(self, deal_id: str, level: float):
        self.state.setdefault("stops", {})[deal_id] = level

    def prune_stops(self, live_deal_ids: set):
        self.state["stops"] = {k: v for k, v in self.state.get("stops", {}).items()
                               if k in live_deal_ids}

    # ---- journal ---- #
    def journal(self, record: dict):
        os.makedirs(os.path.dirname(self.journal_file) or ".", exist_ok=True)
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
