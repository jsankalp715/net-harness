"""Convergence-time trends across CI runs: data model + self-contained HTML report.

Pure functions only (no network, no ``gh``) so they are unit-testable;
``scripts/collect_trends.py`` does the I/O and calls :func:`render_html`.

Chart form: small multiples, one single-series line chart per metric (a metric is
``<test-id>::<label>``, the same key the baseline uses), with the baseline value as
a dashed reference line. Every point has a hover tooltip; a data table follows.
"""

from __future__ import annotations

import html
import json
import math
import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    number: int
    sha: str
    branch: str
    event: str
    created_at: str  # ISO-8601, used for ordering


@dataclass
class MetricSeries:
    key: str
    points: list[tuple[RunInfo, float]] = field(default_factory=list)

    @property
    def values(self) -> list[float]:
        return [v for _, v in self.points]

    @property
    def title(self) -> str:
        """``test_failover.py::test_x[case]::failover`` -> ``case · failover``."""
        test, _, label = self.key.rpartition("::")
        case = test[test.find("[") + 1 : -1] if "[" in test else test.split("::")[-1]
        return f"{case} · {label}"

    @property
    def subtitle(self) -> str:
        test = self.key.rpartition("::")[0]
        return test.split("[")[0]


def build_series(
    runs: list[tuple[RunInfo, dict[str, float]]],
) -> dict[str, MetricSeries]:
    """Pivot per-run metrics into per-metric series, ordered oldest -> newest."""
    series: dict[str, MetricSeries] = {}
    for run, metrics in sorted(runs, key=lambda rm: (rm[0].created_at, rm[0].run_id)):
        for key, value in metrics.items():
            series.setdefault(key, MetricSeries(key)).points.append((run, value))
    return dict(sorted(series.items()))


def nice_ticks(max_value: float, count: int = 3) -> list[float]:
    """Clean y-axis ticks from 0 to >= ``max_value`` (e.g. 0, 0.5, 1.0)."""
    if max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    n = math.ceil(max_value / step - 1e-9)
    return [round(i * step, 6) for i in range(n + 1)]


def _fmt(seconds: float) -> str:
    return f"{seconds:.2f} s" if seconds < 10 else f"{seconds:.1f} s"


def _tick_label(value: float) -> str:
    return f"{value:g}"


# --------------------------------------------------------------------------- SVG
W, H = 320, 150
PAD_L, PAD_R, PAD_T, PAD_B = 36, 64, 12, 24


def render_chart(s: MetricSeries, baseline: float | None) -> str:
    vals = s.values
    top = max([*vals, baseline or 0.0]) * 1.1
    ticks = nice_ticks(top)
    y_max = ticks[-1]
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    n = len(vals)

    def x(i: int) -> float:
        return PAD_L + (plot_w / 2 if n == 1 else i * plot_w / (n - 1))

    def y(v: float) -> float:
        return PAD_T + plot_h * (1 - v / y_max)

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="{html.escape(s.title)}: {n} runs, latest {_fmt(vals[-1])}">'
    ]
    for t in ticks:  # hairline grid + muted tick labels
        parts.append(
            f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
            f'<text class="tick" x="{PAD_L - 6}" y="{y(t) + 3.5:.1f}" text-anchor="end">'
            f"{_tick_label(t)}</text>"
        )
    parts.append(
        f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
    )
    if baseline is not None:
        by = y(baseline)
        parts.append(
            f'<line class="ref" x1="{PAD_L}" x2="{W - PAD_R}" y1="{by:.1f}" y2="{by:.1f}"/>'
        )
    if n > 1:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline class="series" points="{pts}"/>')
    for i, (run, v) in enumerate(s.points):
        tip = {
            "metric": s.title,
            "value": _fmt(v),
            "run": f"#{run.number}",
            "ref": f"{run.branch} @ {run.sha[:7]}",
            "event": run.event,
            "date": run.created_at[:16].replace("T", " "),
        }
        last = " last" if i == n - 1 else ""
        parts.append(
            f'<circle class="dot{last}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4"/>'
            f'<circle class="hit" cx="{x(i):.1f}" cy="{y(v):.1f}" r="12" '
            f"data-tip='{html.escape(json.dumps(tip), quote=True)}'/>"
        )
    # direct label: latest value at the line end (text ink, not series color)
    parts.append(
        f'<text class="end-label" x="{x(n - 1) + 8:.1f}" y="{y(vals[-1]) - 6:.1f}">'
        f"{_fmt(vals[-1])}</text>"
    )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- page
_CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--series:#2a78d6;--ring:rgba(11,11,11,.10);color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#0d0d0d;
--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--series:#3987e5;--ring:rgba(255,255,255,.10);color-scheme:dark}}
:root[data-theme="dark"]{--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--series:#3987e5;--ring:rgba(255,255,255,.10);
color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 12px}
.sub{color:var(--ink-2);margin:0 0 20px}
.grid-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr));gap:12px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:12px}
.card h3{font-size:13px;margin:0;font-weight:600;overflow-wrap:anywhere}
.card p{font-size:12px;color:var(--muted);margin:0;overflow-wrap:anywhere}
.card .meta{color:var(--ink-2);margin:2px 0 6px;font-variant-numeric:tabular-nums}
.key{display:inline-block;width:16px;border-top:1px dashed var(--muted);vertical-align:middle;
margin:0 4px 0 2px}
svg{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--muted);font-size:10px;font-variant-numeric:tabular-nums}
.ref{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 3}
.series{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.dot{fill:var(--series);stroke:var(--surface);stroke-width:2}
.end-label{fill:var(--ink);font-size:11px;font-weight:600;font-variant-numeric:tabular-nums}
.hit{fill:transparent;cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);
border:1px solid var(--ring);border-radius:8px;padding:8px 10px;font-size:12px;
box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;max-width:280px;z-index:10}
#tip .v{font-weight:700;font-variant-numeric:tabular-nums}#tip .m{color:var(--ink-2)}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--ring);
border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
th{color:var(--ink-2);font-weight:600}
"""

_JS = """
const tip=document.getElementById('tip');
function line(text,cls){const el=document.createElement('div');if(cls)el.className=cls;
el.textContent=text;return el}
function show(e){const d=JSON.parse(e.target.dataset.tip);
tip.replaceChildren(line(d.metric,'m'),line(d.value,'v'),line(`run ${d.run} · ${d.event}`),
line(d.ref,'m'),line(`${d.date} UTC`,'m'));tip.style.display='block';move(e)}
function move(e){const r=tip.getBoundingClientRect();let x=e.clientX+14,y=e.clientY+14;
if(x+r.width>innerWidth-8)x=e.clientX-r.width-14;if(y+r.height>innerHeight-8)y=e.clientY-r.height-14;
tip.style.left=x+'px';tip.style.top=y+'px'}
document.querySelectorAll('.hit').forEach(h=>{h.addEventListener('mouseenter',show);
h.addEventListener('mousemove',move);h.addEventListener('mouseleave',()=>tip.style.display='none');
h.addEventListener('focus',e=>{show({target:e.target,clientX:e.target.getBoundingClientRect().x,
clientY:e.target.getBoundingClientRect().y})});h.addEventListener('blur',()=>tip.style.display='none');
h.setAttribute('tabindex','0')});
"""


def summarize(s: MetricSeries, baseline: float | None) -> dict[str, Any]:
    vals = s.values
    return {
        "runs": len(vals),
        "min": min(vals),
        "median": statistics.median(vals),
        "max": max(vals),
        "latest": vals[-1],
        "baseline": baseline,
        "delta": None if baseline is None else vals[-1] - baseline,
    }


def render_html(
    series: dict[str, MetricSeries], baseline: dict[str, float], generated_at: str
) -> str:
    runs = {run.run_id for s in series.values() for run, _ in s.points}
    cards: list[str] = []
    rows: list[str] = []
    for key, s in series.items():
        base = baseline.get(key)
        cards.append(
            f'<div class="card"><h3>{html.escape(s.title)}</h3>'
            f"<p>{html.escape(s.subtitle)}</p>"
            f'<p class="meta">latest {_fmt(s.values[-1])}'
            + ("" if base is None else f' · <span class="key"></span>baseline {_fmt(base)}')
            + f"</p>{render_chart(s, base)}</div>"
        )
        st = summarize(s, base)
        delta = "—" if st["delta"] is None else f"{st['delta']:+.2f} s"
        rows.append(
            f"<tr><td>{html.escape(s.title)}<br><small>{html.escape(s.subtitle)}</small></td>"
            f"<td>{st['runs']}</td><td>{_fmt(st['min'])}</td><td>{_fmt(st['median'])}</td>"
            f"<td>{_fmt(st['max'])}</td><td>{_fmt(st['latest'])}</td>"
            f"<td>{'—' if base is None else _fmt(base)}</td><td>{delta}</td></tr>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Convergence Trends</title><style>{_CSS}</style></head><body><main>"
        "<h1>Convergence trends</h1>"
        f'<p class="sub">{len(series)} metrics across {len(runs)} successful CI runs · '
        f"seconds, lower is better · dashed line = stored baseline · generated "
        f"{html.escape(generated_at)}</p>"
        f'<div class="grid-cards">{"".join(cards)}</div>'
        '<h2>Data table</h2><div class="table-wrap"><table><thead><tr><th>metric</th>'
        "<th>runs</th><th>min</th><th>median</th><th>max</th><th>latest</th>"
        "<th>baseline</th><th>latest &minus; baseline</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        f'</main><div id="tip" role="tooltip"></div><script>{_JS}</script></body></html>\n'
    )
