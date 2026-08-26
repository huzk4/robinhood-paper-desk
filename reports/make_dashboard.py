#!/usr/bin/env python3
"""
make_dashboard.py — render docs/index.html from state/ + data/.

Self-contained HTML (inline CSS/SVG/JS, no external assets) served by GitHub
Pages and previewable anywhere. Run by the daily workflow after run_daily.py.
stdlib only.
"""
from __future__ import annotations

import csv
import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "docs", "index.html")

W, H, PAD_L, PAD_R, PAD_T, PAD_B = 860, 300, 52, 16, 14, 30


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def load_trades(n=12):
    path = os.path.join(STATE, "trades.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:][::-1]


def spy_closes_since(start_date):
    path = os.path.join(DATA, "SPY.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["date"] >= start_date]
    return [(r["date"], float(r["close"])) for r in rows]


# ------------------------------------------------------------------ chart

def equity_chart(nav_hist, spy):
    """Indexed (=100 at inception) NAV vs SPY as an SVG line chart."""
    if not nav_hist:
        return "<p class='note'>No NAV history yet.</p>"
    navs = [(d["date"], d["nav"]) for d in nav_hist]
    base_nav = navs[0][1]
    spy_map = dict(spy)
    base_spy = spy_map.get(navs[0][0]) or (spy[0][1] if spy else None)

    dates = sorted({d for d, _ in navs} | {d for d, _ in spy if d >= navs[0][0]})
    nav_map = dict(navs)
    series = {"Portfolio": [], "SPY": []}
    for d in dates:
        if d in nav_map:
            series["Portfolio"].append((d, 100.0 * nav_map[d] / base_nav))
        if base_spy and d in spy_map:
            series["SPY"].append((d, 100.0 * spy_map[d] / base_spy))

    all_vals = [v for pts in series.values() for _, v in pts] or [100.0]
    lo, hi = min(all_vals), max(all_vals)
    span = max(hi - lo, 1.0)
    lo, hi = lo - span * 0.15, hi + span * 0.15
    x_of = lambda i: PAD_L + (W - PAD_L - PAD_R) * (i / max(len(dates) - 1, 1))
    y_of = lambda v: PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo))
    xi = {d: i for i, d in enumerate(dates)}

    grid, gl = [], 4
    for k in range(gl + 1):
        v = lo + (hi - lo) * k / gl
        y = y_of(v)
        grid.append(f"<line x1='{PAD_L}' y1='{y:.1f}' x2='{W-PAD_R}' y2='{y:.1f}' class='grid'/>"
                    f"<text x='{PAD_L-8}' y='{y+4:.1f}' class='tick' text-anchor='end'>{v:.1f}</text>")
    # x ticks: first, mid, last
    for i in {0, len(dates) // 2, len(dates) - 1}:
        grid.append(f"<text x='{x_of(i):.1f}' y='{H-8}' class='tick' text-anchor='middle'>{dates[i][5:]}</text>")

    paths, endlabels, used_ys = [], [], []
    for name, cls in (("SPY", "spy"), ("Portfolio", "port")):
        pts = series[name]
        if not pts:
            continue
        coords = [(x_of(xi[d]), y_of(v)) for d, v in pts]
        if len(coords) == 1:
            paths.append(f"<circle cx='{coords[0][0]:.1f}' cy='{coords[0][1]:.1f}' r='4.5' class='dot-{cls}'/>")
        else:
            dstr = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
            paths.append(f"<path d='{dstr}' class='line-{cls}'/>")
            paths.append(f"<circle cx='{coords[-1][0]:.1f}' cy='{coords[-1][1]:.1f}' r='4' class='dot-{cls}'/>")
        ly = coords[-1][1] + 4
        while any(abs(ly - u) < 14 for u in used_ys):  # nudge colliding labels apart
            ly += 14
        used_ys.append(ly)
        endlabels.append(f"<text x='{coords[-1][0]+8:.1f}' y='{ly:.1f}' class='endlbl endlbl-{cls}'>{name} {pts[-1][1]:.2f}</text>")

    data_json = html.escape(json.dumps({
        "dates": dates,
        "port": {d: round(v, 2) for d, v in series["Portfolio"]},
        "spy": {d: round(v, 2) for d, v in series["SPY"]},
        "geom": [W, H, PAD_L, PAD_R, PAD_T, PAD_B, lo, hi],
    }), quote=True)
    note = ("<p class='note'>Indexed to 100 at inception (" + dates[0] +
            "). The curve builds one point per trading day.</p>") if len(navs) < 5 else ""
    return (f"<div class='chartwrap'><svg viewBox='0 0 {W} {H}' role='img' data-eq='{data_json}' "
            f"aria-label='Portfolio vs SPY, indexed'>"
            + "".join(grid) + "".join(paths) + "".join(endlabels) +
            f"<line class='xh' y1='{PAD_T}' y2='{H-PAD_B}' x1='-9' x2='-9'/></svg>"
            f"<div class='tt' hidden></div></div>"
            f"<div class='legend'><span><i class='sw sw-port'></i>Portfolio</span>"
            f"<span><i class='sw sw-spy'></i>SPY</span></div>" + note)


# ------------------------------------------------------------------ tables

def divbar(score, lim):
    """Small diverging bar, center baseline."""
    w, h, half = 64, 12, 32
    frac = max(-1.0, min(1.0, score / lim))
    bw = abs(frac) * (half - 2)
    x = half if frac >= 0 else half - bw
    cls = "pos" if frac > 0 else ("neg" if frac < 0 else "zero")
    bar = f"<rect x='{x:.1f}' y='1' width='{max(bw,1.5):.1f}' height='{h-2}' rx='2' class='db-{cls}'/>"
    return (f"<svg class='db' viewBox='0 0 {w} {h}' aria-label='{score:+d}'>"
            f"<line x1='{half}' y1='0' x2='{half}' y2='{h}' class='db-mid'/>{bar}</svg>")


def scorecard_rows(scores, macro):
    rows = []
    for t, s in sorted(scores.items(), key=lambda kv: -kv[1]["total"]):
        act = s["action"]
        acls = ("act-in" if "RE-ENTRY" in act or "TACTICAL" in act else
                "act-out" if "EXIT" in act or "STAY OUT" in act else "act-hold")
        rows.append(
            f"<tr><td class='tick-td'>{t}</td>"
            f"<td>{divbar(s['trend'],2)}</td><td>{divbar(s['momentum'],2)}</td>"
            f"<td>{divbar(macro,2)}</td>"
            f"<td class='tot num'>{s['total']:+d}</td>"
            f"<td><span class='chip {acls}'>{html.escape(act)}</span></td></tr>")
    return "".join(rows)


def positions_block(packet, trades):
    pos = packet.get("positions", {})
    out = []
    if pos:
        out.append("<table><thead><tr><th>Ticker</th><th class='num'>Shares</th>"
                   "<th class='num'>Avg cost</th><th>Opened</th></tr></thead><tbody>")
        for t, p in sorted(pos.items()):
            out.append(f"<tr><td class='tick-td'>{t}</td><td class='num'>{p['shares']:.4f}</td>"
                       f"<td class='num'>${p['avg_cost']:.2f}</td><td>{p['opened']}</td></tr>")
        out.append("</tbody></table>")
    else:
        out.append("<p class='note'>All cash — no open positions.</p>")
    pending = load_json(os.path.join(STATE, "pending_orders.json"), [])
    proposed = packet.get("proposed_orders", [])
    if pending:
        out.append("<h3>Queued for next open</h3><ul class='olist'>")
        out += [f"<li><b>{o['side'].upper()} {o['shares']} {o['ticker']}</b>"
                f" <span class='note'>{html.escape(o.get('reason',''))}</span></li>" for o in pending]
        out.append("</ul>")
    elif proposed:
        out.append("<h3>Proposed (awaiting approval)</h3><ul class='olist'>")
        out += [f"<li><b>{o['side'].upper()} {o['shares']} {o['ticker']}</b>"
                f" <span class='note'>{html.escape(o.get('reason',''))}</span></li>" for o in proposed]
        out.append("</ul>")
    if trades:
        out.append("<h3>Recent activity</h3><table><thead><tr><th>Date</th><th>Order</th>"
                   "<th class='num'>Fill</th><th>Status</th></tr></thead><tbody>")
        for r in trades:
            out.append(f"<tr><td>{r['date']}</td><td>{r['side'].upper()} {r['shares']} "
                       f"<span class='tick-td'>{r['ticker']}</span></td>"
                       f"<td class='num'>{('$'+format(float(r['fill_price']),'.2f')) if r['fill_price'] not in ('','0') else '—'}</td>"
                       f"<td><span class='chip {'act-in' if r['status']=='FILLED' else 'act-out'}'>"
                       f"{r['status']}</span></td></tr>")
        out.append("</tbody></table>")
    return "".join(out)


# ------------------------------------------------------------------ page

def build():
    packet = load_json(os.path.join(STATE, "signal_packet.json"), {})
    ledger = load_json(os.path.join(STATE, "ledger.json"), {})
    if not packet:
        raise SystemExit("no signal_packet.json — run run_daily.py first")
    nav_hist = ledger.get("nav_history", [])
    spy = spy_closes_since(nav_hist[0]["date"]) if nav_hist else []
    trades = load_trades()
    macro = packet["macro"]
    dd = packet.get("drawdown_from_peak", 0.0)
    rpnl = ledger.get("realized_pnl", 0.0)
    start = ledger.get("start_cash", 100000.0)
    ret = (packet["nav"] / start - 1.0) if start else 0.0

    kpis = [
        ("NAV", f"${packet['nav']:,.2f}", f"{ret:+.2%} since inception", "pos" if ret >= 0 else "neg"),
        ("Cash", f"${packet['cash']:,.2f}", "buying power", ""),
        ("Realized P&L", f"${rpnl:,.2f}", "closed trades", "pos" if rpnl >= 0 else "neg"),
        ("Drawdown", f"{dd:.1%}", "from peak · halts at 15%", "neg" if dd > 0.05 else ""),
        ("Macro", f"{macro['score']:+d}", html.escape(macro.get("regime", "")), "pos" if macro["score"] > 0 else ("neg" if macro["score"] < 0 else "")),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='kpi-l'>{k}</div><div class='kpi-v num'>{v}</div>"
        f"<div class='kpi-s {cls}'>{s}</div></div>" for k, v, s, cls in kpis)

    research_link = (" · <a href='research.html'>research →</a>"
                     if os.path.exists(os.path.join(ROOT, "docs", "research.html"))
                     else "")

    halted = ("<div class='halt'>⚠ Kill switch active — trading halted pending review.</div>"
              if packet.get("halted") else "")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Desk</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#f6f8f6; --surface:#fcfcfb; --line:#e3e6e2;
  --ink:#0b0b0b; --ink2:#52514e; --ink3:#8a897f;
  --port:#2a78d6; --spy:#eb6834;
  --div-pos:#2a78d6; --div-neg:#e34948; --div-mid:#d8d7d2;
  --in:#0ca30c22; --in-ink:#0a6b0a; --out:#d03b3b1f; --out-ink:#a32e2e;
  --hold:#8a897f22; --hold-ink:#52514e; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#141413; --surface:#1a1a19; --line:#31312e;
  --ink:#ffffff; --ink2:#c3c2b7; --ink3:#8a897f;
  --port:#3987e5; --spy:#d95926;
  --div-pos:#3987e5; --div-neg:#e66767; --div-mid:#41403c;
  --in:#0ca30c2e; --in-ink:#5fd35f; --out:#d03b3b2e; --out-ink:#ef8a8a;
  --hold:#8a897f2e; --hold-ink:#c3c2b7; }} }}
:root[data-theme="dark"] {{
  --bg:#141413; --surface:#1a1a19; --line:#31312e;
  --ink:#ffffff; --ink2:#c3c2b7; --ink3:#8a897f;
  --port:#3987e5; --spy:#d95926;
  --div-pos:#3987e5; --div-neg:#e66767; --div-mid:#41403c;
  --in:#0ca30c2e; --in-ink:#5fd35f; --out:#d03b3b2e; --out-ink:#ef8a8a;
  --hold:#8a897f2e; --hold-ink:#c3c2b7; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:960px; margin:0 auto; padding:28px 18px 64px; }}
header h1 {{ font-size:1.5rem; margin:0; }}
.sub {{ color:var(--ink2); font-size:.9rem; margin:4px 0 0; }}
.sub a {{ color:var(--port); }}
.mode {{ display:inline-block; margin-left:8px; padding:2px 10px; border:1px solid var(--line);
  border-radius:999px; font-size:.75rem; color:var(--ink2); vertical-align:2px; }}
.halt {{ background:var(--out); color:var(--out-ink); border-radius:8px; padding:12px 16px; margin-top:14px; font-weight:600; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:22px 0; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.kpi-l {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--ink3); }}
.kpi-v {{ font-size:1.35rem; font-weight:650; margin-top:2px; }}
.kpi-s {{ font-size:.78rem; color:var(--ink2); }}
.kpi-s.pos {{ color:var(--in-ink); }} .kpi-s.neg {{ color:var(--out-ink); }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:18px; }}
.card h2 {{ font-size:1.02rem; margin:0 0 12px; }}
.card h3 {{ font-size:.85rem; margin:16px 0 6px; color:var(--ink2); }}
.num {{ font-variant-numeric:tabular-nums; }}
.note {{ color:var(--ink3); font-size:.85rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
th {{ text-align:left; font-size:.7rem; text-transform:uppercase; letter-spacing:.07em;
  color:var(--ink3); font-weight:600; padding:6px 10px; border-bottom:1px solid var(--line); }}
th.num, td.num {{ text-align:right; }}
td {{ padding:6px 10px; border-bottom:1px solid var(--line); color:var(--ink2); }}
tr:last-child td {{ border-bottom:0; }}
.tick-td {{ font-weight:650; color:var(--ink); }}
.tot {{ font-weight:700; color:var(--ink); }}
.tablewrap {{ overflow-x:auto; }}
.chip {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.72rem; font-weight:600; white-space:nowrap; }}
.act-in {{ background:var(--in); color:var(--in-ink); }}
.act-out {{ background:var(--out); color:var(--out-ink); }}
.act-hold {{ background:var(--hold); color:var(--hold-ink); }}
.db {{ width:64px; height:12px; display:block; }}
.db-mid {{ stroke:var(--div-mid); stroke-width:2; }}
.db-pos {{ fill:var(--div-pos); }} .db-neg {{ fill:var(--div-neg); }} .db-zero {{ fill:var(--div-mid); }}
.chartwrap {{ position:relative; }}
svg[data-eq] {{ width:100%; height:auto; display:block; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.tick {{ fill:var(--ink3); font-size:11px; }}
.line-port {{ stroke:var(--port); stroke-width:2.5; fill:none; stroke-linejoin:round; }}
.line-spy {{ stroke:var(--spy); stroke-width:2; fill:none; stroke-linejoin:round; }}
.dot-port {{ fill:var(--port); stroke:var(--surface); stroke-width:2; }}
.dot-spy {{ fill:var(--spy); stroke:var(--surface); stroke-width:2; }}
.endlbl {{ font-size:11.5px; font-weight:600; }}
.endlbl-port {{ fill:var(--port); }} .endlbl-spy {{ fill:var(--spy); }}
.legend {{ display:flex; gap:16px; font-size:.82rem; color:var(--ink2); margin-top:6px; }}
.sw {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; }}
.sw-port {{ background:var(--port); }} .sw-spy {{ background:var(--spy); }}
.xh {{ stroke:var(--ink3); stroke-width:1; stroke-dasharray:3 3; }}
.tt {{ position:absolute; pointer-events:none; background:var(--surface); border:1px solid var(--line);
  border-radius:8px; padding:6px 10px; font-size:.8rem; box-shadow:0 4px 14px rgba(0,0,0,.12); white-space:nowrap; }}
footer {{ color:var(--ink3); font-size:.78rem; margin-top:26px; }}
</style></head><body><div class="wrap">
<header><h1>Paper Desk <span class="mode">{packet.get('mode','advisory')} · paper only</span></h1>
<p class="sub">Simulated portfolio · as of {packet['date']} · updates each trading day after US close{research_link}</p>{halted}</header>
<div class="kpis">{kpi_html}</div>
<div class="card"><h2>Equity curve — portfolio vs SPY</h2>{equity_chart(nav_hist, spy)}</div>
<div class="card"><h2>Positions &amp; orders</h2>{positions_block(packet, trades)}</div>
<div class="card"><h2>Three-pillar scorecard</h2><div class="tablewrap">
<table><thead><tr><th>Ticker</th><th>Trend</th><th>Momentum</th><th>Macro</th>
<th class="num">Total</th><th>Action</th></tr></thead>
<tbody>{scorecard_rows(packet.get('scores', {}), macro['score'])}</tbody></table></div>
<p class="note">Pillar bars are diverging from 0 (Trend/Momentum ±2, Macro ±2, Total ±6).
Macro regime: {html.escape(macro.get('regime',''))} ({macro['score']:+d}).</p></div>
<footer>robinhood-paper-desk · simulated money, not financial advice · generated by reports/make_dashboard.py</footer>
</div>
<script>
(function() {{
  var svg = document.querySelector('svg[data-eq]'); if (!svg) return;
  var d = JSON.parse(svg.getAttribute('data-eq'));
  var g = d.geom, W=g[0],H=g[1],PL=g[2],PR=g[3],PT=g[4],PB=g[5],lo=g[6],hi=g[7];
  var tt = svg.parentElement.querySelector('.tt'), xh = svg.querySelector('.xh');
  var n = d.dates.length; if (n < 2) return;
  svg.addEventListener('mousemove', function(e) {{
    var r = svg.getBoundingClientRect();
    var fx = (e.clientX - r.left) / r.width * W;
    var i = Math.round((fx - PL) / (W - PL - PR) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    var date = d.dates[i], x = PL + (W-PL-PR) * (i/(n-1));
    xh.setAttribute('x1', x); xh.setAttribute('x2', x);
    var parts = [date];
    if (d.port[date] !== undefined) parts.push('Portfolio ' + d.port[date].toFixed(2));
    if (d.spy[date] !== undefined) parts.push('SPY ' + d.spy[date].toFixed(2));
    tt.textContent = parts.join(' · '); tt.hidden = false;
    var px = x / W * r.width;
    tt.style.left = Math.min(px + 12, r.width - tt.offsetWidth - 4) + 'px';
    tt.style.top = '10px';
  }});
  svg.addEventListener('mouseleave', function() {{
    tt.hidden = true; xh.setAttribute('x1', -9); xh.setAttribute('x2', -9);
  }});
}})();
</script></body></html>"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(page)
    print(f"wrote {OUT} ({len(page):,} bytes)")


if __name__ == "__main__":
    build()
