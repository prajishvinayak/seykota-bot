"""Self-contained live dashboard (output/dashboard_live.html). Chart.js from CDN."""
from __future__ import annotations
import os, json
from datetime import datetime, timezone


def build_live_dashboard(out_path: str, *, env: str, ccy: str, equity: float, cash: float,
                         available: float, peak: float, dd: float, day_pnl: float,
                         heat: float, positions: list, clusters: list,
                         actions: list, equity_history: list, journal_file: str,
                         halted: bool = False):
    def card(label, value, cls=""):
        return f'<div class="card"><div class="lbl">{label}</div><div class="val {cls}">{value}</div></div>'

    cards = "".join([
        card("Equity", f"{equity:,.0f} {ccy}"),
        card("Day P&L", f"{day_pnl*100:+.2f}%", "pos" if day_pnl >= 0 else "neg"),
        card("Drawdown", f"{dd*100:.2f}%", "neg" if dd > 0 else ""),
        card("Portfolio heat", f"{(heat/equity*100) if equity else 0:.2f}%"),
        card("Open positions", len(positions)),
        card("Peak equity", f"{peak:,.0f}"),
    ])

    pos_rows = "".join(
        f'<tr><td>{p["epic"]}</td><td>{"LONG" if p["direction"]>0 else "SHORT"}</td>'
        f'<td>{p["size"]:g}</td><td>{p["level"]}</td><td>{p.get("stopLevel")}</td>'
        f'<td class="{"pos" if p.get("upl",0)>=0 else "neg"}">{p.get("upl",0):,.2f}</td></tr>'
        for p in positions) or '<tr><td colspan="6">Flat — no open positions.</td></tr>'

    clus_rows = "".join(
        f'<tr><td>{c["name"]}</td><td>{c["count"]} / {c["cap"]}</td><td>{c["heat"]:.2f}%</td></tr>'
        for c in clusters)

    act_rows = "".join(f"<li>{a}</li>" for a in (actions or ["(no actions this cycle)"]))

    # recent journal (last 20)
    jrows = ""
    if os.path.exists(journal_file):
        try:
            with open(journal_file, encoding="utf-8") as f:
                lines = f.readlines()[-20:]
            for ln in reversed(lines):
                r = json.loads(ln)
                jrows += (f'<tr><td>{r.get("ts","")[:19]}</td><td>{r.get("event","")}</td>'
                          f'<td>{r.get("epic","")}</td><td>{r.get("side","")}</td>'
                          f'<td>{r.get("size","")}</td><td>{r.get("stop","")}</td></tr>')
        except Exception:
            pass
    jrows = jrows or '<tr><td colspan="6">No orders yet.</td></tr>'

    payload = json.dumps({
        "labels": [e["date"] for e in equity_history],
        "equity": [e["equity"] for e in equity_history],
        "dd": [round(e["drawdown"] * 100, 2) for e in equity_history],
    })
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    banner = '<div class="halt">⛔ KILL SWITCH ACTIVE — flattened & halted</div>' if halted else ""

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Seykota Bot — Live</title>
<meta http-equiv="refresh" content="300">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{{color-scheme:dark}}
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e8ec}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0}} .sub{{color:#9aa0aa;font-size:13px;margin:4px 0 18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}}
.card{{background:#171a21;border:1px solid #232733;border-radius:10px;padding:12px 14px}}
.lbl{{color:#9aa0aa;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
.val{{font-size:20px;font-weight:600;margin-top:4px}} .pos{{color:#4ade80}} .neg{{color:#f87171}}
.panel{{background:#171a21;border:1px solid #232733;border-radius:10px;padding:16px;margin-bottom:20px}}
h2{{font-size:14px;margin:0 0 12px;color:#cbd2dc}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #232733}} th{{color:#9aa0aa;font-weight:500}}
ul{{margin:0;padding-left:18px;font-size:13px;color:#cbd2dc}}
.halt{{background:#7f1d1d;color:#fff;padding:10px 14px;border-radius:8px;margin-bottom:16px;font-weight:600}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}} @media(max-width:820px){{.two{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>Seykota Bot — Live [{env.upper()}]</h1>
<div class="sub">Updated {updated} · auto-refreshes every 5 min · paper/demo</div>
{banner}
<div class="grid">{cards}</div>
<div class="panel"><h2>Equity</h2><canvas id="eq" height="80"></canvas></div>
<div class="two">
  <div class="panel"><h2>Open positions</h2><table>
    <tr><th>Epic</th><th>Side</th><th>Size</th><th>Entry</th><th>Stop</th><th>uP&amp;L</th></tr>
    {pos_rows}</table></div>
  <div class="panel"><h2>Cluster exposure</h2><table>
    <tr><th>Cluster</th><th>Positions</th><th>Heat</th></tr>{clus_rows}</table></div>
</div>
<div class="panel"><h2>This cycle's actions</h2><ul>{act_rows}</ul></div>
<div class="panel"><h2>Recent orders</h2><table>
  <tr><th>Time</th><th>Event</th><th>Epic</th><th>Side</th><th>Size</th><th>Stop</th></tr>{jrows}</table></div>
</div>
<script>
const d={payload};
if(d.labels.length){{new Chart(document.getElementById('eq'),{{type:'line',
 data:{{labels:d.labels,datasets:[{{label:'Equity',data:d.equity,borderColor:'#4ade80',
 backgroundColor:'rgba(74,222,128,.08)',fill:true,pointRadius:0,borderWidth:1.5}}]}},
 options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#9aa0aa',maxTicksLimit:10}},grid:{{color:'#1c2029'}}}},
 y:{{ticks:{{color:'#9aa0aa'}},grid:{{color:'#1c2029'}}}}}}}}}});}}
else{{document.getElementById('eq').replaceWith(Object.assign(document.createElement('div'),
 {{className:'sub',textContent:'Equity chart appears after the first daily cycle.'}}));}}
</script></body></html>"""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
