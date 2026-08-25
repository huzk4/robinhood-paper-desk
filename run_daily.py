#!/usr/bin/env python3
"""
run_daily.py — one entrypoint for the daily cycle.

  fetch data -> fill yesterday's orders at today's open -> score universe
  -> propose orders -> (advisory: park for approval | autonomous: queue)
  -> write signal packet + scorecard + ledger state.

Runs identically on GitHub Actions (with internet) and offline from cache.
PAPER ONLY: orders go to sim.broker.PaperBroker and nowhere else.

Usage:
  python run_daily.py                 # normal daily run (fetch + score)
  python run_daily.py --no-fetch      # cache-only (cloud sandbox)
  python run_daily.py --approve       # promote proposed orders to pending
  python run_daily.py --synthetic     # end-to-end demo on synthetic data
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import indicators  # noqa: E402  (vendored, engine/)
import score as score_mod  # noqa: E402
import macro_pillar  # noqa: E402
from engine import data_feed  # noqa: E402
from sim.broker import PaperBroker  # noqa: E402
from sim.ledger import Ledger  # noqa: E402


def load_config(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------- strategy

ENTRY_ACTIONS = {"RE-ENTRY (new cycle)"}
EXIT_ACTIONS = {"EXIT", "EXIT / TRIM"}


def propose_orders(cards: dict[str, dict], ledger: Ledger,
                   closes: dict[str, float], cfg: dict) -> list[dict]:
    """Deterministic rule layer on top of the 3-pillar cards."""
    s = cfg["strategy"]
    nav = ledger.nav(closes)
    orders = []
    for t, card in cards.items():
        action = card["decision"]["action"]
        total = card["pillar_total"]
        held = t in ledger.positions
        px = closes.get(t)
        if not px:
            continue
        if held and (action in EXIT_ACTIONS or total <= s["exit_at"]):
            orders.append({"side": "sell", "ticker": t,
                           "shares": ledger.positions[t]["shares"],
                           "reason": f"{action} (score {total:+d})"})
        elif (not held) and action in ENTRY_ACTIONS and total >= s["enter_at"]:
            shares = round(nav * s["target_position_pct"] / px, 4)
            if shares > 0:
                orders.append({"side": "buy", "ticker": t, "shares": shares,
                               "reason": f"{action} (score {total:+d})"})
    return orders


# --------------------------------------------------------------- pipeline

def run(cfg: dict, state_dir: str, fetch: bool, news: bool,
        inject_news: str | None = None) -> dict:
    cache = cfg["data"]["cache_dir"]
    uni = cfg["universe"]["stocks"] + cfg["universe"]["etfs"]
    need = sorted(set(uni + cfg["macro_tickers"]))

    if fetch:
        print("fetching data…")
        data_feed.refresh(cache, need, cfg["data"]["lookback_days"])

    # last two bars per ticker (today's open for fills, close for marks)
    opens, closes, dates = {}, {}, set()
    series = {}
    for t in need:
        rows = data_feed.load_cached(cache, t)
        if len(rows) < 30:
            print(f"  ! {t}: only {len(rows)} bars cached, skipping")
            continue
        series[t] = [r["close"] for r in rows]
        opens[t] = rows[-1]["open"]
        closes[t] = rows[-1]["close"]
        dates.add(rows[-1]["date"])
    if not closes:
        raise SystemExit("no cached data — run with fetch (on Actions) first")
    today = max(dates)

    os.makedirs(state_dir, exist_ok=True)
    ledger = Ledger.load(os.path.join(state_dir, "ledger.json"),
                         cfg["portfolio"]["start_cash"])
    broker = PaperBroker(ledger, cfg, state_dir)

    # 1. fill yesterday's queued orders at today's open
    fills = broker.process_fills(opens, closes, today)

    # 2. macro pillar (shared by all names)
    macro_in = {"series": {t: series[t] for t in cfg["macro_tickers"] if t in series}}
    macro = macro_pillar.score_macro(macro_in)
    macro_score = macro.pillar_score

    # 3. per-asset 3-pillar scorecards
    cards = {}
    for t in uni:
        if t not in series:
            continue
        cards[t] = score_mod.score_symbol(series[t], macro_score=macro_score,
                                          symbol=t, holding=t in ledger.positions)

    # 4. propose orders
    proposed = propose_orders(cards, ledger, closes, cfg)
    mode = cfg["strategy"]["mode"]
    if mode == "autonomous":
        broker.submit(proposed)
    else:  # advisory: park for human/agent approval (--approve promotes them)
        with open(os.path.join(state_dir, "proposed_orders.json"), "w") as f:
            json.dump(proposed, f, indent=2)

    # 5. news reference (never a signal)
    headlines = []
    if inject_news:
        from engine import news_feed
        headlines = news_feed.load_injected(inject_news)
    elif news:
        from engine import news_feed
        interesting = sorted(cards, key=lambda t: abs(cards[t]["pillar_total"]),
                             reverse=True)[:8]
        headlines = news_feed.get_headlines(interesting, limit_per_ticker=3)

    # 6. mark, persist, report
    snap = ledger.mark(closes, today)
    ledger.save(os.path.join(state_dir, "ledger.json"))

    packet = {
        "date": today, "mode": mode, "nav": snap["nav"], "cash": snap["cash"],
        "drawdown_from_peak": round(ledger.drawdown(closes), 4),
        "halted": ledger.halted,
        "macro": {"score": macro_score, "regime": macro.regime,
                  "composite": macro.composite, "notes": macro.notes},
        "positions": ledger.positions,
        "fills_today": fills,
        "proposed_orders": proposed,
        "scores": {t: {"total": c["pillar_total"],
                       "trend": c["pillars"]["trend"]["score"],
                       "momentum": c["pillars"]["momentum"]["score"],
                       "action": c["decision"]["action"],
                       "rationale": c["decision"]["rationale"]}
                   for t, c in cards.items()},
        "headlines": headlines,
    }
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "signal_packet.json"), "w") as f:
        json.dump(packet, f, indent=2, default=str)
    _write_scorecard_md(packet, cards, os.path.join(state_dir, "scorecard.md"))
    return packet


def _write_scorecard_md(packet: dict, cards: dict, path: str) -> None:
    L = [f"# Scorecard — {packet['date']}", "",
         f"NAV **${packet['nav']:,.2f}** · cash ${packet['cash']:,.2f} · "
         f"drawdown {packet['drawdown_from_peak']:.1%} · "
         f"macro {packet['macro']['score']:+d} ({packet['macro']['regime']})", ""]
    if packet["halted"]:
        L.append("**⚠ KILL SWITCH ACTIVE — trading halted pending human review.**\n")
    L.append("| Ticker | Trend | Mom | Macro | Total | Action |")
    L.append("|---|---:|---:|---:|---:|---|")
    for t in sorted(cards, key=lambda x: cards[x]["pillar_total"], reverse=True):
        c = cards[t]
        L.append(f"| {t} | {c['pillars']['trend']['score']:+d} "
                 f"| {c['pillars']['momentum']['score']:+d} "
                 f"| {packet['macro']['score']:+d} | **{c['pillar_total']:+d}** "
                 f"| {c['decision']['action']} |")
    if packet["fills_today"]:
        L += ["", "## Fills / rejections today", ""]
        for f_ in packet["fills_today"]:
            L.append(f"- {f_['status']} {f_['side']} {f_['shares']} {f_['ticker']} "
                     f"@ {f_['fill_price']} {('— ' + f_['detail']) if f_['detail'] else ''}")
    if packet["proposed_orders"]:
        L += ["", f"## Proposed orders ({packet['mode']})", ""]
        for o in packet["proposed_orders"]:
            L.append(f"- {o['side'].upper()} {o['shares']} {o['ticker']} — {o['reason']}")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")


def approve(state_dir: str, cfg: dict) -> None:
    src = os.path.join(state_dir, "proposed_orders.json")
    if not os.path.exists(src):
        print("nothing to approve")
        return
    with open(src) as f:
        orders = json.load(f)
    ledger = Ledger.load(os.path.join(state_dir, "ledger.json"),
                         cfg["portfolio"]["start_cash"])
    PaperBroker(ledger, cfg, state_dir).submit(orders)
    os.remove(src)
    print(f"approved {len(orders)} orders -> pending (fill at next open)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--state-dir", default=os.path.join(ROOT, "state"))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--inject-news", default=None)
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--synthetic", action="store_true",
                    help="end-to-end demo on synthetic data (no network)")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.approve:
        approve(args.state_dir, cfg)
        return 0

    if args.synthetic:
        from tests.synthetic import make_synthetic_cache
        cfg = make_synthetic_cache(cfg)  # rewrites cache_dir + universe
        args.no_fetch, args.no_news = True, True

    packet = run(cfg, args.state_dir, fetch=not args.no_fetch,
                 news=not args.no_news, inject_news=args.inject_news)
    print(json.dumps({k: packet[k] for k in
                      ("date", "nav", "cash", "macro", "proposed_orders")},
                     indent=2, default=str))
    print(f"\nscorecard: {os.path.join(args.state_dir, 'scorecard.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
