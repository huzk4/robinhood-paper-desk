#!/usr/bin/env python3
"""
backtest.py — event-driven daily backtests that reuse the LIVE code paths.

The simulator here is the same machinery the paper desk trades with:
sim.ledger.Ledger for accounting and sim.risk.check_order for guardrails.
Fills happen at the NEXT session's open (signals at close T, fill at open
T+1) with the configured slippage — no lookahead by construction, same as
the live paper broker.

Strategies:
  pillar      — the production 3-pillar rules (score.py + run_daily bands)
  momo_12_1   — classic 12-1 cross-sectional momentum, monthly top-5 stocks
  bb_meanrev  — Bollinger mean-reversion (buy <lower band + RSI<35, exit mid)
  spy_hold    — SPY buy-and-hold benchmark (same costs)

Honesty notes, printed on every report:
  * Universe is TODAY'S constituents — survivorship bias flatters results.
  * Costs modeled: slippage both ways, zero commission. No borrow/margin.
  * Macro pillar is refreshed every 21 bars (monthly) from prefix data only.

Usage:
  python research/backtest.py --data-dir research/data --start 2016-01-01
  python research/backtest.py --strategies pillar,spy_hold --json out.json

stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import score as score_mod  # noqa: E402
import macro_pillar  # noqa: E402
from engine import data_feed  # noqa: E402
from sim.ledger import Ledger  # noqa: E402
from sim import risk  # noqa: E402
from research.series_scorer import indicator_panel  # noqa: E402

MACRO_REFRESH_BARS = 21  # monthly


# ----------------------------------------------------------------- data

def load_universe(cfg: dict, data_dir: str, start: str, end: str):
    """
    Returns (dates, bars) where dates is the SPY trading calendar between
    start and end, and bars[ticker] = {date: {open, close}} for that range
    PLUS full history before it (for indicator warmup).
    """
    tickers = sorted(set(cfg["universe"]["stocks"] + cfg["universe"]["etfs"]
                         + cfg["macro_tickers"]))
    bars: dict[str, dict[str, dict]] = {}
    closes_full: dict[str, list[tuple[str, float]]] = {}
    for t in tickers:
        rows = data_feed.load_cached(data_dir, t)
        if not rows:
            continue
        bars[t] = {r["date"]: {"open": r["open"], "close": r["close"]}
                   for r in rows if r["date"] <= end}
        closes_full[t] = [(r["date"], r["close"]) for r in rows if r["date"] <= end]
    if "SPY" not in bars:
        raise SystemExit(f"no SPY data in {data_dir} — fetch history first")
    dates = [d for d in sorted(bars["SPY"]) if start <= d <= end]
    if not dates:
        raise SystemExit(f"no sessions in {start}..{end} in {data_dir}")
    return dates, bars, closes_full


class Panel:
    """Precomputed per-ticker indicator panels aligned to each ticker's own
    date axis, with O(1) lookup of 'panel row at or before date'."""

    def __init__(self, closes_full: dict[str, list[tuple[str, float]]]):
        self.dates: dict[str, list[str]] = {}
        self.closes: dict[str, list[float]] = {}
        self.panels: dict[str, list] = {}
        self.idx: dict[str, dict[str, int]] = {}
        for t, seq in closes_full.items():
            ds = [d for d, _ in seq]
            cs = [c for _, c in seq]
            self.dates[t] = ds
            self.closes[t] = cs
            self.panels[t] = indicator_panel(cs)
            self.idx[t] = {d: i for i, d in enumerate(ds)}

    def at(self, ticker: str, date: str):
        """(bar_index, ind_dict) for `date`, or (None, None)."""
        i = self.idx.get(ticker, {}).get(date)
        if i is None:
            return None, None
        return i, self.panels[ticker][i]


# ----------------------------------------------------------- strategies

class Strategy:
    """Interface: propose(date, panel, ledger, closes, cfg) -> [orders].
    Orders queue at close T and fill at open T+1 through the shared broker
    loop below. order: {side, ticker, shares, reason}."""

    name = "base"
    uses_risk_checks = True

    def warmup(self, panel: Panel, dates: list[str], cfg: dict):
        pass

    def propose(self, date, panel, ledger, closes, cfg):
        return []


class PillarStrategy(Strategy):
    """The production rules: score.py cards + run_daily's band layer."""
    name = "pillar"

    def __init__(self, enter_at=None, exit_at=None, target_pct=None):
        self.enter_at = enter_at
        self.exit_at = exit_at
        self.target_pct = target_pct
        self._macro_cache: dict[int, int] = {}
        self._bar_count = 0

    def warmup(self, panel, dates, cfg):
        self._macro_tickers = cfg["macro_tickers"]
        self._trade_tickers = cfg["universe"]["stocks"] + cfg["universe"]["etfs"]

    def _macro_score(self, panel: Panel, date: str) -> int | None:
        """Monthly macro refresh from prefix-only data."""
        bucket = self._bar_count // MACRO_REFRESH_BARS
        if bucket in self._macro_cache:
            return self._macro_cache[bucket]
        series = {}
        for t in self._macro_tickers:
            i = panel.idx.get(t, {}).get(date)
            if i is None:  # tolerate holes: last index <= date
                ds = panel.dates.get(t, [])
                lo = [j for j, d in enumerate(ds) if d <= date]
                i = lo[-1] if lo else None
            if i is not None and i >= 60:
                series[t] = panel.closes[t][:i + 1]
        score = None
        if len(series) >= 4:
            try:
                score = macro_pillar.score_macro({"series": series}).pillar_score
            except Exception:
                score = None
        self._macro_cache[bucket] = score
        return score

    def propose(self, date, panel, ledger, closes, cfg):
        s = dict(cfg["strategy"])
        if self.enter_at is not None:
            s["enter_at"] = self.enter_at
        if self.exit_at is not None:
            s["exit_at"] = self.exit_at
        if self.target_pct is not None:
            s["target_position_pct"] = self.target_pct
        macro = self._macro_score(panel, date)
        self._bar_count += 1
        nav = ledger.nav(closes)
        orders = []
        for t in self._trade_tickers:
            i, ind = panel.at(t, date)
            if ind is None or ind["ema200"] is None:
                continue
            held = t in ledger.positions
            trend, _ = score_mod.score_trend(ind)
            mom, _ = score_mod.score_momentum(ind)
            total = trend + mom + (macro or 0)
            decision = score_mod.decide(ind, trend, mom, macro, held)
            action = decision["action"]
            px = closes.get(t)
            if not px:
                continue
            if held and s.get("patient_exits") and action == "EXIT / TRIM":
                continue  # patient mode: no exhaustion trims (mirrors run_daily)
            if held and (action in ("EXIT", "EXIT / TRIM") or total <= s["exit_at"]):
                orders.append({"side": "sell", "ticker": t,
                               "shares": ledger.positions[t]["shares"],
                               "reason": f"{action} (score {total:+d})"})
            elif (not held) and action == "RE-ENTRY (new cycle)" and total >= s["enter_at"]:
                shares = round(nav * s["target_position_pct"] / px, 4)
                if shares > 0:
                    orders.append({"side": "buy", "ticker": t, "shares": shares,
                                   "reason": f"{action} (score {total:+d})"})
        return orders


