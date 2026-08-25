import math

import pytest

from sim.ledger import Ledger


def test_buy_sell_math_matches_hand_calc():
    led = Ledger(100_000)
    led.apply_buy("AAPL", 100, 200.0, 0.0, "2026-01-05")
    assert led.cash == pytest.approx(80_000)
    assert led.positions["AAPL"]["shares"] == 100
    assert led.positions["AAPL"]["avg_cost"] == pytest.approx(200.0)

    # average up: 50 more @ 220 -> avg (100*200 + 50*220)/150 = 206.6667
    led.apply_buy("AAPL", 50, 220.0, 0.0, "2026-01-06")
    assert led.positions["AAPL"]["avg_cost"] == pytest.approx(206.6667, abs=1e-3)
    assert led.cash == pytest.approx(69_000)

    # sell 150 @ 210 -> proceeds 31500, pnl (210-206.6667)*150 = 500
    pnl = led.apply_sell("AAPL", 150, 210.0, 0.0, "2026-01-10")
    assert pnl == pytest.approx(500.0, abs=1e-6)
    assert "AAPL" not in led.positions
    assert led.cash == pytest.approx(100_500)
    assert led.realized_pnl == pytest.approx(500.0)


def test_nav_and_drawdown():
    led = Ledger(100_000)
    led.apply_buy("SPY", 100, 500.0, 0.0, "2026-01-05")
    assert led.nav({"SPY": 500.0}) == pytest.approx(100_000)
    led.mark({"SPY": 600.0}, "2026-01-06")           # nav 110k, peak 110k
    assert led.peak_nav == pytest.approx(110_000)
    dd = led.drawdown({"SPY": 400.0})                 # nav 90k vs peak 110k
    assert dd == pytest.approx(1 - 90_000 / 110_000)


def test_cannot_overspend_or_oversell():
    led = Ledger(1_000)
    with pytest.raises(ValueError):
        led.apply_buy("SPY", 100, 500.0, 0.0, "2026-01-05")
    with pytest.raises(ValueError):
        led.apply_sell("SPY", 1, 500.0, 0.0, "2026-01-05")


def test_roundtrip_persistence(tmp_path):
    led = Ledger(50_000)
    led.apply_buy("QQQ", 10, 400.0, 1.0, "2026-02-02")
    led.mark({"QQQ": 410.0}, "2026-02-02")
    p = tmp_path / "ledger.json"
    led.save(str(p))
    led2 = Ledger.load(str(p), 50_000)
    assert led2.cash == pytest.approx(led.cash)
    assert led2.positions["QQQ"]["shares"] == 10
    assert led2.nav({"QQQ": 410.0}) == pytest.approx(led.nav({"QQQ": 410.0}))
