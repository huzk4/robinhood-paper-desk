#!/usr/bin/env python3
"""
series_scorer.py — per-bar indicator dicts for backtesting, in ONE pass.

indicators.compute(close) returns the indicator stack for the LAST bar only.
Calling it once per bar in a backtest is O(n^2) and takes ~30+ min for a
10-year universe. This module computes every series once and then emits, for
each bar t, a dict with exactly the same keys and values as
indicators.compute(close[:t+1]) would return.

Parity holds by construction: every series used (EMA, Wilder RSI, MACD, TRIX,
rolling Bollinger) is forward-recursive or windowed, so its value at bar t is
identical whether the future exists or not. tests/test_backtest.py asserts
this against indicators.compute() on prefixes.

stdlib only.
"""
from __future__ import annotations

import os
import sys
from statistics import pstdev
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import indicators as I  # noqa: E402


def _bollinger_series(close: list[float], period: int = 20, mult: float = 2.0):
    """Rolling Bollinger — same formula as indicators.bollinger (pstdev)."""
    n = len(close)
    mid = [None] * n
    up = [None] * n
    lo = [None] * n
    pb: list[Optional[float]] = [None] * n
    for t in range(period - 1, n):
        window = close[t - period + 1:t + 1]
        m = sum(window) / period
        sd = pstdev(window)
        u, l = m + mult * sd, m - mult * sd
        rng = u - l
        mid[t], up[t], lo[t] = m, u, l
        pb[t] = (close[t] - l) / rng if rng != 0 else 0.5
    return mid, up, lo, pb


def _first_valid(series) -> Optional[int]:
    for i, v in enumerate(series):
        if v is not None:
            return i
    return None


def _prev_valid(series, t, fv):
    """Last non-None value strictly before index t (warmup-then-contiguous)."""
    if fv is None or t - 1 < fv:
        return None
    return series[t - 1]


def _slope_at(series, t, fv, lookback=5):
    """indicators._slope on the prefix ending at t (warmup-then-contiguous)."""
    if fv is None or series[t] is None or t - lookback < fv:
        return None
    return series[t] - series[t - lookback]


def indicator_panel(close: list[float], slope_lookback: int = 5) -> list[Optional[dict]]:
    """
    Returns a list of per-bar indicator dicts (None for warmup bars < 30),
    each matching indicators.compute(close[:t+1], slope_lookback).
    """
    n = len(close)
    ema20 = I.ema_series(close, 20)
    ema50 = I.ema_series(close, 50)
    ema200 = I.ema_series(close, 200)
    rsi = I.rsi_wilder(close, 14)
    macd_line, macd_sig, macd_hist = I.macd(close, 12, 26, 9)
    trix_line, trix_sig = I.trix(close, 15, 9)
    bb_mid, bb_up, bb_lo, pct_b = _bollinger_series(close, 20, 2.0)

    # bars since last close below EMA20, tracked incrementally
    last_below = [None] * n  # index of most recent bar (<= t) with close < ema20
    prev_idx = None
    for t in range(n):
        if ema20[t] is not None and close[t] < ema20[t]:
            prev_idx = t
        last_below[t] = prev_idx

    fvs = {id(s): _first_valid(s) for s in
           (ema20, ema50, ema200, rsi, macd_hist, trix_line, trix_sig)}

    out: list[Optional[dict]] = [None] * n
    for t in range(n):
        if t + 1 < 30:  # too little history to be meaningful (matches engine warmups)
            continue
        warn = None
        if t + 1 < 210:
            warn = (f"Only {t + 1} bars; EMA200/some indicators may be None. "
                    f"Ideal >=220.")
        bsb = (t - last_below[t]) if last_below[t] is not None else None
        out[t] = {
            "n_bars": t + 1,
            "warning": warn,
            "close": close[t],
            "ema20": ema20[t], "ema50": ema50[t], "ema200": ema200[t],
            "ema20_slope": _slope_at(ema20, t, fvs[id(ema20)], slope_lookback),
            "ema50_slope": _slope_at(ema50, t, fvs[id(ema50)], slope_lookback),
            "ema200_slope": _slope_at(ema200, t, fvs[id(ema200)], slope_lookback),
            "rsi14": rsi[t], "rsi14_prev": _prev_valid(rsi, t, fvs[id(rsi)]),
            "macd_line": macd_line[t], "macd_signal": macd_sig[t],
            "macd_hist": macd_hist[t],
            "macd_hist_prev": _prev_valid(macd_hist, t, fvs[id(macd_hist)]),
            "trix": trix_line[t],
            "trix_prev": _prev_valid(trix_line, t, fvs[id(trix_line)]),
            "trix_signal": trix_sig[t],
            "trix_signal_prev": _prev_valid(trix_sig, t, fvs[id(trix_sig)]),
            "bars_since_below_ema20": bsb,
            "bb_mid": bb_mid[t], "bb_upper": bb_up[t], "bb_lower": bb_lo[t],
            "percent_b": pct_b[t],
        }
    return out