class Momentum121(Strategy):
    """12-1 cross-sectional momentum: monthly, top-5 stocks equal-weight
    (15% each, 25% cash). Skip-month convention: r = P[t-21]/P[t-252] - 1."""
    name = "momo_12_1"
    uses_risk_checks = False  # its own sizing; costs still applied
    TOP_N, WEIGHT = 5, 0.15

    def __init__(self):
        self._bar = 0

    def warmup(self, panel, dates, cfg):
        self._stocks = cfg["universe"]["stocks"]

    def propose(self, date, panel, ledger, closes, cfg):
        self._bar += 1
        if (self._bar - 1) % 21 != 0:
            return []
        scores = {}
        for t in self._stocks:
            i = panel.idx.get(t, {}).get(date)
            if i is None or i < 252:
                continue
            c = panel.closes[t]
            if c[i - 252] > 0:
                scores[t] = c[i - 21] / c[i - 252] - 1.0
        if len(scores) < self.TOP_N:
            return []
        top = sorted(scores, key=scores.get, reverse=True)[:self.TOP_N]
        nav = ledger.nav(closes)
        orders = []
        for t in list(ledger.positions):
            if t not in top:
                orders.append({"side": "sell", "ticker": t,
                               "shares": ledger.positions[t]["shares"],
                               "reason": "momo rebalance: dropped from top-5"})
        for t in top:
            px = closes.get(t)
            if not px:
                continue
            have = ledger.positions.get(t, {"shares": 0})["shares"] * px
            target = nav * self.WEIGHT
            delta = target - have
            if abs(delta) < 0.01 * nav:
                continue
            shares = round(abs(delta) / px, 4)
            if shares > 0:
                orders.append({"side": "buy" if delta > 0 else "sell",
                               "ticker": t, "shares": shares,
                               "reason": f"momo rebalance to {self.WEIGHT:.0%}"})
        return orders


