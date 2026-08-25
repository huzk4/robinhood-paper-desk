#!/usr/bin/env python3
"""
broker.py — the paper broker. The ONLY broker in this system.

Order lifecycle (no lookahead by construction):
  day T (after close): strategy/agent submits orders -> state/pending_orders.json
  day T+1 (next run) : orders fill at T+1's OPEN, with slippage against you,
                       after passing risk checks. Fills append to trades.csv.

There is no code path to a real brokerage anywhere in this repository.
stdlib only.
"""
from __future__ import annotations

import csv
import json
import os

from . import risk

TRADE_COLUMNS = ["date", "ticker", "side", "shares", "fill_price", "commission",
                 "reason", "status", "detail"]


class PaperBroker:
    def __init__(self, ledger, cfg: dict, state_dir: str = "state"):
        self.ledger = ledger
        self.cfg = cfg
        self.state_dir = state_dir
        self.pending_path = os.path.join(state_dir, "pending_orders.json")
        self.trades_path = os.path.join(state_dir, "trades.csv")

    # ------------------------------------------------------------- pending
    def load_pending(self) -> list[dict]:
        if os.path.exists(self.pending_path):
            with open(self.pending_path) as f:
                return json.load(f)
        return []

    def submit(self, orders: list[dict]) -> None:
        """Queue orders for next-open fill. order: {side,ticker,shares,reason}."""
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.pending_path, "w") as f:
            json.dump(orders, f, indent=2)

    # --------------------------------------------------------------- fills
    def process_fills(self, opens: dict[str, float], closes: dict[str, float],
                      date: str) -> list[dict]:
        """
        Fill pending orders at today's open. `opens` are today's opening
        prices; `closes` are latest closes used for NAV-based risk checks.
        Returns fill/rejection records (also appended to trades.csv).
        """
        pending = self.load_pending()
        results, trades_today = [], 0
        slip = self.cfg["fill"]["slippage_bps"] / 10_000.0
        commission = self.cfg["fill"]["commission"]

        # kill switch first — evaluated on current marks
        if risk.check_kill_switch(self.ledger, closes, self.cfg):
            results.append({"date": date, "ticker": "*", "side": "halt",
                            "shares": 0, "fill_price": 0, "commission": 0,
                            "reason": "kill_switch",
                            "status": "HALTED",
                            "detail": f"drawdown > {self.cfg['risk']['max_drawdown_halt']:.0%}; "
                                      "all pending orders cancelled"})
            pending = []

        for o in pending:
            t = o["ticker"]
            open_px = opens.get(t)
            if open_px is None:
                rec = self._rec(date, o, 0.0, commission, "REJECTED", "no opening price today")
            else:
                est = open_px * (1 + slip) if o["side"] == "buy" else open_px * (1 - slip)
                o2 = dict(o, est_price=est)
                ok, why = risk.check_order(o2, self.ledger, closes, self.cfg,
                                           trades_today, date)
                if not ok:
                    rec = self._rec(date, o, est, commission, "REJECTED", why)
                else:
                    if o["side"] == "buy":
                        self.ledger.apply_buy(t, float(o["shares"]), est, commission, date)
                    else:
                        self.ledger.apply_sell(t, float(o["shares"]), est, commission, date)
                    trades_today += 1
                    rec = self._rec(date, o, est, commission, "FILLED", "")
            results.append(rec)

        # clear the queue; append audit rows
        if os.path.exists(self.pending_path):
            os.remove(self.pending_path)
        self._append_trades(results)
        return results

    # --------------------------------------------------------------- audit
    def _rec(self, date, o, px, commission, status, detail) -> dict:
        return {"date": date, "ticker": o["ticker"], "side": o["side"],
                "shares": o["shares"], "fill_price": round(px, 4),
                "commission": commission, "reason": o.get("reason", ""),
                "status": status, "detail": detail}

    def _append_trades(self, records: list[dict]) -> None:
        if not records:
            return
        os.makedirs(self.state_dir, exist_ok=True)
        new = not os.path.exists(self.trades_path)
        with open(self.trades_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
            if new:
                w.writeheader()
            for r in records:
                w.writerow(r)
