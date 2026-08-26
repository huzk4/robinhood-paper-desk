#!/usr/bin/env python3
"""
make_research_page.py — render docs/research.html from research/results/.

Companion page to the live dashboard: backtest equity curves, the metrics
table, and the walk-forward report. Same self-contained style as
reports/make_dashboard.py. stdlib only.
"""
from __future__ import annotations

import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "research", "results")
OUT = os.path.join(ROOT, "docs", "research.html")

W, H, PL, PR, PT, PB = 860, 320, 52, 16, 14, 30

SERIES_STYLE = {  # css class suffix, label
    "pillar": ("pillar", "3-pillar (production rules)"),
    "momo_12_1": ("momo", "12-1 momentum top-5"),
    "bb_meanrev": ("bbmr", "Bollinger mean-reversion"),
    "spy_hold": ("spyh", "SPY buy & hold"),
}


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def curves_svg(bt: dict) -> str:
    series = {}
    for name, r in bt.get("results", {}).items():
        nav = r.get("nav") or []
        if len(nav) >= 2:
            base = nav[0]["nav"]
            series[name] = [(x["date"], 100.0 * x["nav"] / base) for x in nav]
    if not series:
        return "<p class='note'>No backtest results yet — run research/backtest.py.</p>"
    all_dates = sorted({d for pts in series.values() for d, _ in pts})
    vals = [v for pts in series.values() for _, v in pts]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    x_of = {d: PL + (W - PL - PR) * i / max(len(all_dates) - 1, 1)
            for i, d in enumerate(all_dates)}

    def y_of(v):
        return PT + (H - PT - PB) * (1 - (v - lo) / (hi - lo))

    grid, labels = [], []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = y_of(v)
        grid.append(f"<line class='grid' x1='{PL}' y1='{y:.1f}' x2='{W - PR}' y2='{y:.1f}'/>")
        labels.append(f"<text class='tick' x='{PL - 6}' y='{y + 4:.1f}' text-anchor='end'>{v:.0f}</text>")
    for i in range(0, len(all_dates), max(len(all_dates) // 6, 1)):
        d = all_dates[i]
        labels.append(f"<text class='tick' x='{x_of[d]:.1f}' y='{H - 8}' text-anchor='middle'>{d}</text>")

    paths, ends = [], []
    for name, pts in series.items():
        cls, _ = SERIES_STYLE.get(name, ("other", name))
        dstr = " ".join(f"{'M' if i == 0 else 'L'}{x_of[d]:.1f},{y_of(v):.1f}"
                        for i, (d, v) in enumerate(pts))
        paths.append(f"<path class='ln ln-{cls}' d='{dstr}'/>")
        d_last, v_last = pts[-1]
        ends.append(f"<text class='endlbl end-{cls}' x='{x_of[d_last] - 4:.1f}' "
                    f"y='{y_of(v_last) - 6:.1f}' text-anchor='end'>{v_last:.0f}</text>")

    legend = "".join(
        f"<span><span class='sw sw-{SERIES_STYLE[n][0]}'></span>{html.escape(SERIES_STYLE[n][1])}</span>"
        for n in series if n in SERIES_STYLE)
    return (f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Backtest equity curves'>"
            + "".join(grid + labels + paths + ends) + "</svg>"
            f"<div class='legend'>{legend}</div>"
            f"<p class='note'>Indexed to 100 at {html.escape(bt.get('start', ''))}. "
            f"Weekly samples of daily NAV.</p>")


def metrics_table(bt: dict) -> str:
    cols = [("cagr", "CAGR", "pct"), ("ann_vol", "Vol", "pct"),
            ("sharpe", "Sharpe", "raw"), ("max_drawdown", "MaxDD", "pct"),
            ("turnover_x_per_year", "Turnover×/yr", "raw"),
            ("n_trades", "Trades", "int"), ("hit_rate", "Hit rate", "pct"),
            ("excess_cagr_vs_spy", "vs SPY", "pct")]
    head = "<tr><th>Strategy</th>" + "".join(f"<th class='num'>{h}</th>" for _, h, _ in cols) + "</tr>"
    rows = []
    for name, r in bt.get("results", {}).items():
        m = r.get("metrics", {})
        tds = []
        for key, _, kind in cols:
            v = m.get(key)
            if v is None:
                tds.append("<td class='num'>—</td>")
            elif kind == "pct":
                cls = " pos" if v > 0 else (" neg" if v < 0 else "")
                cls = cls if key in ("cagr", "excess_cagr_vs_spy") else ""
                tds.append(f"<td class='num{cls}'>{v:+.1%}</td>" if key == "excess_cagr_vs_spy"
                           else f"<td class='num{cls}'>{v:.1%}</td>")
            elif kind == "int":
                tds.append(f"<td class='num'>{v:,}</td>")
            else:
                tds.append(f"<td class='num'>{v:.2f}</td>")
        label = SERIES_STYLE.get(name, (None, name))[1]
        halted = " ⚠halted" if r.get("halted") else ""
        rows.append(f"<tr><td class='tick-td'>{html.escape(label)}{halted}</td>{''.join(tds)}</tr>")
    return f"<div class='tablewrap'><table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"


def walkforward_block(wf: dict) -> str:
    if not wf:
        return "<p class='note'>No walk-forward report yet — run research/walkforward.py.</p>"
    p = wf.get("winner", {})
    head = ("<tr><th>Window</th><th>Range</th><th class='num'>Pillar CAGR</th>"
            "<th class='num'>SPY CAGR</th><th class='num'>Excess</th>"
            "<th class='num'>Sharpe</th><th class='num'>MaxDD</th></tr>")
    rows = []
    for wname in ("tune", "test", "holdout"):
        w = wf.get("windows", {}).get(wname)
        if not w or "error" in (w or {}):
            continue
        mp, ms = w["pillar"], w["spy_hold"]
        ex = w["excess_cagr"]
        rows.append(
            f"<tr><td class='tick-td'>{wname.upper()}</td>"
            f"<td>{w['range'][0]} → {w['range'][1]}</td>"
            f"<td class='num'>{mp.get('cagr', 0):.1%}</td>"
            f"<td class='num'>{ms.get('cagr', 0):.1%}</td>"
            f"<td class='num {'pos' if ex > 0 else 'neg'}'>{ex:+.1%}</td>"
            f"<td class='num'>{mp.get('sharpe', 0):.2f}</td>"
            f"<td class='num'>{mp.get('max_drawdown', 0):.1%}</td></tr>")
    grid_note = (f"Winner of the tune grid: enter ≥ {p.get('enter_at')}, "
                 f"exit ≤ {p.get('exit_at')}, size {p.get('target_pct', 0):.0%} "
                 f"of NAV per position. TEST and HOLDOUT ran once with these "
                 f"parameters; HOLDOUT was never used for any tuning decision.")
    return (f"<div class='tablewrap'><table><thead>{head}</thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
            f"<p class='note'>{html.escape(grid_note)}</p>")


def build() -> None:
    bt = load(os.path.join(RESULTS, "backtests.json"), {})
    wf = load(os.path.join(RESULTS, "walkforward.json"), {})
    honesty = (bt or {}).get("honesty", []) + (wf or {}).get("honesty", [])
    seen, notes = set(), []
    for n in honesty:
        if n not in seen:
            seen.add(n)
            notes.append(f"<li>{html.escape(n)}</li>")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Desk — Research</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#f6f8f6; --surface:#fcfcfb; --line:#e3e6e2;
  --ink:#0b0b0b; --ink2:#52514e; --ink3:#8a897f;
  --pillar:#2a78d6; --momo:#7a3fd1; --bbmr:#0f8f5f; --spyh:#eb6834;
  --in-ink:#0a6b0a; --out-ink:#a32e2e; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#141413; --surface:#1a1a19; --line:#31312e;
  --ink:#ffffff; --ink2:#c3c2b7; --ink3:#8a897f;
  --pillar:#3987e5; --momo:#a06ee8; --bbmr:#27b57f; --spyh:#d95926;
  --in-ink:#5fd35f; --out-ink:#ef8a8a; }} }}
:root[data-theme="dark"] {{
  --bg:#141413; --surface:#1a1a19; --line:#31312e;
  --ink:#ffffff; --ink2:#c3c2b7; --ink3:#8a897f;
  --pillar:#3987e5; --momo:#a06ee8; --bbmr:#27b57f; --spyh:#d95926;
  --in-ink:#5fd35f; --out-ink:#ef8a8a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:960px; margin:0 auto; padding:28px 18px 64px; }}
header h1 {{ font-size:1.5rem; margin:0; }}
.sub {{ color:var(--ink2); font-size:.9rem; margin:4px 0 0; }}
.sub a {{ color:var(--pillar); }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:18px; }}
.card h2 {{ font-size:1.02rem; margin:0 0 12px; }}
.num {{ font-variant-numeric:tabular-nums; }}
.num.pos {{ color:var(--in-ink); }} .num.neg {{ color:var(--out-ink); }}
.note {{ color:var(--ink3); font-size:.85rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
th {{ text-align:left; font-size:.7rem; text-transform:uppercase; letter-spacing:.07em;
  color:var(--ink3); font-weight:600; padding:6px 10px; border-bottom:1px solid var(--line); }}
th.num, td.num {{ text-align:right; }}
td {{ padding:6px 10px; border-bottom:1px solid var(--line); color:var(--ink2); }}
tr:last-child td {{ border-bottom:0; }}
.tick-td {{ font-weight:650; color:var(--ink); }}
.tablewrap {{ overflow-x:auto; }}
svg {{ width:100%; height:auto; display:block; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.tick {{ fill:var(--ink3); font-size:11px; }}
.ln {{ fill:none; stroke-width:2.2; stroke-linejoin:round; }}
.ln-pillar {{ stroke:var(--pillar); }} .ln-momo {{ stroke:var(--momo); }}
.ln-bbmr {{ stroke:var(--bbmr); }} .ln-spyh {{ stroke:var(--spyh); stroke-width:1.8; stroke-dasharray:none; }}
.endlbl {{ font-size:11px; font-weight:600; }}
.end-pillar {{ fill:var(--pillar); }} .end-momo {{ fill:var(--momo); }}
.end-bbmr {{ fill:var(--bbmr); }} .end-spyh {{ fill:var(--spyh); }}
.legend {{ display:flex; flex-wrap:wrap; gap:14px; font-size:.82rem; color:var(--ink2); margin-top:8px; }}
.sw {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; }}
.sw-pillar {{ background:var(--pillar); }} .sw-momo {{ background:var(--momo); }}
.sw-bbmr {{ background:var(--bbmr); }} .sw-spyh {{ background:var(--spyh); }}
ul {{ margin:6px 0 0 18px; padding:0; color:var(--ink2); font-size:.88rem; }}
li {{ margin-bottom:4px; }}
footer {{ color:var(--ink3); font-size:.78rem; margin-top:26px; }}
</style></head><body><div class="wrap">
<header><h1>Paper Desk — Research</h1>
<p class="sub">Backtests &amp; walk-forward validation · {html.escape(bt.get('start', '—'))} →
{html.escape(bt.get('end', '—'))} · <a href="index.html">← live dashboard</a></p></header>
<div class="card"><h2>Backtest equity curves</h2>{curves_svg(bt or {})}</div>
<div class="card"><h2>Strategy metrics</h2>{metrics_table(bt or {}) if bt else "<p class='note'>No results yet.</p>"}</div>
<div class="card"><h2>Walk-forward validation (3-pillar)</h2>{walkforward_block(wf or {})}</div>
<div class="card"><h2>Honesty notes</h2><ul>{''.join(notes) or '<li>—</li>'}</ul></div>
<footer>robinhood-paper-desk · simulated money, not financial advice · generated by research/make_research_page.py</footer>
</div></body></html>"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(page)
    print(f"wrote {OUT} ({len(page):,} bytes)")


if __name__ == "__main__":
    build()
