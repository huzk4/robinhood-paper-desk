#!/usr/bin/env python3
"""
data_feed.py — daily OHLCV with local CSV cache.

Source chain: yfinance -> Stooq CSV endpoint. Both need open internet, so in
the cloud sandbox this module runs cache-only; GitHub Actions does the
fetching and commits the cache (see .github/workflows/daily_run.yml).

Cache format: data/<TICKER>.csv with columns date,open,high,low,close,volume
(ascending dates). stdlib + optional yfinance.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import urllib.request

CACHE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def cache_path(cache_dir: str, ticker: str) -> str:
    return os.path.join(cache_dir, f"{ticker.upper()}.csv")


def load_cached(cache_dir: str, ticker: str) -> list[dict]:
    """Return rows (dicts, ascending date) from cache, or []."""
    path = cache_path(cache_dir, ticker)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f)]
    for r in rows:
        for k in ("open", "high", "low", "close", "volume"):
            r[k] = float(r[k]) if r[k] not in ("", None) else None
    rows.sort(key=lambda r: r["date"])
    return rows


def save_cache(cache_dir: str, ticker: str, rows: list[dict]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["date"])
    with open(cache_path(cache_dir, ticker), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in CACHE_COLUMNS})


# ----------------------------------------------------------------- fetchers

def _fetch_yfinance(ticker: str, start: str) -> list[dict]:
    import yfinance as yf  # optional dep

    df = yf.download(ticker, start=start, interval="1d",
                     progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        return []
    if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    out = []
    for idx, row in df.iterrows():
        out.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0) or 0),
        })
    return out


def _fetch_stooq(ticker: str, start: str) -> list[dict]:
    d1 = start.replace("-", "")
    d2 = dt.date.today().strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&d1={d1}&d2={d2}&i=d"
    with urllib.request.urlopen(url, timeout=30) as r:
        text = r.read().decode()
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        if not rec.get("Date"):
            continue
        rows.append({
            "date": rec["Date"],
            "open": float(rec["Open"]), "high": float(rec["High"]),
            "low": float(rec["Low"]), "close": float(rec["Close"]),
            "volume": float(rec.get("Volume") or 0),
        })
    return rows


def refresh(cache_dir: str, tickers: list[str], lookback_days: int = 420,
            verbose: bool = True) -> dict:
    """Fetch/refresh cache for tickers. Returns {ticker: n_rows_or_error}."""
    start = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    report = {}
    for t in tickers:
        rows, err = [], None
        for fetcher in (_fetch_yfinance, _fetch_stooq):
            try:
                rows = fetcher(t, start)
                if rows:
                    break
            except Exception as e:  # noqa: BLE001 — try next source
                err = f"{fetcher.__name__}: {type(e).__name__}: {e}"
        if rows:
            save_cache(cache_dir, t, rows)
            report[t] = len(rows)
        else:
            report[t] = f"FAILED ({err})"
        if verbose:
            print(f"  {t:<6} {report[t]}")
    return report


def closes(cache_dir: str, ticker: str) -> list[float]:
    return [r["close"] for r in load_cached(cache_dir, ticker)]


def last_bar(cache_dir: str, ticker: str) -> dict | None:
    rows = load_cached(cache_dir, ticker)
    return rows[-1] if rows else None


if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] or ["SPY"]
    print(refresh("data", tickers))
