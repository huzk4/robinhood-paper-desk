#!/usr/bin/env python3
"""
risk.py — hard guardrails. Orders that breach a limit are REJECTED, not warned.

The agent cannot override a rejection; changing limits means editing
config.yaml through a reviewed commit, never at run time. stdlib only.
"""
from __future__ import annotations

import datetime as dt


def _sector_of(ticker: str, cfg: dict) -> str:
    sectors = cfg.get("sectors", {}) or {}
    if ticker in sectors:
        return sectors[ticker]
    etfs = (cfg.get("universe", {}) or {}).get("etfs", [])
    return "etf" if ticker in etfs else "unknown"


def check_order(order: dict, ledger, prices: dict[str, float], cfg: dict,
                trades_today: int, today: str) -> tuple[bool, str]:
    """
    order: {side: buy|sell, ticker, shares, est_price}
    Returns (ok, reason). Reason explains any rejection.
    """
    r = cfg["risk"]
    t = order["ticker"]
    side = order["side"]
    shares = float(order["shares"])
    px = float(order.get("est_price") or prices.get(t) or 0)

    if ledger.halted:
        return False, "HALTED: kill switch active (drawdown breach). Human review required."
    if shares <= 0:
        return False, "shares must be > 0"
    if px <= 0:
        return False, f"no price available for {t}"
    if trades_today >= r["max_trades_per_day"]:
        return False, f"max_trades_per_day ({r['max_trades_per_day']}) reached"

    nav = ledger.nav(prices)
    notional = shares * px

    if side == "buy":
        # cash floor
        cash_after = ledger.cash - notional
        if cash_after < r["min_cash_pct"] * nav:
            return False, (f"cash floor: cash after buy {cash_after:,.0f} < "
                           f"{r['min_cash_pct']:.0%} of NAV {nav:,.0f}")
        # position concentration
        existing = ledger.positions.get(t, {"shares": 0})["shares"] * px
        if existing + notional > r["max_position_pct"] * nav:
            return False, (f"position cap: {t} would be "
                           f"{(existing + notional) / nav:.1%} of NAV "
                           f"(max {r['max_position_pct']:.0%})")
        # sector concentration
        sec = _sector_of(t, cfg)
        sec_val = sum(p["shares"] * prices.get(s, 0)
                      for s, p in ledger.positions.items()
                      if _sector_of(s, cfg) == sec)
        if sec_val + notional > r["max_sector_pct"] * nav:
            return False, (f"sector cap: {sec} would be "
                           f"{(sec_val + notional) / nav:.1%} of NAV "
                           f"(max {r['max_sector_pct']:.0%})")
    elif side == "sell":
        p = ledger.positions.get(t)
        if not p or shares > p["shares"] + 1e-9:
            return False, f"cannot sell {shares} {t}: holding {p['shares'] if p else 0}"
        # minimum holding period
        held_days = _trading_day_gap(p["last_buy"], today)
        if held_days < r["min_holding_days"]:
            return False, (f"min holding: {t} bought {p['last_buy']}, "
                           f"{held_days} sessions ago (min {r['min_holding_days']})")
    else:
        return False, f"unknown side {side!r}"

    return True, "ok"


def check_kill_switch(ledger, prices: dict[str, float], cfg: dict) -> bool:
    """Returns True (and sets halted) if drawdown from peak breaches the limit."""
    dd = ledger.drawdown(prices)
    if dd > cfg["risk"]["max_drawdown_halt"]:
        ledger.halted = True
        return True
    return False


def _trading_day_gap(d1: str, d2: str) -> int:
    """Approximate trading-session gap between two ISO dates (weekdays only)."""
    a = dt.date.fromisoformat(d1)
    b = dt.date.fromisoformat(d2)
    if b <= a:
        return 0
    days, cur = 0, a
    while cur < b:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days
