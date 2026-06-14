"""
Live-loop safety — criteria 4 (every position carries a server-side stop) and
7 (kill switch flattens + halts), plus market-status respect.
"""
from __future__ import annotations
import os
import pytest
from seykota_bot.engine import Config
from seykota_bot.capital_client import CapitalClient, CapitalError
from seykota_bot.store import Store
from seykota_bot.live import LiveTrader
from .conftest import make_df, df_to_rows, FakeClient


def _settings(tmp_path, kill_file="KILL_SWITCH_NONE"):
    return {
        "mode": {"environment": "demo"},
        "risk": {"daily_loss_limit_pct": 0.06},
        "execution": {"kill_switch_file": str(tmp_path / kill_file),
                      "max_size_per_trade": 50, "respect_market_status": True},
        "paths": {"output_dir": str(tmp_path / "out"),
                  "data_cache": str(tmp_path / "cache"),
                  "state_file": str(tmp_path / "state.json"),
                  "journal_file": str(tmp_path / "journal.jsonl")},
    }


def _universe():
    return [{"epic": "EQ0", "group": "index", "corr": "equity"}]


def _cfg():
    return Config(ema_fast=5, ema_slow=15, donchian_n=10, atr_period=5,
                  chandelier_period=5, risk_pct=0.01, allow_short=False,
                  max_positions_per_group=3, group_heat_cap=0.06)


def test_open_position_refuses_without_stop():
    """Criterion 4 (unit): the client refuses to open with no protective stop."""
    client = CapitalClient("key", "id", "pw", environment="demo")
    with pytest.raises(CapitalError):
        client.open_position("EQ0", "BUY", 1.0)   # no stop -> must raise (before any HTTP)


def test_live_entry_always_carries_a_stop(tmp_path):
    """Criterion 4 (integration): every order LiveTrader sends includes stop_level."""
    rows = {"EQ0": df_to_rows(make_df(n=400, trend=0.8, vol=0.4, seed=2))}
    client = FakeClient(rows, status="TRADEABLE")
    store = Store(str(tmp_path / "s.json"), str(tmp_path / "j.jsonl"))
    trader = LiveTrader(client, _cfg(), _settings(tmp_path), _universe(), store, log=lambda *a: None)
    trader.run_cycle(dry_run=False)
    assert client.opened, "expected at least one breakout entry in a strong uptrend"
    for o in client.opened:
        assert o["stop_level"] is not None


def test_market_status_gate_blocks_when_closed(tmp_path):
    """respect_market_status: no orders when the market is not TRADEABLE."""
    rows = {"EQ0": df_to_rows(make_df(n=400, trend=0.8, vol=0.4, seed=2))}
    client = FakeClient(rows, status="CLOSED")
    store = Store(str(tmp_path / "s.json"), str(tmp_path / "j.jsonl"))
    trader = LiveTrader(client, _cfg(), _settings(tmp_path), _universe(), store, log=lambda *a: None)
    trader.run_cycle(dry_run=False)
    assert client.opened == []


def test_kill_switch_flattens_and_halts(tmp_path):
    """Criterion 7: kill switch closes all open positions and halts."""
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    settings = _settings(tmp_path)
    settings["execution"]["kill_switch_file"] = str(kill)
    pos = [{"position": {"dealId": "d1", "direction": "BUY", "size": 1, "level": 100,
                         "stopLevel": 95, "upl": 0},
            "market": {"epic": "EQ0", "marketStatus": "TRADEABLE", "bid": 101, "offer": 101}}]
    client = FakeClient({"EQ0": df_to_rows(make_df(seed=2))}, positions=pos)
    store = Store(str(tmp_path / "s.json"), str(tmp_path / "j.jsonl"))
    trader = LiveTrader(client, _cfg(), settings, _universe(), store, log=lambda *a: None)
    trader.run_cycle(dry_run=False)
    assert "d1" in client.closed
    assert store.state.get("halted") is True
    assert client.opened == []