class BollingerMeanRev(Strategy):
    """Buy close<lower band with RSI<35; exit at middle band or RSI>60."""
    name = "bb_meanrev"

    def warmup(self, panel, dates, cfg):
        self._tickers = cfg["universe"]["stocks"] + cfg["universe"]["etfs"]

    def propose(self, date, panel, ledger, closes, cfg):
        s = cfg["strategy"]
        nav = ledger.nav(closes)
        orders = []
        for t in self._tickers:
            i, ind = panel.at(t, date)
            if ind is None or ind["bb_lower"] is None or ind["rsi14"] is None:
                continue
            px = closes.get(t)
            if not px:
                continue
            held = t in ledger.positions
            if held and (px >= ind["bb_mid"] or ind["rsi14"] > 60):
                orders.append({"side": "sell", "ticker": t,
                               "shares": ledger.positions[t]["shares"],
                               "reason": "meanrev exit: mid band / RSI>60"})
            elif not held and px < ind["bb_lower"] and ind["rsi14"] < 35:
                shares = round(nav * s["target_position_pct"] / px, 4)
                if shares > 0:
                    orders.append({"side": "buy", "ticker": t, "shares": shares,
                                   "reason": "meanrev entry: <lower band, RSI<35"})
        return orders


class SpyHold(Strategy):
    """All-in SPY at the first fillable open. The benchmark."""
    name = "spy_hold"
    uses_risk_checks = False

    def propose(self, date, panel, ledger, closes, cfg):
        if ledger.positions or "SPY" not in closes:
            return []
        shares = round(ledger.cash * 0.9995 / closes["SPY"], 4)
        return [{"side": "buy", "ticker": "SPY", "shares": shares,
                 "reason": "buy & hold benchmark"}]


STRATEGIES = {c.name: c for c in
              (PillarStrategy, Momentum121, BollingerMeanRev, SpyHold)}


# -------------------------------------------------------------- backtest

def run_backtest(strategy: Strategy, cfg: dict, dates: list[str],
                 bars: dict, panel: Panel) -> dict:
    """Daily loop: fill yesterday's orders at today's open (slippage, risk
    checks via sim.risk), mark at close, then let the strategy queue orders."""
    led = Ledger(cfg["portfolio"]["start_cash"])
    slip = cfg["fill"]["slippage_bps"] / 10_000.0
    commission = cfg["fill"]["commission"]
    pending: list[dict] = []
    trades: list[dict] = []
    rejections = 0
    strategy.warmup(panel, dates, cfg)

    for date in dates:
        opens = {t: b[date]["open"] for t, b in bars.items() if date in b}
        closes = {t: b[date]["close"] for t, b in bars.items() if date in b}

        # 1. fill yesterday's queue at today's open
        trades_today = 0
        for o in pending:
            t = o["ticker"]
            if t not in opens:
                continue
            raw = opens[t]
            px = raw * (1 + slip) if o["side"] == "buy" else raw * (1 - slip)
            if strategy.uses_risk_checks:
                ok, why = risk.check_order(
                    {**o, "est_price": px}, led, closes, cfg, trades_today, date)
                if not ok:
                    rejections += 1
                    continue
            elif led.halted:
                continue
            try:
                if o["side"] == "buy":
                    afford = led.cash / px
                    shares = min(o["shares"], round(afford, 4))
                    if shares <= 0:
                        continue
                    led.apply_buy(t, shares, px, commission, date)
                else:
                    shares = min(o["shares"], led.positions.get(t, {"shares": 0})["shares"])
                    if shares <= 0:
                        continue
                    led.apply_sell(t, shares, px, commission, date)
            except ValueError:
                rejections += 1
                continue
            trades_today += 1
            trades.append({"date": date, "ticker": t, "side": o["side"],
                           "shares": shares, "fill_price": round(px, 4),
                           "reason": o["reason"]})
        pending = []

        # 2. mark at close, kill switch
        led.mark(closes, date)
        if strategy.uses_risk_checks:
            risk.check_kill_switch(led, closes, cfg)

        # 3. queue tomorrow's orders from today's close data
        if not led.halted:
            pending = strategy.propose(date, panel, led, closes, cfg)

    return {"strategy": strategy.name, "nav": led.nav_history,
            "trades": trades, "rejections": rejections,
            "final_positions": {t: p["shares"] for t, p in led.positions.items()},
            "halted": led.halted,
            "metrics": compute_metrics(led.nav_history, trades)}


