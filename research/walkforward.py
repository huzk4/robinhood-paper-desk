#!/usr/bin/env python3
"""
walkforward.py — tune / test / holdout validation for the pillar strategy.

Protocol (fixed before looking at results, per the build plan):
  TUNE     2016-01-01 .. 2021-12-31   grid-search enter/exit bands + size
  TEST     2022-01-01 .. 2024-12-31   run ONLY the tune winner
  HOLDOUT  2025-01-01 .. present      untouched by any tuning decision

The winner is picked on TUNE Sharpe (ties: higher CAGR). TEST tells you how
much of that survives out-of-sample; HOLDOUT is the honest number to quote.
A strategy that does not beat SPY buy-and-hold on TEST after costs does not
graduate to driving the paper portfolio.

Usage:
  python research/walkforward.py --data-dir research/data
stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research.backtest import (  # noqa: E402
    PillarStrategy, SpyHold, Panel, load_universe, run_backtest)

WINDOWS = {"tune": ("2016-01-01", "2021-12-31"),
           "test": ("2022-01-01", "2024-12-31"),
           "holdout": ("2025-01-01", "2099-01-01")}

GRID = {"enter_at": [2, 3, 4],
        "exit_at": [-3, -2, -1],
        "target_pct": [0.05, 0.08]}


def run_window(params, cfg, data_dir, start, end, strategy_cls=PillarStrategy):
    dates, bars, closes_full = load_universe(cfg, data_dir, start, end)
    panel = Panel(closes_full)
    strat = strategy_cls(**params) if params is not None else strategy_cls()
    r = run_backtest(strat, copy.deepcopy(cfg), dates, bars, panel)
    return r["metrics"], dates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "research", "data"))
    ap.add_argument("--json", default=os.path.join(ROOT, "research", "results",
                                                   "walkforward.json"))
    args = ap.parse_args()

    import yaml
    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    # ---- TUNE: grid search -------------------------------------------
    t0, t1 = WINDOWS["tune"]
    dates, bars, closes_full = load_universe(cfg, args.data_dir, t0, t1)
    panel = Panel(closes_full)  # share the panel across the grid (big win)
    rows = []
    for e, x, tp in itertools.product(GRID["enter_at"], GRID["exit_at"],
                                      GRID["target_pct"]):
        strat = PillarStrategy(enter_at=e, exit_at=x, target_pct=tp)
        m = run_backtest(strat, copy.deepcopy(cfg), dates, bars, panel)["metrics"]
        rows.append({"enter_at": e, "exit_at": x, "target_pct": tp, **m})
        print(f"tune enter={e:+d} exit={x:+d} size={tp:.0%}: "
              f"CAGR {m.get('cagr', 0):+7.2%} Sharpe {m.get('sharpe', 0):5.2f} "
              f"MaxDD {m.get('max_drawdown', 0):6.2%}")
    rows.sort(key=lambda r: (r.get("sharpe") or -9, r.get("cagr") or -9),
              reverse=True)
    best = rows[0]
    params = {k: best[k] for k in ("enter_at", "exit_at", "target_pct")}
    print(f"tune winner: {params}")

    # SPY benchmark per window + winner on test/holdout ------------------
    report = {"grid": rows, "winner": params, "windows": {}}
    for wname, (w0, w1) in WINDOWS.items():
        try:
            m_p, ds = run_window(params, cfg, args.data_dir, w0, w1)
            m_s, _ = run_window(None, cfg, args.data_dir, w0, w1,
                                strategy_cls=SpyHold)
        except SystemExit as e:
            report["windows"][wname] = {"error": str(e)}
            continue
        report["windows"][wname] = {
            "range": [ds[0], ds[-1]], "pillar": m_p, "spy_hold": m_s,
            "excess_cagr": round(m_p.get("cagr", 0) - m_s.get("cagr", 0), 4)}
        print(f"{wname:8s} pillar CAGR {m_p.get('cagr', 0):+7.2%} "
              f"vs SPY {m_s.get('cagr', 0):+7.2%}")

    report["honesty"] = [
        "Grid searched on TUNE only; TEST/HOLDOUT ran once with the winner.",
        "Universe = today's constituents (survivorship bias flatters everything).",
        "Fills at next open with slippage; zero commission.",
        "18-cell grid on 6 years of data still overfits — treat TUNE numbers as ceiling."]

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w") as f:
        json.dump(report, f, indent=1)
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
