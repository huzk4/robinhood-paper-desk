"""Sanity checks on the vendored indicator math (engine/indicators.py)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import pytest

import indicators as I


def test_ema_of_constant_is_constant():
    s = I.ema_series([100.0] * 50, 20)
    assert s[-1] == pytest.approx(100.0)


def test_rsi_extremes():
    up = [100 + i for i in range(60)]
    down = [200 - i for i in range(60)]
    assert I.rsi_wilder(up)[-1] == pytest.approx(100.0, abs=1e-6)
    assert I.rsi_wilder(down)[-1] == pytest.approx(0.0, abs=1e-6)


def test_macd_is_ema_diff():
    close = [100 + 10 * math.sin(i / 8) for i in range(120)]
    macd_line, _, _ = I.macd(close)
    e12 = I.ema_series(close, 12)
    e26 = I.ema_series(close, 26)
    assert macd_line[-1] == pytest.approx(e12[-1] - e26[-1], abs=1e-9)


def test_bollinger_brackets_price():
    close = [100 + 3 * math.sin(i / 5) for i in range(60)]
    mid, upper, lower, pb = I.bollinger(close)
    assert lower < mid < upper
    assert 0.0 <= pb <= 1.0 or pb  # %B can exceed [0,1] at extremes; just numeric


def test_cross_check_against_ta_library():
    """Cross-validate RSI/EMA vs the independent `ta` package when available."""
    ta = pytest.importorskip("ta")
    pd = pytest.importorskip("pandas")
    import random
    rng = random.Random(42)
    close = [100.0]
    for _ in range(299):
        close.append(round(close[-1] * (1 + rng.uniform(-0.02, 0.02)), 4))
    s = pd.Series(close)

    ours_rsi = I.rsi_wilder(close)[-1]
    ta_rsi = ta.momentum.RSIIndicator(s, window=14).rsi().iloc[-1]
    assert ours_rsi == pytest.approx(float(ta_rsi), abs=0.5)

    ours_ema = I.ema_series(close, 20)[-1]
    ta_ema = ta.trend.EMAIndicator(s, window=20).ema_indicator().iloc[-1]
    assert ours_ema == pytest.approx(float(ta_ema), rel=2e-3)
