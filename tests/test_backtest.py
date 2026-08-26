"""Backtest engine tests — deterministic synthetic data, no network."""
import math
import os
import sys

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import indicators  # noqa: E402
from research.series_scorer import indicator_panel  # noqa: E402
from research.backtest import (  # noqa: E402
    Panel, PillarStrategy, SpyHold, STRATEGIES, compute_metrics,
    load_universe, run_backtest)
from tests.synthetic import make_synthetic_cache, _series  # noqa: E402


def _cfg(tmp_path):
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    cfg = make_synthetic_cache(cfg, cache_dir=str(tmp_path / "data"))
    return cfg


# ------------------------------------------------------- panel parity

def test_panel_matches_compute_on_prefixes():
    """series_scorer per-bar dicts == indicators.compute on each prefix."""
    close = _series("rebound", 320)
    panel = indicator_panel(close)
    for t in (35, 100, 219, 260, 290, 319):  # spans warmup boundaries
        want = indicators.compute(close[:t + 1])
        got = panel[t]
        assert got is not None, f"bar {t} missing"
        for k, v in want.items():
            g = got[k]
            if isinstance(v, float) and isinstance(g, float):
                assert math.isclose(v, g, rel_tol=1e-12, abs_tol=1e-12), \
                    f"bar {t} key {k}: {v} != {g}"
            else:
                assert v == g, f"bar {t} key {k}: {v!r} != {g!r}"


def test_panel_prefix_invariance_no_lookahead():
    """Panel values at bar t are identical when the future is deleted."""
    close = _series("bull", 320)
    full = indicator_panel(close)
    trunc = indicator_panel(close[:250])
    for t in (60, 150, 249):
        assert full[t] == trunc[t]


# ----------------------------------------------------- backtest engine

def _run(tmp_path, name, **kw):
    cfg = _cfg(tmp_path)
    dates, bars, closes_full = load_universe(
        cfg, cfg["data"]["cache_dir"], "2025-05-01", "2099-01-01")
    panel = Panel(closes_full)
    strat = PillarStrategy(**kw) if kw else STRATEGIES[name]()
    return run_backtest(strat, cfg, dates, bars, panel), cfg


def test_spy_hold_benchmark_math(tmp_path):
    r, cfg = _run(tmp_path, "spy_hold")
    assert len(r["trades"]) == 1 and r["trades"][0]["ticker"] == "SPY"
    fill = r["trades"][0]
    # slippage applied against you on the buy
    assert fill["fill_price"] > 0
    nav_end = r["nav"][-1]["nav"]
    assert nav_end > 0
    m = r["metrics"]
    assert m["n_trades"] == 1 and m["max_drawdown"] >= 0


def test_pillar_respects_guardrails(tmp_path):
    r, cfg = _run(tmp_path, "pillar")
    risk = cfg["risk"]
    start = cfg["portfolio"]["start_cash"]
    # reconstruct position notionals per fill day from the trade log:
    # every accepted fill passed sim.risk.check_order, so caps held by
    # construction; assert the OUTPUT is consistent with them anyway.
    by_day = {}
    for t in r["trades"]:
        by_day.setdefault(t["date"], []).append(t)
    for day, ts in by_day.items():
        assert len(ts) <= risk["max_trades_per_day"]
    # single-position cap at entry: no buy bigger than max_position_pct of
    # a NAV that can never exceed the running max NAV that day
    navs = {x["date"]: x["nav"] for x in r["nav"]}
    for t in r["trades"]:
        if t["side"] == "buy":
            notional = t["shares"] * t["fill_price"]
            assert notional <= risk["max_position_pct"] * navs[t["date"]] * 1.02
    # min holding period: no sell within min_holding_days sessions of last buy
    last_buy = {}
    dates_order = [x["date"] for x in r["nav"]]
    dindex = {d: i for i, d in enumerate(dates_order)}
    for t in r["trades"]:
        if t["side"] == "buy":
            last_buy[t["ticker"]] = t["date"]
        else:
            lb = last_buy.get(t["ticker"])
            if lb:
                assert dindex[t["date"]] - dindex[lb] >= risk["min_holding_days"]


def test_pillar_takes_re_entry_trades(tmp_path):
    """The production rules must produce RE-ENTRY buys on the synthetic
    universe (the bull-cycle tickers dip and reclaim EMA20 on the sin wave).

    Note a smooth V-recovery (REBND) correctly does NOT trade: its rebound
    fires inside the death cross — tactical only — and afterwards there is
    no fresh trigger. Don't-chase is by design.
    """
    r, _ = _run(tmp_path, "pillar")
    buys = [t for t in r["trades"] if t["side"] == "buy"]
    assert buys, "expected at least one RE-ENTRY buy on the synthetic universe"
    assert all("RE-ENTRY" in t["reason"] for t in buys)


def test_all_strategies_run_clean(tmp_path):
    for name in STRATEGIES:
        r, _ = _run(tmp_path, name)
        assert r["nav"], name
        assert r["metrics"]["bars"] == len(r["nav"])


def test_metrics_flat_series():
    nav = [{"date": f"2025-01-{d:02d}", "nav": 100.0} for d in range(1, 21)]
    m = compute_metrics(nav, [])
    assert m["total_return"] == 0 and m["sharpe"] == 0 and m["max_drawdown"] == 0
