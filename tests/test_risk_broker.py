import json

import pytest

from sim.broker import PaperBroker
from sim.ledger import Ledger
from sim import risk

CFG = {
    "universe": {"stocks": ["AAPL", "MSFT"], "etfs": ["SPY"]},
    "sectors": {"AAPL": "tech", "MSFT": "tech"},
    "fill": {"slippage_bps": 5, "commission": 0.0},
    "risk": {"max_position_pct": 0.08, "max_sector_pct": 0.10,
             "min_cash_pct": 0.20, "max_trades_per_day": 5,
             "min_holding_days": 2, "max_drawdown_halt": 0.15},
}
PRICES = {"AAPL": 100.0, "MSFT": 100.0, "SPY": 100.0}


def test_position_cap_boundary():
    led = Ledger(100_000)
    ok, why = risk.check_order({"side": "buy", "ticker": "AAPL",
                                "shares": 81, "est_price": 100.0},
                               led, PRICES, CFG, 0, "2026-01-05")
    assert not ok and "position cap" in why
    ok, _ = risk.check_order({"side": "buy", "ticker": "AAPL",
                              "shares": 79, "est_price": 100.0},
                             led, PRICES, CFG, 0, "2026-01-05")
    assert ok


def test_sector_cap():
    led = Ledger(100_000)
    led.apply_buy("AAPL", 70, 100.0, 0.0, "2026-01-02")
    ok, why = risk.check_order({"side": "buy", "ticker": "MSFT",
                                "shares": 45, "est_price": 100.0},
                               led, PRICES, CFG, 0, "2026-01-05")
    assert not ok and "sector cap" in why  # 7k + 4.5k > 10% of 100k


def test_cash_floor():
    led = Ledger(10_000)
    ok, why = risk.check_order({"side": "buy", "ticker": "SPY",
                                "shares": 85, "est_price": 100.0},
                               led, PRICES, {**CFG, "risk": {**CFG["risk"],
                                             "max_position_pct": 0.99,
                                             "max_sector_pct": 0.99}},
                               0, "2026-01-05")
    assert not ok and "cash floor" in why


def test_min_holding_period():
    led = Ledger(100_000)
    led.apply_buy("AAPL", 10, 100.0, 0.0, "2026-01-05")  # Monday
    ok, why = risk.check_order({"side": "sell", "ticker": "AAPL",
                                "shares": 10, "est_price": 100.0},
                               led, PRICES, CFG, 0, "2026-01-06")  # next day
    assert not ok and "min holding" in why
    ok, _ = risk.check_order({"side": "sell", "ticker": "AAPL",
                              "shares": 10, "est_price": 100.0},
                             led, PRICES, CFG, 0, "2026-01-08")  # 3 sessions
    assert ok


def test_trades_per_day_limit():
    led = Ledger(100_000)
    ok, why = risk.check_order({"side": "buy", "ticker": "SPY",
                                "shares": 1, "est_price": 100.0},
                               led, PRICES, CFG, 5, "2026-01-05")
    assert not ok and "max_trades_per_day" in why


def test_kill_switch_halts_and_cancels(tmp_path):
    led = Ledger(100_000)
    led.apply_buy("SPY", 700, 100.0, 0.0, "2026-01-05")
    led.mark(PRICES, "2026-01-05")            # peak 100k
    broker = PaperBroker(led, CFG, str(tmp_path))
    broker.submit([{"side": "buy", "ticker": "AAPL", "shares": 1, "reason": "t"}])
    crash = {"AAPL": 100.0, "MSFT": 100.0, "SPY": 70.0}   # nav 79k, dd 21%
    res = broker.process_fills(crash, crash, "2026-01-06")
    assert led.halted
    assert res[0]["status"] == "HALTED"
    assert broker.load_pending() == []
    # subsequent orders rejected while halted
    ok, why = risk.check_order({"side": "buy", "ticker": "SPY",
                                "shares": 1, "est_price": 70.0},
                               led, crash, CFG, 0, "2026-01-07")
    assert not ok and "HALTED" in why


def test_fill_applies_slippage_and_audits(tmp_path):
    led = Ledger(100_000)
    broker = PaperBroker(led, CFG, str(tmp_path))
    broker.submit([{"side": "buy", "ticker": "SPY", "shares": 10, "reason": "test"}])
    res = broker.process_fills({"SPY": 100.0}, PRICES, "2026-01-06")
    assert res[0]["status"] == "FILLED"
    assert res[0]["fill_price"] == pytest.approx(100.05)   # +5 bps against you
    assert (tmp_path / "trades.csv").exists()
    assert not (tmp_path / "pending_orders.json").exists()  # queue cleared