# --------------------------------------------------------------- metrics

def compute_metrics(nav_hist: list[dict], trades: list[dict]) -> dict:
    if len(nav_hist) < 2:
        return {}
    navs = [x["nav"] for x in nav_hist]
    n_years = max(len(navs) / 252.0, 1e-9)
    total = navs[-1] / navs[0] - 1.0
    cagr = (navs[-1] / navs[0]) ** (1.0 / n_years) - 1.0
    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    vol = math.sqrt(var) * math.sqrt(252)
    sharpe = (mean * 252) / vol if vol > 0 else 0.0
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = max(mdd, 1.0 - v / peak)
    traded = sum(t["shares"] * t["fill_price"] for t in trades)
    avg_nav = sum(navs) / len(navs)
    turnover = traded / avg_nav / n_years if avg_nav else 0.0
    # round trips: FIFO by ticker
    wins = losses = 0
    lots: dict[str, list[list[float]]] = {}
    for t in trades:
        if t["side"] == "buy":
            lots.setdefault(t["ticker"], []).append([t["shares"], t["fill_price"]])
        else:
            rem, pnl = t["shares"], 0.0
            q = lots.get(t["ticker"], [])
            while rem > 1e-9 and q:
                take = min(rem, q[0][0])
                pnl += take * (t["fill_price"] - q[0][1])
                q[0][0] -= take
                rem -= take
                if q[0][0] <= 1e-9:
                    q.pop(0)
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
    closed = wins + losses
    return {"total_return": round(total, 4), "cagr": round(cagr, 4),
            "ann_vol": round(vol, 4), "sharpe": round(sharpe, 3),
            "max_drawdown": round(mdd, 4),
            "turnover_x_per_year": round(turnover, 2),
            "n_trades": len(trades), "closed_round_trips": closed,
            "hit_rate": round(wins / closed, 3) if closed else None,
            "bars": len(nav_hist), "years": round(n_years, 2)}


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "research", "data"))
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2099-01-01")
    ap.add_argument("--strategies", default="pillar,momo_12_1,bb_meanrev,spy_hold")
    ap.add_argument("--json", default=os.path.join(ROOT, "research", "results",
                                                   "backtests.json"))
    args = ap.parse_args()

    import yaml
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    if not os.path.isdir(args.data_dir):
        # fall back to the live daily cache (short history — smoke tests only)
        args.data_dir = os.path.join(ROOT, "data")
        print(f"! research data dir missing, falling back to {args.data_dir}")

    dates, bars, closes_full = load_universe(cfg, args.data_dir, args.start, args.end)
    print(f"universe loaded: {len(closes_full)} tickers, "
          f"{len(dates)} sessions {dates[0]}..{dates[-1]}")
    panel = Panel(closes_full)

    results = {}
    for name in args.strategies.split(","):
        name = name.strip()
        strat = STRATEGIES[name]()
        r = run_backtest(strat, copy.deepcopy(cfg), dates, bars, panel)
        results[name] = r
        m = r["metrics"]
        print(f"{name:12s} CAGR {m.get('cagr', 0):+7.2%}  Sharpe {m.get('sharpe', 0):5.2f}  "
              f"MaxDD {m.get('max_drawdown', 0):6.2%}  trades {m.get('n_trades', 0):4d}  "
              f"halted={r['halted']}")

    spy = results.get("spy_hold", {}).get("metrics", {})
    for name, r in results.items():
        if name != "spy_hold" and spy:
            r["metrics"]["excess_cagr_vs_spy"] = round(
                r["metrics"].get("cagr", 0) - spy.get("cagr", 0), 4)

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    out = {"start": dates[0], "end": dates[-1],
           "honesty": ["Universe = today's constituents (survivorship bias).",
                       "Fills at next open with slippage; zero commission.",
                       "Macro pillar refreshed monthly from prefix-only data.",
                       "Paper results overstate live results."],
           "results": {k: {kk: vv for kk, vv in v.items() if kk != "trades"}
                       | {"n_trades": len(v["trades"])}
                       for k, v in results.items()}}
    # keep NAV series but thin to weekly points to keep the file small
    for k in out["results"]:
        nav = results[k]["nav"]
        out["results"][k]["nav"] = nav[::5] + ([nav[-1]] if nav and (len(nav) - 1) % 5 else [])
    with open(args.json, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
