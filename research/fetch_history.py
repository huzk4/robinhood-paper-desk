#!/usr/bin/env python3
"""
fetch_history.py — pull long daily history into research/data/.

Separate cache from the live data/ dir on purpose: the daily loop only needs
~420 days, research needs 2014-present (one warmup year before the 2016 tune
window start). Runs on GitHub Actions (the sandbox has no market-data
egress). Reuses engine.data_feed fetchers: yfinance first, Stooq fallback.

Usage: python research/fetch_history.py [--start 2014-01-01]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import data_feed  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--start", default="2014-01-01")
    ap.add_argument("--out", default=os.path.join(ROOT, "research", "data"))
    args = ap.parse_args()

    import yaml
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    tickers = sorted(set(cfg["universe"]["stocks"] + cfg["universe"]["etfs"]
                         + cfg["macro_tickers"]))
    os.makedirs(args.out, exist_ok=True)

    ok, bad = 0, []
    for t in tickers:
        rows = []
        for fetcher in (data_feed._fetch_yfinance, data_feed._fetch_stooq):
            try:
                rows = fetcher(t, args.start)
                if rows:
                    break
            except Exception as e:
                print(f"  {t}: {fetcher.__name__} failed: {e}")
        if len(rows) < 500:
            bad.append(t)
            print(f"! {t}: only {len(rows)} bars — NOT cached")
            continue
        data_feed.save_cache(args.out, t, rows)
        ok += 1
        print(f"  {t}: {len(rows)} bars {rows[0]['date']}..{rows[-1]['date']}")

    print(f"cached {ok}/{len(tickers)} tickers to {args.out}"
          + (f"; missing: {bad}" if bad else ""))
    # missing macro tickers would silently weaken the macro pillar — fail loud
    missing_macro = [t for t in cfg["macro_tickers"] if t in bad]
    if missing_macro:
        print(f"FATAL: macro tickers missing history: {missing_macro}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
