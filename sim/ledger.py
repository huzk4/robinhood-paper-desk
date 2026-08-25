#!/usr/bin/env python3
"""
ledger.py — paper portfolio accounting. The single source of truth.

Tracks cash, positions (shares, avg cost, first/last acquisition date),
realized P&L, NAV history, and peak NAV for drawdown checks. stdlib only.
"""
from __future__ import annotations

import json
import os


class Ledger:
    def __init__(self, start_cash: float):
        self.cash: float = float(start_cash)
        self.start_cash: float = float(start_cash)
        # ticker -> {shares, avg_cost, opened, last_buy}
        self.positions: dict[str, dict] = {}
        self.realized_pnl: float = 0.0
        self.nav_history: list[dict] = []  # {date, nav, cash}
        self.peak_nav: float = float(start_cash)
        self.halted: bool = False

    # ------------------------------------------------------------- fills
    def apply_buy(self, ticker: str, shares: float, price: float,
                  commission: float, date: str) -> None:
        cost = shares * price + commission
        if cost > self.cash + 1e-9:
            raise ValueError(f"buy {ticker}: cost {cost:.2f} exceeds cash {self.cash:.2f}")
        self.cash -= cost
        p = self.positions.get(ticker)
        if p:
            total_shares = p["shares"] + shares
            p["avg_cost"] = (p["avg_cost"] * p["shares"] + price * shares) / total_shares
            p["shares"] = total_shares
            p["last_buy"] = date
        else:
            self.positions[ticker] = {"shares": shares, "avg_cost": price,
                                      "opened": date, "last_buy": date}

    def apply_sell(self, ticker: str, shares: float, price: float,
                   commission: float, date: str) -> float:
        p = self.positions.get(ticker)
        if not p or shares > p["shares"] + 1e-9:
            raise ValueError(f"sell {ticker}: not enough shares")
        proceeds = shares * price - commission
        self.cash += proceeds
        pnl = (price - p["avg_cost"]) * shares - commission
        self.realized_pnl += pnl
        p["shares"] -= shares
        if p["shares"] <= 1e-9:
            del self.positions[ticker]
        return pnl

    # ------------------------------------------------------------ valuation
    def nav(self, prices: dict[str, float]) -> float:
        v = self.cash
        for t, p in self.positions.items():
            px = prices.get(t)
            if px is None:
                raise ValueError(f"nav: missing price for {t}")
            v += p["shares"] * px
        return v

    def mark(self, prices: dict[str, float], date: str) -> dict:
        nav = self.nav(prices)
        self.peak_nav = max(self.peak_nav, nav)
        snap = {"date": date, "nav": round(nav, 2), "cash": round(self.cash, 2)}
        if not self.nav_history or self.nav_history[-1]["date"] != date:
            self.nav_history.append(snap)
        else:
            self.nav_history[-1] = snap
        return snap

    def drawdown(self, prices: dict[str, float]) -> float:
        if self.peak_nav <= 0:
            return 0.0
        return 1.0 - self.nav(prices) / self.peak_nav

    # ---------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {"cash": round(self.cash, 4), "start_cash": self.start_cash,
                "positions": self.positions, "realized_pnl": round(self.realized_pnl, 4),
                "nav_history": self.nav_history, "peak_nav": round(self.peak_nav, 4),
                "halted": self.halted}

    @classmethod
    def from_dict(cls, d: dict) -> "Ledger":
        led = cls(d["start_cash"])
        led.cash = d["cash"]
        led.positions = d.get("positions", {})
        led.realized_pnl = d.get("realized_pnl", 0.0)
        led.nav_history = d.get("nav_history", [])
        led.peak_nav = d.get("peak_nav", led.start_cash)
        led.halted = d.get("halted", False)
        return led

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str, start_cash: float) -> "Ledger":
        if os.path.exists(path):
            with open(path) as f:
                return cls.from_dict(json.load(f))
        return cls(start_cash)
