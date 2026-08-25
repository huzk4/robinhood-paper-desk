"""Synthetic OHLCV generator — deterministic, no network. Used by tests and --synthetic demo."""
from __future__ import annotations

import datetime as dt
import math
import os
import tempfile

from engine import data_feed


def _series(kind: str, n: int = 320) -> list[float]:
    out = []
    for i in range(n):
        if kind == "bull":          # steady uptrend + mild cycle
            px = 100 * (1.0015 ** i) + 4 * math.sin(i / 9)
        elif kind == "bear":        # steady downtrend
            px = 300 * (0.9985 ** i) + 4 * math.sin(i / 7)
        elif kind == "rebound":     # downtrend then sharp V-recovery
            px = 200 * (0.998 ** i) if i < 270 else 200 * (0.998 ** 270) * (1.004 ** (i - 270))
        else:                       # flat chop
            px = 150 + 5 * math.sin(i / 5)
        out.append(round(px, 4))
    return out


def _dates(n: int) -> list[str]:
    d = dt.date(2025, 5, 1)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def write_ticker(cache_dir: str, ticker: str, closes: list[float]) -> None:
    dates = _dates(len(closes))
    rows = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        rows.append({"date": dates[i], "open": round(prev * 1.001, 4),
                     "high": round(max(prev, c) * 1.004, 4),
                     "low": round(min(prev, c) * 0.996, 4),
                     "close": c, "volume": 1_000_000})
    data_feed.save_cache(cache_dir, ticker, rows)


SYNTH_MAP = {
    # stocks
    "BULLCO": "bull", "BEARCO": "bear", "REBND": "rebound", "CHOPPY": "flat",
    # macro set
    "SPY": "bull", "RSP": "bull", "IWM": "flat", "HYG": "bull",
    "LQD": "flat", "TLT": "bear", "XLY": "bull", "XLP": "flat", "QQQ": "bull",
}


def make_synthetic_cache(cfg: dict, cache_dir: str | None = None) -> dict:
    cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "rpd-synth-data")
    for t, kind in SYNTH_MAP.items():
        write_ticker(cache_dir, t, _series(kind))
    cfg = dict(cfg)
    cfg["data"] = dict(cfg["data"], cache_dir=cache_dir)
    cfg["universe"] = {"stocks": ["BULLCO", "BEARCO", "REBND", "CHOPPY"],
                       "etfs": ["SPY", "QQQ", "TLT"]}
    cfg["sectors"] = {"BULLCO": "tech", "BEARCO": "energy",
                      "REBND": "fin", "CHOPPY": "health"}
    return cfg
