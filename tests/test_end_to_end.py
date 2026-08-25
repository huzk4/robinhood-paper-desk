"""End-to-end pipeline test on deterministic synthetic data — no network."""
import json
import os
import sys

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import run_daily
from tests.synthetic import make_synthetic_cache


def _cfg(tmp_path):
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    cfg = make_synthetic_cache(cfg, cache_dir=str(tmp_path / "data"))
    cfg["strategy"]["mode"] = "autonomous"
    return cfg


def test_full_daily_cycle(tmp_path):
    cfg = _cfg(tmp_path)
    state = str(tmp_path / "state")

    packet = run_daily.run(cfg, state, fetch=False, news=False)
    assert packet["nav"] == 100_000  # nothing filled yet on day 1
    assert set(packet["scores"]) == {"BULLCO", "BEARCO", "REBND", "CHOPPY",
                                     "SPY", "QQQ", "TLT"}
    # scorecard + signal packet written
    assert os.path.exists(os.path.join(state, "signal_packet.json"))
    assert os.path.exists(os.path.join(state, "scorecard.md"))
    # bull assets should not score deeply negative, bear not strongly positive
    assert packet["scores"]["BULLCO"]["total"] >= 0
    assert packet["scores"]["BEARCO"]["total"] <= 0

    # second run: any queued orders fill at open, ledger persists
    packet2 = run_daily.run(cfg, state, fetch=False, news=False)
    with open(os.path.join(state, "ledger.json")) as f:
        led = json.load(f)
    assert led["cash"] <= 100_000
    assert packet2["date"] == packet["date"]  # same last bar in synthetic cache


def test_advisory_mode_parks_orders(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["strategy"]["mode"] = "advisory"
    # force an entry signal so a proposed order exists
    cfg["strategy"]["enter_at"] = -6
    run_daily.ENTRY_ACTIONS_BAK = run_daily.ENTRY_ACTIONS
    state = str(tmp_path / "state")
    packet = run_daily.run(cfg, state, fetch=False, news=False)
    # advisory: orders parked, not queued
    assert not os.path.exists(os.path.join(state, "pending_orders.json"))
    assert os.path.exists(os.path.join(state, "proposed_orders.json"))
