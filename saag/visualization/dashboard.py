"""
Dashboard Generator

Assembles a self-contained HTML dashboard from HTML fragments. Content is
grouped into tabs; each `add_*` method renders one widget and appends it to
the tab currently open.

Interactive widgets (sortable tables, Cytoscape networks, D3 matrices) emit
their JavaScript into `self.scripts`, which is flushed once at the end of
`generate()` so that scripts run after all markup exists.
"""
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from .palette import (
    BRAND_PURPLE,
    CRITICALITY_COLORS,
    CRITICALITY_BADGE_COLORS,
    DEFAULT_COLOR,
    HIERARCHY_COLORS,
    ROLE_BADGE_COLORS,
    TYPE_COLORS,
    TYPE_SHAPES,
    criticality_badge_css,
)


# Component type → layer mapping for Cytoscape compound nodes
COMPONENT_LAYER_MAP = {
    "Application": "layer-app",
    "Library":     "layer-app",
    "Topic":       "layer-mw",
    "Broker":      "layer-mw",
    "Node":        "layer-infra",
    "NetworkDevice": "layer-infra",
}

LAYER_COMPOUNDS = {
    "layer-app":   "Application Layer",
    "layer-mw":    "Middleware Layer",
    "layer-infra": "Infrastructure Layer",
}

# ─── HTML Template ────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
  <script src="https://unpkg.com/layout-base@2.0.1/layout-base.js"></script>
  <script src="https://unpkg.com/cose-base@2.2.0/cose-base.js"></script>
  <script src="https://unpkg.com/cytoscape-cose-bilkent@4.1.0/cytoscape-cose-bilkent.js"></script>
  <script src="https://d3js.org/d3.v7.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --primary:   #534AB7;
      --success:   #3B6D11;
      --warning:   #854F0B;
      --danger:    #A32D2D;
      --info:      #185FA5;
      --bg:        #f8f9fa;
      --surface:   #ffffff;
      --border:    #e5e7eb;
      --text:      #111827;
      --muted:     #6b7280;
      --nav-bg:    #1e293b;
      --radius-md: 8px;
      --radius-lg: 12px;
      --font-sans: 'Inter', -apple-system, system-ui, sans-serif;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: var(--font-sans);
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      line-height: 1.6;
    }}
    .db {{ padding: 1.5rem 2rem; max-width: 1400px; margin: 0 auto; }}
    /* ── Tabs ── */
    .tabs {{
      display: flex; gap: 4px; border-bottom: 0.5px solid var(--border);
      margin-bottom: 1.5rem; position: sticky; top: 0;
      background: var(--bg); z-index: 100; padding-top: 10px;
    }}
    .tab {{
      padding: 8px 16px; font-size: 13px; border: none; background: none;
      cursor: pointer; color: var(--muted); border-bottom: 2px solid transparent;
      margin-bottom: -1px; font-weight: 500; transition: all 0.2s;
    }}
    .tab:hover {{ color: var(--text); }}
    .tab.active {{ color: var(--primary); border-bottom-color: var(--primary); }}
    .sag-main {{
      padding-top: 20px;
    }}
    .sag-header {{
      padding: 24px 0 18px; margin-bottom: 0; background: transparent; color: var(--text);
    }}
    .sag-header-top {{
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 8px;
    }}
    .sag-header .meta {{
      font-size: 11px; opacity: 0.75; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
    }}
    .sag-header-badges {{
      display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
    }}
    .sag-header-main {{
      margin-top: 4px;
    }}
    .sag-header h1 {{
      font-size: 24px; font-weight: 700; letter-spacing: -0.5px; color: var(--text); margin-bottom: 4px;
    }}
    .sag-subtitle {{
      font-size: 13px; color: var(--muted); font-weight: 400;
    }}
    /* ── Sections ── */
    .section {{
      background: var(--surface); border: 0.5px solid var(--border);
      border-radius: var(--radius-lg); padding: 28px 32px;
      margin-bottom: 24px; scroll-margin-top: 72px;
    }}
    .section-header {{
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 24px; padding-bottom: 16px;
      border-bottom: 0.5px solid var(--border);
    }}
    .section-header h2 {{
      font-size: 16px; font-weight: 600; color: var(--text);
    }}
    .subsection {{ margin: 20px 0 12px; }}
    .subsection h3 {{
      font-size: 13px; font-weight: 500; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.04em;
    }}
    /* ── KPI cards ── */
    .grid4 {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px; margin-bottom: 1.5rem;
    }}
    .kpi {{ background: var(--surface); border-radius: var(--radius-md); padding: 16px 20px; border: 0.5px solid var(--border); }}
    .kpi-label {{ font-size: 11px; color: var(--muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }}
    .kpi-val {{ font-size: 26px; font-weight: 600; color: var(--primary); }}
    .kpi-val.danger {{ color: var(--danger); }}
    .kpi-val.warning {{ color: var(--warning); }}
    .kpi-val.success {{ color: var(--success); }}
    .kpi-val.info {{ color: var(--info); }}
    /* ── Charts ── */
    .grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(480px, 1fr)); gap: 1.5rem; margin-bottom: 1.5rem; }}
    .chart-card {{
      border: 0.5px solid var(--border); border-radius: var(--radius-md);
      padding: 20px;
    }}
    .chart-card h4 {{
      font-size: 12px; font-weight: 500; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 16px;
    }}
    .chart-container {{ position: relative; width: 100%; }}
    /* ── Tables ── */
    .table-container {{
      overflow-x: auto; border: 0.5px solid var(--border);
      border-radius: var(--radius-md); margin-bottom: 20px;
    }}
    .table-filter-row {{
      display: flex; gap: 8px; padding: 12px 16px;
      border-bottom: 0.5px solid var(--border); flex-wrap: wrap;
      align-items: center;
    }}
    .table-filter-row select, .table-filter-row input {{
      font-size: 12px; padding: 4px 10px;
      border: 0.5px solid var(--border); border-radius: var(--radius-md);
      background: var(--surface); color: var(--text); outline: none;
    }}
    .table-filter-row label {{
      font-size: 11px; color: var(--muted); font-weight: 500;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{
      text-align: left; padding: 8px 12px;
      font-size: 11px; font-weight: 500; color: var(--muted);
      border-bottom: 0.5px solid var(--border);
      background: #f9fafb; cursor: pointer; user-select: none;
      white-space: nowrap;
    }}
    th:hover {{ color: var(--text); }}
    th .sort-icon {{ margin-left: 4px; opacity: 0.4; }}
    td {{ padding: 7px 12px; border-bottom: 0.5px solid #f3f4f6; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #f9fafb; }}
    /* ── Badges ── */
    .badge {{
      display: inline-block; padding: 2px 7px; border-radius: 4px;
      font-size: 11px; font-weight: 500;
    }}
{badge_css}
    .badge-passed    {{ background: #EAF3DE; color: #27500A; }}
    .badge-failed    {{ background: #FCEBEB; color: #791F1F; }}
    .badge-spof      {{ background: #F4C0D1; color: #72243E; }}
    .badge-tag       {{ background: #EEEDFE; color: #3C3489; }}
    .badge-devops-sre {{ background: #E6F1FB; color: #0C447C; }}
    .badge-architect  {{ background: #EEEDFE; color: #534AB7; }}
    .badge-developer  {{ background: #E1F5EE; color: #0F6E56; }}
    /* ── Triage cards ── */
    .triage-card {{
      background: var(--surface); border: 0.5px solid var(--border);
      border-radius: var(--radius-md); padding: 14px 18px;
      margin-bottom: 10px; border-left: 4px solid var(--primary);
    }}
    .triage-header {{
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 6px; flex-wrap: wrap; gap: 8px;
    }}
    .triage-title {{
      font-size: 13px; font-weight: 600; color: var(--text);
    }}
    .triage-action {{
      font-size: 12px; color: var(--text); background: #f9fafb;
      padding: 8px 12px; border-radius: 6px; margin-top: 8px;
      border-left: 3px solid var(--success);
    }}
    .triage-elevated {{
      display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; align-items: center; font-size: 11px;
    }}
    /* ── Cascade risk panel ── */
    .cascade-stat {{
      background: #f3f4f6; border-radius: var(--radius-md);
      padding: 14px 16px;
    }}
    .cascade-stat-val {{
      font-size: 22px; font-weight: 600; line-height: 1; margin-bottom: 4px;
    }}
    .cascade-stat-label {{
      font-size: 11px; color: var(--muted); font-weight: 500;
      text-transform: uppercase; letter-spacing: 0.04em;
    }}
    .cascade-note {{
      font-size: 12px; color: var(--muted); padding: 8px 14px;
      border-left: 2px solid var(--border); margin-bottom: 20px;
      line-height: 1.5;
    }}
    /* ── Hierarchy tree ── */
    .hier-node {{
      display: flex; align-items: center; gap: 8px;
      padding: 7px 12px; border-radius: var(--radius-md);
      border: 0.5px solid var(--border); margin-bottom: 4px;
      font-size: 13px;
    }}
    .hier-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
    .hier-badge {{
      display: inline-block; padding: 1px 6px; border-radius: 4px;
      font-size: 11px; font-weight: 500; margin-left: 6px;
    }}
    .hier-q {{ margin-left: auto; font-size: 12px; font-weight: 500; }}
    /* ── RM segmented bar (component table) ── */
    .rm-bar {{
      display: flex; height: 8px; width: 100px;
      border-radius: 4px; overflow: hidden; background: #f3f4f6;
    }}
    .rm-seg {{ height: 100%; }}
    /* ── Per-dimension ρ bars ── */
    .dim-row {{
      display: flex; align-items: center; gap: 12px; margin-bottom: 10px;
    }}
    .dim-label {{ font-size: 12px; color: var(--muted); width: 150px; flex-shrink: 0; }}
    .dim-bar-outer {{
      flex: 1; height: 8px; background: #f3f4f6;
      border-radius: 4px; overflow: hidden;
    }}
    .dim-bar-inner {{ height: 100%; border-radius: 4px; transition: width .3s; }}
    .dim-val {{
      font-size: 12px; font-weight: 500; width: 48px;
      text-align: right; flex-shrink: 0;
    }}
    /* ── Metrics box ── */
    .metrics-box {{
      border: 0.5px solid var(--border); border-radius: var(--radius-md);
      padding: 16px 20px; margin-bottom: 20px;
    }}
    .metrics-box h4 {{
      font-size: 12px; font-weight: 500; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 14px;
    }}
    .metric-row {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 5px 0; border-bottom: 0.5px solid #f3f4f6;
      font-size: 12px;
    }}
    .metric-row:last-child {{ border-bottom: none; }}
    .metric-name {{ color: var(--muted); }}
    .metric-value {{ font-weight: 500; }}
    .metric-value.pass {{ color: var(--success); }}
    .metric-value.fail {{ color: var(--danger); }}
    /* ── Anti-patterns ── */
    .antipattern-card {{
      border-left: 3px solid var(--border); padding: 14px 18px;
      margin-bottom: 12px; border-radius: 0 var(--radius-md) var(--radius-md) 0;
      background: #fafafa;
    }}
    .antipattern-card.critical {{ border-left-color: var(--danger); }}
    .antipattern-card.high     {{ border-left-color: var(--warning); }}
    .antipattern-card h4 {{ font-size: 13px; font-weight: 500; margin-bottom: 4px; }}
    .antipattern-card p  {{ font-size: 12px; color: var(--muted); }}
    /* ── Explanation cards ── */
    .explanation-card {{
      border: 0.5px solid var(--border); border-radius: var(--radius-md);
      padding: 16px 20px; margin-bottom: 12px;
    }}
    .explanation-card h4 {{ font-size: 13px; font-weight: 500; margin-bottom: 6px; }}
    .explanation-card p  {{ font-size: 12px; color: var(--muted); }}
    /* ── Network graph ── */
    .cy-wrapper {{
      position: relative; margin-bottom: 20px;
    }}
    .cy-toolbar {{
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 12px; padding: 10px 14px; background: #f8fafc;
      border: 0.5px solid var(--border); border-bottom: none;
      border-radius: var(--radius-md) var(--radius-md) 0 0;
    }}
    .cy-toolbar-group {{
      display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted);
    }}
    .cy-select, .cy-input {{
      padding: 5px 9px; font-size: 12px; border: 0.5px solid var(--border);
      border-radius: 4px; background: #ffffff; color: var(--text); outline: none;
    }}
    .cy-select:focus, .cy-input:focus {{
      border-color: var(--primary);
    }}
    .cy-btn {{
      padding: 5px 10px; font-size: 12px; font-weight: 500;
      border: 0.5px solid var(--border); border-radius: 4px;
      background: #ffffff; cursor: pointer; color: var(--text);
      transition: background 0.15s;
    }}
    .cy-btn:hover {{ background: #f1f5f9; }}
    .cy-viewport-box {{
      position: relative; width: 100%;
      border: 0.5px solid var(--border);
      border-radius: 0 0 var(--radius-md) var(--radius-md);
      overflow: hidden; background: #fafafa;
    }}
    .cy-container {{
      width: 100%; height: 100%;
    }}
    .cy-inspector {{
      position: absolute; top: 12px; right: 12px; width: 330px;
      max-height: calc(100% - 24px); overflow-y: auto;
      background: #ffffff; border: 0.5px solid var(--border);
      border-radius: var(--radius-md); box-shadow: 0 4px 18px rgba(0,0,0,0.12);
      padding: 16px; z-index: 1000; font-size: 12px;
    }}
    .cy-inspector-header {{
      display: flex; justify-content: space-between; align-items: flex-start;
      margin-bottom: 12px; border-bottom: 0.5px solid #f1f5f9; padding-bottom: 8px;
    }}
    .cy-inspector-title {{
      font-size: 14px; font-weight: 600; color: var(--text); word-break: break-all;
    }}
    .cy-inspector-close {{
      cursor: pointer; font-size: 18px; line-height: 1; color: var(--muted);
      background: none; border: none; padding: 0 4px;
    }}
    .cy-inspector-close:hover {{ color: var(--text); }}
    .cy-metric-grid {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px;
    }}
    .cy-metric-card {{
      background: #f8fafc; padding: 8px 10px; border-radius: 6px; border: 0.5px solid #e2e8f0;
    }}
    .cy-metric-label {{
      font-size: 10px; color: var(--muted); text-transform: uppercase; font-weight: 500; margin-bottom: 2px;
    }}
    .cy-metric-value {{
      font-size: 14px; font-weight: 600; color: var(--text);
    }}
    .cy-legend {{
      display: flex; flex-wrap: wrap; gap: 18px; margin-top: 14px;
      font-size: 12px; color: var(--muted); align-items: center;
    }}
    .cy-legend-section {{
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    }}
    .cy-legend-item {{ display: flex; align-items: center; gap: 5px; }}
    .cy-swatch {{
      width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
    }}
    /* ── Footer ── */
    .sag-footer {{
      text-align: center; padding: 40px; color: var(--muted);
      font-size: 12px; border-top: 0.5px solid var(--border); margin-top: 40px;
    }}
    @media (max-width: 768px) {{
      .grid2 {{ grid-template-columns: 1fr; }}
      .sag-main   {{ padding: 16px 16px 40px; }}
    }}
  </style>
</head>
<body>
  <div class="sag-main">
    <div class="sag-header">
      <div class="sag-header-top">
        <div class="meta">Software-as-a-Graph (SaaG) &nbsp;&bull;&nbsp; Step 7 &nbsp;&bull;&nbsp; Architectural Decision Support</div>
        <div class="sag-header-badges">
          <span class="badge badge-tag">ISO/IEC 25010 &amp; 25019</span>
          <span class="badge badge-architect">Dual-Pathway: ISO-RM + HGT GNN</span>
          <span class="badge badge-devops-sre">Generated: {timestamp}</span>
        </div>
      </div>
      <div class="sag-header-main">
        <h1>{title}</h1>
        <p class="sag-subtitle">Architectural Quality Scoring, Blast-Radius Forecasting &amp; Stakeholder Remediation</p>
      </div>
    </div>
    <div class="db">
      {tabs_html}
      {content}
    </div>
  </div>
  <div class="sag-footer">
    <p>Software-as-a-Graph Methodology &nbsp;·&nbsp; Architectural Decision Support</p>
  </div>
  <script>
  function switchTab(id, btn) {{
    document.querySelectorAll('[id^=tab-]').forEach(el => el.style.display = 'none');
    var target = document.getElementById('tab-' + id);
    if (target) target.style.display = '';
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    if (btn) btn.classList.add('active');
    // Force chart resize if hidden tab had charts
    window.dispatchEvent(new Event('resize'));
    // Force Cytoscape viewport recalculation and auto-fit on tab display
    if (window.sagCyInstances) {{
      Object.keys(window.sagCyInstances).forEach(function(k) {{
        var cy = window.sagCyInstances[k];
        if (cy) {{
          cy.resize();
          cy.fit(25);
        }}
      }});
    }}
  }}
  </script>
  {scripts}
</body>
</html>
"""


class DashboardGenerator:
    """
    Assembles responsive HTML dashboards with interactive charts,
    sortable/filterable tables, Cytoscape.js network graphs, D3 dependency
    matrices, cascade risk panels, and MIL-STD-498 hierarchy trees.
    """

    def __init__(self, title: str):
        self.title = title
        self.scripts: List[str] = []
        self.tabs: List[Dict[str, Any]] = []
        self._current_tab: Optional[Dict[str, Any]] = None
        self._table_counter = 0

    def _emit(self, html: str) -> None:
        """
        Append rendered HTML to the open tab.

        Widgets added before any `add_tab()` call land in a default tab so
        that callers are never required to open one explicitly.
        """
        if self._current_tab is None:
            if not self.tabs:
                self.add_tab("Main")
            self._current_tab = self.tabs[0]
        self._current_tab["sections"].append(html)

    def add_tab(self, name: str, anchor_id: str = "") -> None:
        """Start a new tab group."""
        tid = anchor_id or name.lower().replace(" ", "-")
        new_tab = {"id": tid, "title": name, "sections": []}
        self.tabs.append(new_tab)
        self._current_tab = new_tab

    def end_tab(self) -> None:
        """Close the current tab."""
        self._current_tab = None

    def start_section(self, title: str, anchor_id: str = "") -> None:
        sid = anchor_id or title.lower().replace(" ", "-")
        self._emit(
            f'<div class="section" id="{sid}">'
            f'<div class="section-header"><h2>{title}</h2></div>'
        )

    def end_section(self) -> None:
        self._emit("</div>")

    def add_subsection(self, title: str) -> None:
        self._emit(f'<div class="subsection"><h3>{title}</h3></div>')

    # ── KPI Cards ──────────────────────────────────────────────────────────

    def add_kpis(self, kpis: Dict[str, Any], styles: Dict[str, str] = None) -> None:
        styles = styles or {}
        cards = "".join(
            f'<div class="kpi">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-val {styles.get(label, "info")}">{value}</div>'
            f'</div>'
            for label, value in kpis.items()
        )
        self._emit(f'<div class="grid4">{cards}</div>')

    # ── Chart Grid ─────────────────────────────────────────────────────────

    def add_charts(self, charts: List[Any], grid_class: str = "grid2") -> None:
        valid = [c for c in charts if c is not None]
        if not valid:
            return
        cards = "".join(f'<div class="chart-card">{c}</div>' for c in valid)
        self._emit(f'<div class="{grid_class}">{cards}</div>')

    # ── Static Table ───────────────────────────────────────────────────────

    def add_table(
        self, headers: List[str], rows: List[List[Any]], title: str = ""
    ) -> None:
        """Static table — use add_interactive_table for sort/filter."""
        html = ['<div class="table-container">']
        if title:
            html.append(f'<div style="padding:12px 16px;font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase">{title}</div>')
        html.append("<table><thead><tr>")
        for h in headers:
            html.append(f"<th>{h}</th>")
        html.append("</tr></thead><tbody>")
        for row in rows:
            html.append("<tr>")
            for cell in row:
                cell_str = str(cell)
                lu = cell_str.upper()
                if lu in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"):
                    cell_html = f'<span class="badge badge-{lu.lower()}">{cell_str}</span>'
                elif lu in ("PASSED", "PASS", "✓"):
                    cell_html = '<span class="badge badge-passed">PASSED</span>'
                elif lu in ("FAILED", "FAIL", "✗"):
                    cell_html = '<span class="badge badge-failed">FAILED</span>'
                else:
                    cell_html = cell_str
                html.append(f"<td>{cell_html}</td>")
            html.append("</tr>")
        html.append("</tbody></table></div>")
        self._emit("".join(html))

    # ── Interactive Table (sort + filter) ──────────────────────────────────

    def add_interactive_table(
        self,
        headers: List[str],
        rows: List[List[Any]],
        title: str = "",
        type_col: Optional[int] = None,
        level_col: Optional[int] = None,
    ) -> None:
        """
        Table with client-side column-header sort and optional filter
        dropdowns for type and criticality level columns.
        """
        self._table_counter += 1
        tid = f"itbl_{self._table_counter}"

        type_vals = sorted({str(rows[i][type_col]) for i in range(len(rows))}) if type_col is not None else []
        level_vals = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"]

        html = ['<div class="table-container">']
        html.append('<div class="table-filter-row">')
        if title:
            html.append(f'<span style="font-size:12px;font-weight:500;color:#374151;margin-right:8px">{title}</span>')
        if type_col is not None:
            html.append(
                f'<select id="{tid}_tf" onchange="sagFilterTable(\'{tid}\')">'
                f'<option value="">All types</option>'
                + "".join(f'<option value="{v}">{v}</option>' for v in type_vals)
                + "</select>"
            )
        if level_col is not None:
            html.append(
                f'<select id="{tid}_lf" onchange="sagFilterTable(\'{tid}\')">'
                f'<option value="">All levels</option>'
                + "".join(f'<option value="{v}">{v}</option>' for v in level_vals)
                + "</select>"
            )
        html.append(
            f'<input type="text" id="{tid}_search" placeholder="Search..." '
            f'oninput="sagFilterTable(\'{tid}\')" style="margin-left:auto;width:160px">'
        )
        html.append("</div>")

        html.append(f'<table id="{tid}"><thead><tr>')
        for i, h in enumerate(headers):
            html.append(
                f'<th onclick="sagSortTable(\'{tid}\',{i})">'
                f'{h} <span class="sort-icon">↕</span></th>'
            )
        html.append("</tr></thead><tbody>")
        for row in rows:
            html.append("<tr>")
            for cell in row:
                html.append(f"<td>{cell}</td>")
            html.append("</tr>")
        html.append(f"</tbody></table></div>")

        tf_idx = type_col if type_col is not None else -1
        lf_idx = level_col if level_col is not None else -1

        script = f"""
        <script>
        (function() {{
          window.sagTblMeta = window.sagTblMeta || {{}};
          window.sagTblMeta['{tid}'] = {{ typeCol: {tf_idx}, levelCol: {lf_idx} }};

          window.sagSortTable = window.sagSortTable || function(id, col) {{
            var tbl = document.getElementById(id);
            if (!tbl) return;
            var rows = Array.from(tbl.tBodies[0].rows);
            var asc = tbl.dataset.sortCol == col && tbl.dataset.sortDir == 'asc';
            rows.sort(function(a, b) {{
              var av = a.cells[col].textContent.trim();
              var bv = b.cells[col].textContent.trim();
              var an = parseFloat(av), bn = parseFloat(bv);
              if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
              return asc ? av.localeCompare(bv) : bv.localeCompare(av);
            }});
            rows.forEach(function(r) {{ tbl.tBodies[0].appendChild(r); }});
            tbl.dataset.sortCol = col;
            tbl.dataset.sortDir = asc ? 'desc' : 'asc';
            tbl.querySelectorAll('th .sort-icon').forEach(function(s,i) {{
              s.textContent = (i === col) ? (asc ? ' ↑' : ' ↓') : ' ↕';
            }});
          }};

          function cellText(cell) {{
            return (cell ? cell.textContent : '').trim();
          }}

          window.sagFilterTable = window.sagFilterTable || function(id) {{
            var tbl = document.getElementById(id);
            if (!tbl) return;
            var meta = (window.sagTblMeta || {{}})[id] || {{}};
            var search = (document.getElementById(id + '_search') || {{}}).value || '';
            var tf = (document.getElementById(id + '_tf') || {{}}).value || '';
            var lf = (document.getElementById(id + '_lf') || {{}}).value || '';
            search = search.toLowerCase();
            Array.from(tbl.tBodies[0].rows).forEach(function(row) {{
              var text = row.textContent.toLowerCase();
              var typeOk = !tf || (meta.typeCol >= 0 && cellText(row.cells[meta.typeCol]) === tf);
              var levelOk = !lf || (meta.levelCol >= 0 && cellText(row.cells[meta.levelCol]) === lf);
              var searchOk = !search || text.indexOf(search) >= 0;
              row.style.display = (typeOk && levelOk && searchOk) ? '' : 'none';
            }});
          }};
        }})();
        </script>
        """
        self._emit("".join(html))
        self.scripts.append(script)

    # ── Cascade Risk Panel ─────────────────────────────────────────────────

    def add_cascade_risk_panel(
        self,
        cascade_chart_html: Optional[str],
        qos_gini: float = 0.0,
        wilcoxon_p: float = 1.0,
        delta_rho: float = 0.0,
        note: str = "",
    ) -> None:
        """
        Cascade risk section: stat cards (Gini, p-value, Δρ) + dual-bar chart.
        """
        html = []
        if note:
            html.append(f'<div class="cascade-note">{note}</div>')

        html.append('<div class="grid4">')
        stats = [
            ("QoS Gini coefficient",     f"{qos_gini:.3f}",    "info"),
            ("Wilcoxon p-value",          f"{wilcoxon_p:.4f}",  "success" if wilcoxon_p < 0.05 else "warning"),
            ("Δρ (enrichment)", f"+{delta_rho:.3f}",  "success" if delta_rho > 0.03 else "warning"),
        ]
        for label, val, style in stats:
            html.append(
                f'<div class="kpi">'
                f'<div class="kpi-label">{label}</div>'
                f'<div class="kpi-val {style}">{val}</div>'
                f'</div>'
            )
        html.append("</div>")

        if cascade_chart_html:
            html.append(f'<div class="chart-card"><h4>Cascade risk distribution</h4>{cascade_chart_html}</div>')

        self._emit("".join(html))

    def add_top5_bars(self, components: List[Any]) -> None:
        """Horizontal Q(v) bar list for the five highest-scoring components."""
        html = ['<div class="chart-card"><h4>Top 5 components by Q(v)</h4>']
        for c in components[:5]:
            q = getattr(c, 'overall', 0.0)
            level = getattr(c, 'level', 'MINIMAL')
            spof = getattr(c, 'spof', False)
            name = getattr(c, 'name', getattr(c, 'id', 'Unknown'))
            html.append(f"""
                <div style="margin-bottom:12px">
                  <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
                    <span style="font-weight:600">{name} <span class="badge badge-{level.lower()}">{level}</span>{ '<span class="badge badge-spof" style="margin-left:4px">SPOF</span>' if spof else '' }</span>
                    <span style="font-weight:600">{q:.3f}</span>
                  </div>
                  <div style="height:8px;background:var(--bg);border-radius:4px;overflow:hidden">
                    <div style="height:100%;width:{q*100:.1f}%;background:{CRITICALITY_COLORS.get(level, DEFAULT_COLOR)};border-radius:4px;transition:width .3s"></div>
                  </div>
                </div>""")
        html.append('</div>')
        self._emit("".join(html))

    # ── Per-Dimension ρ Panel ──────────────────────────────────────────────

    def add_dim_rho_panel(
        self,
        dim_rho_html: str,
        seed_chart_html: Optional[str] = None,
    ) -> None:
        """
        Two-column panel: per-dim ρ progress bars (left) + multi-seed line (right).
        """
        seed_card = (
            f'<div class="chart-card"><h4>Multi-seed stability</h4>{seed_chart_html}</div>'
            if seed_chart_html else ""
        )
        self._emit(
            f'<div class="grid2">'
            f'<div class="chart-card"><h4>Per-dimension Spearman ρ</h4>{dim_rho_html}</div>'
            f'{seed_card}'
            f'</div>'
        )

    # ── MIL-STD-498 Hierarchy Tree ──────────────────────────────────────────

    def add_hierarchy_tree(self, tree: Dict[str, Any]) -> None:
        """Render the CSS→CSCI→CSC→CSU tree as an indented vertical list."""

        def _render(node: Dict[str, Any], depth: int) -> List[str]:
            parts = []
            level = node.get("level", "CSU")
            bg, accent = HIERARCHY_COLORS.get(level, HIERARCHY_COLORS["CSU"])
            q = node.get("q")
            cbci = node.get("cbci")
            indent = f"margin-left:{depth * 24}px"
            q_html = (
                f'<span class="hier-q" style="color:{accent}">'
                f'Q = {q:.3f}</span>'
            ) if q is not None else ""
            cbci_html = (
                f'<span class="hier-badge" '
                f'style="background:{bg};color:{accent}">CBCI: {cbci:.2f}</span>'
            ) if cbci is not None else ""
            fw = "500" if depth == 0 else "400"
            parts.append(
                f'<div class="hier-node" style="{indent};background:{bg}22">'
                f'  <div class="hier-dot" style="background:{accent}"></div>'
                f'  <span style="font-weight:{fw}">'
                f'    {node.get("label", node["id"])}'
                f'  </span>'
                f'  {cbci_html}'
                f'  {q_html}'
                f'</div>'
            )
            for child in node.get("children", []):
                parts.extend(_render(child, depth + 1))
            return parts

        self._emit("".join(_render(tree, 0)))

    # ── Metrics Box ────────────────────────────────────────────────────────

    def add_metrics_box(
        self,
        metrics: Dict[str, Any],
        title: str = "Metrics",
        highlights: Dict[str, bool] = None,
    ) -> None:
        highlights = highlights or {}
        html = [f'<div class="metrics-box"><h4>{title}</h4>']
        for name, value in metrics.items():
            val_class = " pass" if highlights.get(name) else (" fail" if name in highlights else "")
            val_str = f"{value:.4f}" if isinstance(value, float) else str(value)
            html.append(
                f'<div class="metric-row">'
                f'<span class="metric-name">{name}</span>'
                f'<span class="metric-value{val_class}">{val_str}</span>'
                f'</div>'
            )
        html.append("</div>")
        self._emit("".join(html))

    # ── Explanation Cards ──────────────────────────────────────────────────

    def add_explanation_section(self, explanation: Dict[str, Any]) -> None:
        if not explanation:
            return
        html = []
        for comp_id, info in explanation.items():
            if isinstance(info, dict):
                title = info.get("title", comp_id)
                text = info.get("explanation", "")
                level = info.get("level", "MINIMAL").lower()
                html.append(
                    f'<div class="explanation-card">'
                    f'<h4>{title} <span class="badge badge-{level}">'
                    f'{level.upper()}</span></h4>'
                    f'<p>{text}</p>'
                    f'</div>'
                )
        if html:
            self._emit("".join(html))

    # ── Triage Bridge Panel ────────────────────────────────────────────────

    def add_triage_panel(
        self,
        triage_entries: List[Dict[str, Any]],
        ranking_source: str = "gnn",
    ) -> None:
        """Render the Triage Bridge Top-K shortlist with root-cause patterns and stakeholder roles."""
        if not triage_entries:
            return

        source_label = "Pathway B (GNN Blast Radius)" if ranking_source.lower() == "gnn" else "Pathway A (RM Quality)"
        html = [
            f'<div class="triage-panel" style="margin-bottom:24px;">',
            f'<div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:4px">Triage Bridge — Actionable Stakeholder Remediation</div>',
            f'<div style="font-size:12px;color:var(--muted);margin-bottom:14px">Top-{len(triage_entries)} critical assets ranked via {source_label} and scoped to ISO-RM root-cause diagnosis.</div>',
        ]

        for entry in triage_entries:
            rank = entry.get("rank", 1)
            cid = entry.get("component_id", "")
            ctype = entry.get("component_type", "")
            score = entry.get("ranking_score", 0.0)
            pattern = entry.get("pattern", "UNSPECIFIED")
            level = str(entry.get("level", "MINIMAL")).lower()
            roles = entry.get("roles", [])
            elevated = entry.get("elevated_dimensions", [])
            action = entry.get("priority_action", "")

            role_badges = []
            for r in roles:
                r_slug = r.lower().replace(" / ", "-").replace(" ", "-")
                if "devops" in r_slug or "sre" in r_slug:
                    badge_cls = "badge-devops-sre"
                elif "architect" in r_slug:
                    badge_cls = "badge-architect"
                else:
                    badge_cls = "badge-developer"
                role_badges.append(f'<span class="badge {badge_cls}">{r}</span>')

            elevated_tags = []
            for ed in elevated:
                d_name = ed.get("dimension", "")
                d_val = ed.get("value", 0.0)
                elevated_tags.append(f'<span class="badge badge-tag" style="background:#FCEBEB;color:#791F1F">{d_name}: {d_val:.2f}</span>')

            elevated_block = f'<div class="triage-elevated" style="margin-bottom:6px"><strong>Elevated:</strong> {" ".join(elevated_tags)}</div>' if elevated_tags else ""
            action_block = f'<div class="triage-action"><strong>Priority Action:</strong> {action}</div>' if action else ""

            html.append(f"""
            <div class="triage-card">
              <div class="triage-header">
                <div class="triage-title">
                  <span style="color:var(--muted);margin-right:6px">#{rank}</span>
                  <strong>{cid}</strong>
                  <span style="font-size:11px;color:var(--muted);font-weight:normal;margin-left:4px">({ctype})</span>
                  <span class="badge badge-{level}" style="margin-left:6px">{level.upper()}</span>
                </div>
                <div style="display:flex;gap:4px;align-items:center;">
                  <span style="font-size:12px;font-weight:600;color:var(--primary);margin-right:6px">Score: {score:.3f}</span>
                  {''.join(role_badges)}
                </div>
              </div>
              <div style="font-size:12px;color:var(--text);margin-bottom:4px;">
                <strong>Root Cause:</strong> <span style="font-family:monospace;color:var(--primary)">{pattern}</span>
              </div>
              {elevated_block}
              {action_block}
            </div>
            """)

        html.append("</div>")
        self._emit("".join(html))

    # ── Anti-Pattern Catalog ───────────────────────────────────────────────

    def add_antipattern_catalog(self, patterns: List[Dict[str, Any]]) -> None:
        if not patterns:
            content = '<p style="color:var(--muted);font-size:13px">No anti-patterns detected.</p>'
        else:
            html = []
            for p in patterns[:50]:
                sev = p.get("severity", "medium").lower()
                name = p.get("name", "Unknown pattern")
                desc = p.get("description", "")
                components = p.get("components", []) or p.get("component_ids", [])
                comp_badges = "".join(
                    f'<span class="badge badge-tag">{c}</span> ' for c in components[:5]
                )
                html.append(
                    f'<div class="antipattern-card {sev}">'
                    f'<h4>{name} <span class="badge badge-{sev}">{sev.upper()}</span></h4>'
                    f'<p>{desc}</p>'
                    + (f'<div style="margin-top:6px">{comp_badges}</div>' if comp_badges else "")
                    + f'</div>'
                )
            if len(patterns) > 50:
                html.append(f'<p style="font-size:12px;color:var(--muted);margin-top:8px;">...and {len(patterns) - 50} more anti-pattern instances detected.</p>')
            content = "".join(html)

        self._emit(content)

    # ── Cytoscape.js Network Graph ─────────────────────────────────────────

    def add_cytoscape_network(
        self,
        graph_id: str,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        title: str = "Interactive Network Topology",
        use_compound_nodes: bool = True,
    ) -> None:
        elements = []
        if use_compound_nodes:
            for layer_id, label in LAYER_COMPOUNDS.items():
                elements.append({
                    "data": {"id": layer_id, "label": label, "isCompound": True},
                    "classes": "compound",
                })

        for node in nodes:
            node_type = node.get("type", "Application")
            parent = COMPONENT_LAYER_MAP.get(node_type) if use_compound_nodes else None
            level = node.get("level", "MINIMAL")
            shape = TYPE_SHAPES.get(node_type, "round-rectangle")
            is_spof = bool(node.get("spof", False))
            border_width = 3 if is_spof else 1.5
            border_color = "#791F1F" if is_spof else "#ffffff"
            border_style = "double" if is_spof else "solid"

            nd: Dict[str, Any] = {
                "data": {
                    "id": node["id"],
                    "name": node.get("name", node.get("label", node["id"])),
                    "label": node.get("label", node["id"]),
                    "nodeType": node_type,
                    "level": level,
                    "score": node.get("score", node.get("value", 10)),
                    "dim_score": node.get("value", 10),
                    "reliability": node.get("reliability", 0.0),
                    "maintainability": node.get("maintainability", 0.0),
                    "fault_tolerance": node.get("fault_tolerance", 0.0),
                    "availability": node.get("availability", 0.0),
                    "gnn_score": node.get("gnn_score", 0.0),
                    "impact": node.get("impact", 0.0),
                    "cascade_depth": node.get("cascade_depth", 0),
                    "mpci": node.get("mpci", 0.0),
                    "foc": node.get("foc", 0.0),
                    "spof": is_spof,
                    "anti_patterns": node.get("anti_patterns", []),
                    "triage_rank": node.get("triage_rank"),
                    "triage_roles": node.get("triage_roles", []),
                    "triage_pattern": node.get("triage_pattern", ""),
                    "triage_priority_action": node.get("triage_priority_action", ""),
                    "title": node.get("title", ""),
                    "color": CRITICALITY_COLORS.get(level, DEFAULT_COLOR),
                    "shape": shape,
                    "borderWidth": border_width,
                    "borderColor": border_color,
                    "borderStyle": border_style,
                },
                "classes": f"node-{node_type.lower()} level-{level.lower()}" + (" node-spof" if is_spof else ""),
            }
            if parent:
                nd["data"]["parent"] = parent
            elements.append(nd)

        for edge in edges:
            weight = edge.get("weight", 1.0)
            if weight != weight:  # NaN
                weight = 1.0

            # Log-scale the frequency weight so heavy flows stay legible.
            thickness = 1.5 + 2.5 * math.log10(1.0 + max(0.0, weight))

            elements.append({
                "data": {
                    "id": f"e_{edge['source']}_{edge['target']}",
                    "source": edge["source"],
                    "target": edge["target"],
                    "weight": weight,
                    "thickness": thickness,
                    "depType": edge.get("dependency_type", "DEPENDS_ON"),
                },
            })

        cy_style = [
            {
                "selector": "node",
                "style": {
                    "label": "data(label)",
                    "font-size": "10px",
                    "font-weight": "500",
                    "shape": "data(shape)",
                    "background-color": "data(color)",
                    "width": "data(dim_score)",
                    "height": "data(dim_score)",
                    "border-width": "data(borderWidth)",
                    "border-color": "data(borderColor)",
                    "border-style": "data(borderStyle)",
                    "color": "#ffffff",
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "wrap",
                    "text-max-width": "80px",
                    "text-outline-color": "data(color)",
                    "text-outline-width": "1px",
                    "transition-property": "border-width, border-color, opacity",
                    "transition-duration": "0.2s",
                },
            },
            {
                "selector": ".compound",
                "style": {
                    "label": "data(label)",
                    "font-size": "12px",
                    "font-weight": "600",
                    "background-opacity": 0.04,
                    "background-color": BRAND_PURPLE,
                    "border-color": BRAND_PURPLE,
                    "border-width": "1px",
                    "border-style": "dashed",
                    "color": BRAND_PURPLE,
                    "text-valign": "top",
                    "text-halign": "center",
                    "padding": "16px",
                },
            },
            {
                "selector": "edge",
                "style": {
                    "width": "data(thickness)",
                    "line-color": "#cbd5e1",
                    "target-arrow-color": "#94a3b8",
                    "target-arrow-shape": "triangle",
                    "curve-style": "bezier",
                    "arrow-scale": 1.0,
                    "opacity": 0.7,
                    "transition-property": "line-color, target-arrow-color, width, opacity",
                    "transition-duration": "0.2s",
                },
            },
            {
                "selector": "node.highlighted",
                "style": {
                    "border-width": "4px",
                    "border-color": "#1e293b",
                    "border-style": "solid",
                    "z-index": 999,
                    "opacity": 1.0,
                },
            },
            {
                "selector": "node.neighbor",
                "style": {
                    "border-width": "3px",
                    "border-color": BRAND_PURPLE,
                    "border-style": "solid",
                    "z-index": 900,
                    "opacity": 1.0,
                },
            },
            {
                "selector": "edge.edge-highlighted",
                "style": {
                    "line-color": BRAND_PURPLE,
                    "target-arrow-color": BRAND_PURPLE,
                    "width": "data(thickness)",
                    "opacity": 1.0,
                    "z-index": 950,
                },
            },
            {
                "selector": ".dimmed",
                "style": {
                    "opacity": 0.15,
                },
            },
        ]

        elem_json = json.dumps(elements)
        style_json = json.dumps(cy_style)
        height = min(680, max(420, len(nodes) * 15))

        # Criticality swatches
        crit_legend = "".join(
            f'<div class="cy-legend-item">'
            f'<div class="cy-swatch" style="background:{CRITICALITY_COLORS[lv]}"></div>'
            f'{lv.capitalize()}'
            f'</div>'
            for lv in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL")
        )
        # Type shape swatches
        shape_svg = {
            "round-rectangle": '<span style="display:inline-block;width:12px;height:10px;background:#64748b;border-radius:2px;"></span>',
            "diamond": '<span style="display:inline-block;width:8px;height:8px;background:#64748b;transform:rotate(45deg);margin:0 2px;"></span>',
            "ellipse": '<span style="display:inline-block;width:10px;height:10px;background:#64748b;border-radius:50%;"></span>',
            "hexagon": '<span style="display:inline-block;width:10px;height:10px;background:#64748b;clip-path:polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);"></span>',
            "barrel": '<span style="display:inline-block;width:10px;height:11px;background:#64748b;border-radius:3px;"></span>',
        }
        type_legend = "".join(
            f'<div class="cy-legend-item">'
            f'{shape_svg.get(TYPE_SHAPES.get(t, "round-rectangle"), "")}'
            f'{t}'
            f'</div>'
            for t in ("Application", "Broker", "Topic", "Library", "Node")
        )
        spof_legend = '<div class="cy-legend-item"><span style="display:inline-block;width:12px;height:12px;border:2px double #791F1F;background:#FCEBEB;border-radius:3px;"></span>SPOF (Articulation Point)</div>'

        content = f"""
        <div class="cy-wrapper">
          <div class="cy-toolbar">
            <div class="cy-toolbar-group">
              <label for="{graph_id}-layout"><strong>Layout:</strong></label>
              <select id="{graph_id}-layout" class="cy-select" onchange="window.sagChangeLayout('{graph_id}', this.value)">
                <option value="cose-bilkent" selected>CoSE-Bilkent (Compound)</option>
                <option value="concentric">Concentric (Criticality)</option>
                <option value="breadthfirst">Breadthfirst (Flow)</option>
                <option value="circle">Circle</option>
                <option value="grid">Grid</option>
              </select>
            </div>
            <div class="cy-toolbar-group">
              <label for="{graph_id}-search"><strong>Find:</strong></label>
              <input type="text" id="{graph_id}-search" class="cy-input" placeholder="Search node or ID..." oninput="window.sagSearchNodes('{graph_id}', this.value)">
            </div>
            <div class="cy-toolbar-group">
              <button class="cy-btn" onclick="window.sagZoom('{graph_id}', 1.25)" title="Zoom In">+</button>
              <button class="cy-btn" onclick="window.sagZoom('{graph_id}', 0.8)" title="Zoom Out">&minus;</button>
              <button class="cy-btn" onclick="window.sagFit('{graph_id}')" title="Fit to View">Fit</button>
              <button class="cy-btn" onclick="window.sagReset('{graph_id}')" title="Reset Selection">Reset</button>
            </div>
          </div>
          <div class="cy-viewport-box">
            <div id="{graph_id}" class="cy-container" style="height:{height}px"></div>
            <div id="{graph_id}-inspector" class="cy-inspector" style="display:none"></div>
          </div>
          <div class="cy-legend">
            <div class="cy-legend-section"><strong>Criticality:</strong> {crit_legend}</div>
            <div class="cy-legend-section"><strong>Types:</strong> {type_legend}</div>
            <div class="cy-legend-section">{spof_legend}</div>
          </div>
        </div>
        """

        self._emit(content)

        script = f"""
        <script>
        (function() {{
          if (typeof cytoscape === 'undefined') return;
          var container = document.getElementById('{graph_id}');
          if (!container) return;

          var cy = cytoscape({{
            container: container,
            elements: {elem_json},
            style: {style_json},
            layout: {{
              name: 'cose-bilkent',
              animate: false,
              nodeRepulsion: 6000,
              idealEdgeLength: 80,
              nodeDimensionsIncludeLabels: true,
            }},
          }});

          window.sagCyInstances = window.sagCyInstances || {{}};
          window.sagCyInstances['{graph_id}'] = cy;

          var inspector = document.getElementById('{graph_id}-inspector');

          function resetHighlight() {{
            cy.elements().removeClass('highlighted neighbor dimmed edge-highlighted');
            if (inspector) inspector.style.display = 'none';
          }}

          function highlightNode(node) {{
            cy.elements().removeClass('highlighted neighbor edge-highlighted').addClass('dimmed');

            node.removeClass('dimmed').addClass('highlighted');
            var connectedEdges = node.connectedEdges();
            connectedEdges.removeClass('dimmed').addClass('edge-highlighted');

            var neighborhood = node.neighborhood('node');
            neighborhood.removeClass('dimmed').addClass('neighbor');

            node.parents().removeClass('dimmed');
            neighborhood.parents().removeClass('dimmed');

            showInspector(node.data(), connectedEdges, neighborhood);
          }}

          function showInspector(d, edges, neighbors) {{
            if (!inspector) return;

            var inEdges = 0, outEdges = 0;
            if (edges) {{
              edges.forEach(function(e) {{
                if (e.data('target') === d.id) inEdges++;
                if (e.data('source') === d.id) outEdges++;
              }});
            }}

            var html = [];
            html.push('<div class="cy-inspector-header">');
            html.push('  <div>');
            html.push('    <div class="cy-inspector-title">' + (d.name || d.id) + '</div>');
            html.push('    <div style="font-size:11px;color:var(--muted);margin-top:2px;">' + d.id + ' &middot; ' + d.nodeType + '</div>');
            html.push('  </div>');
            html.push('  <div style="display:flex;align-items:center;gap:6px;">');
            html.push('    <span class="badge badge-' + d.level.toLowerCase() + '">' + d.level + '</span>');
            html.push('    <button class="cy-inspector-close" onclick="window.sagCloseInspector(\\'{graph_id}\\')">&times;</button>');
            html.push('  </div>');
            html.push('</div>');

            html.push('<div class="cy-metric-grid">');
            html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Criticality Q(v)</div><div class="cy-metric-value" style="color:var(--primary);">' + Number(d.score || 0).toFixed(3) + '</div></div>');
            if (d.gnn_score > 0) {{
              html.push('  <div class="cy-metric-card"><div class="cy-metric-label">GNN Blast Radius</div><div class="cy-metric-value" style="color:#534AB7;">' + Number(d.gnn_score).toFixed(3) + '</div></div>');
            }}
            html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Reliability R(v)</div><div class="cy-metric-value">' + Number(d.reliability || 0).toFixed(2) + ' <span style="font-size:10px;font-weight:normal;color:var(--muted);">(FT:' + Number(d.fault_tolerance || 0).toFixed(2) + ' A:' + Number(d.availability || 0).toFixed(2) + ')</span></div></div>');
            html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Maintainability M(v)</div><div class="cy-metric-value">' + Number(d.maintainability || 0).toFixed(2) + '</div></div>');
            if (d.impact > 0) {{
              html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Simulation Impact</div><div class="cy-metric-value">' + Number(d.impact).toFixed(3) + '</div></div>');
              html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Cascade Depth</div><div class="cy-metric-value">' + (d.cascade_depth || 1) + ' layers</div></div>');
            }}
            html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Centrality (MPCI)</div><div class="cy-metric-value">' + Number(d.mpci || 0).toFixed(3) + '</div></div>');
            html.push('  <div class="cy-metric-card"><div class="cy-metric-label">Fan-out (FOC)</div><div class="cy-metric-value">' + Number(d.foc || 0).toFixed(3) + '</div></div>');
            html.push('</div>');

            if (d.spof) {{
              html.push('<div style="background:#FCEBEB;color:#791F1F;padding:6px 10px;border-radius:4px;font-size:11px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px;">');
              html.push('  <span class="badge badge-spof">SPOF</span> Articulation Point: failure disconnects topology');
              html.push('</div>');
            }}

            if (d.anti_patterns && d.anti_patterns.length > 0) {{
              html.push('<div style="margin-bottom:10px;">');
              html.push('  <div style="font-size:11px;font-weight:600;color:var(--muted);margin-bottom:4px;text-transform:uppercase;">Detected Anti-Patterns</div>');
              html.push('  <div style="display:flex;gap:4px;flex-wrap:wrap;">');
              d.anti_patterns.forEach(function(ap) {{
                html.push('    <span class="badge badge-tag">' + ap + '</span>');
              }});
              html.push('  </div>');
              html.push('</div>');
            }}

            if (d.triage_rank || d.triage_priority_action) {{
              html.push('<div style="background:#f8fafc;border:0.5px solid #e2e8f0;border-left:3px solid var(--primary);padding:8px 10px;border-radius:4px;margin-bottom:10px;">');
              html.push('  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">');
              html.push('    <span style="font-size:11px;font-weight:600;color:var(--primary);">Triage Shortlist #' + (d.triage_rank || '-') + '</span>');
              if (d.triage_roles && d.triage_roles.length) {{
                var roleBadges = d.triage_roles.map(function(r) {{
                  var cls = r.toLowerCase().indexOf('devops') >= 0 ? 'badge-devops-sre' : (r.toLowerCase().indexOf('architect') >= 0 ? 'badge-architect' : 'badge-developer');
                  return '<span class="badge ' + cls + '" style="font-size:10px;">' + r + '</span>';
                }}).join(' ');
                html.push('    <div>' + roleBadges + '</div>');
              }}
              html.push('  </div>');
              if (d.triage_pattern) {{
                html.push('  <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">Root Cause: <code>' + d.triage_pattern + '</code></div>');
              }}
              if (d.triage_priority_action) {{
                html.push('  <div style="font-size:11px;color:var(--text);font-weight:500;">' + d.triage_priority_action + '</div>');
              }}
              html.push('</div>');
            }}

            html.push('<div style="font-size:11px;color:var(--muted);border-top:0.5px solid #f1f5f9;padding-top:6px;display:flex;justify-content:space-between;">');
            html.push('  <span>Inbound dependencies: <strong>' + inEdges + '</strong></span>');
            html.push('  <span>Outbound dependents: <strong>' + outEdges + '</strong></span>');
            html.push('</div>');

            inspector.innerHTML = html.join('');
            inspector.style.display = 'block';
          }}

          cy.on('tap', 'node', function(e) {{
            if (e.target.isParent && e.target.isParent()) return;
            highlightNode(e.target);
          }});

          cy.on('tap', function(e) {{
            if (e.target === cy) {{
              resetHighlight();
            }}
          }});

          window.sagCloseInspector = window.sagCloseInspector || function(gid) {{
            var insp = document.getElementById(gid + '-inspector');
            if (insp) insp.style.display = 'none';
            var cyInst = window.sagCyInstances && window.sagCyInstances[gid];
            if (cyInst) {{
              cyInst.elements().removeClass('highlighted neighbor dimmed edge-highlighted');
            }}
          }};

          window.sagChangeLayout = window.sagChangeLayout || function(gid, layoutName) {{
            var cyInst = window.sagCyInstances && window.sagCyInstances[gid];
            if (!cyInst) return;
            var opts = {{ name: layoutName, animate: true, animationDuration: 400 }};
            if (layoutName === 'cose-bilkent') {{
              opts.nodeRepulsion = 6000;
              opts.idealEdgeLength = 80;
              opts.nodeDimensionsIncludeLabels = true;
            }} else if (layoutName === 'concentric') {{
              opts.concentric = function(n) {{ return (n.data('score') || 0) * 10; }};
              opts.levelWidth = function() {{ return 2; }};
            }} else if (layoutName === 'breadthfirst') {{
              opts.directed = true;
              opts.spacingFactor = 1.2;
            }}
            var l = cyInst.layout(opts);
            l.run();
          }};

          window.sagSearchNodes = window.sagSearchNodes || function(gid, query) {{
            var cyInst = window.sagCyInstances && window.sagCyInstances[gid];
            if (!cyInst) return;
            if (!query || !query.trim()) {{
              cyInst.elements().removeClass('highlighted neighbor dimmed edge-highlighted');
              var insp = document.getElementById(gid + '-inspector');
              if (insp) insp.style.display = 'none';
              return;
            }}
            var q = query.trim().toLowerCase();
            var matched = cyInst.nodes().filter(function(n) {{
              if (n.isParent && n.isParent()) return false;
              var id = (n.data('id') || '').toLowerCase();
              var name = (n.data('name') || '').toLowerCase();
              var label = (n.data('label') || '').toLowerCase();
              return id.indexOf(q) >= 0 || name.indexOf(q) >= 0 || label.indexOf(q) >= 0;
            }});

            if (matched.length > 0) {{
              cyInst.elements().removeClass('highlighted neighbor edge-highlighted').addClass('dimmed');
              matched.removeClass('dimmed').addClass('highlighted');
              matched.parents().removeClass('dimmed');
              if (matched.length === 1) {{
                highlightNode(matched[0]);
                cyInst.center(matched[0]);
              }}
            }} else {{
              cyInst.elements().removeClass('highlighted neighbor edge-highlighted').addClass('dimmed');
            }}
          }};

          window.sagZoom = window.sagZoom || function(gid, factor) {{
            var cyInst = window.sagCyInstances && window.sagCyInstances[gid];
            if (!cyInst) return;
            cyInst.zoom({{
              level: cyInst.zoom() * factor,
              renderedPosition: {{ x: cyInst.width() / 2, y: cyInst.height() / 2 }}
            }});
          }};

          window.sagFit = window.sagFit || function(gid) {{
            var cyInst = window.sagCyInstances && window.sagCyInstances[gid];
            if (!cyInst) return;
            cyInst.fit(25);
          }};

          window.sagReset = window.sagReset || function(gid) {{
            var cyInst = window.sagCyInstances && window.sagCyInstances[gid];
            if (!cyInst) return;
            var search = document.getElementById(gid + '-search');
            if (search) search.value = '';
            cyInst.elements().removeClass('highlighted neighbor dimmed edge-highlighted');
            var insp = document.getElementById(gid + '-inspector');
            if (insp) insp.style.display = 'none';
            cyInst.fit(25);
          }};
        }})();
        </script>
        """
        self.scripts.append(script)

    # ── D3 Dependency Matrix ───────────────────────────────────────────────

    def add_dependency_matrix(
        self,
        matrix_id: str,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        title: str = "Dependency Matrix",
    ) -> None:
        """Adjacency matrix sorted by Q(v)."""
        nodes_json = json.dumps(nodes)
        edges_json = json.dumps(edges)
        crit_colors_json = json.dumps(CRITICALITY_COLORS)
        self._emit(
            f'<p style="font-size:13px;font-weight:500;margin-bottom:12px">{title}</p>'
            f'<div id="{matrix_id}" style="overflow-x:auto"></div>'
        )

        script = f"""
        <script>
        (function() {{
          if (typeof d3 === 'undefined') return;
          var rawNodes = {nodes_json};
          var rawEdges = {edges_json};
          if (!rawNodes.length) return;

          // Order most-critical-first. Node size ('value') is derived from Q(v),
          // so sorting on it is equivalent to sorting on Q(v) descending.
          rawNodes.sort(function(a, b) {{
            return (b.value || 0) - (a.value || 0);
          }});

          var n = rawNodes.length;
          var cellSize = Math.max(8, Math.min(20, Math.floor(560 / n)));
          var margin = {{top: 100, left: 100, right: 10, bottom: 10}};
          var w = n * cellSize + margin.left + margin.right;
          var h = n * cellSize + margin.top + margin.bottom;

          var nodeIndex = {{}};
          rawNodes.forEach(function(nd, i) {{ nodeIndex[nd.id] = i; }});

          var matrix = Array.from({{length: n}}, function() {{
            return new Float32Array(n);
          }});
          rawEdges.forEach(function(e) {{
            var si = nodeIndex[e.source], ti = nodeIndex[e.target];
            if (si != null && ti != null) matrix[si][ti] = e.weight || 1;
          }});

          var crit_colors = {crit_colors_json};

          var maxVal = 0;
          rawEdges.forEach(function(e) {{ if (e.weight > maxVal) maxVal = e.weight; }});
          maxVal = maxVal || 1;

          var svg = d3.select('#' + '{matrix_id}').append('svg')
            .attr('width', w).attr('height', h);
          var g = svg.append('g')
            .attr('transform', 'translate(' + margin.left + ',' + margin.top + ')');

          rawNodes.forEach(function(nd, i) {{
            var clr = crit_colors[nd.level] || '{DEFAULT_COLOR}';
            g.append('text')
              .attr('x', -4).attr('y', i * cellSize + cellSize / 2)
              .attr('text-anchor', 'end').attr('dominant-baseline', 'middle')
              .attr('font-size', Math.max(8, cellSize - 2))
              .attr('fill', clr)
              .text(nd.label ? nd.label.substring(0, 14) : nd.id);
            g.append('text')
              .attr('x', i * cellSize + cellSize / 2).attr('y', -4)
              .attr('text-anchor', 'start').attr('dominant-baseline', 'middle')
              .attr('font-size', Math.max(8, cellSize - 2))
              .attr('fill', clr)
              .attr('transform', 'rotate(-45,' + (i * cellSize + cellSize/2) + ',-4)')
              .text(nd.label ? nd.label.substring(0, 12) : nd.id);
          }});

          for (var r = 0; r < n; r++) {{
            for (var c = 0; c < n; c++) {{
              var val = matrix[r][c];
              g.append('rect')
                .attr('x', c * cellSize).attr('y', r * cellSize)
                .attr('width', cellSize - 1).attr('height', cellSize - 1)
                .attr('rx', 1)
                .attr('fill', val > 0 ? '{BRAND_PURPLE}' : '#f3f4f6')
                .attr('opacity', val > 0 ? 0.2 + 0.8 * (val / maxVal) : 1)
                .attr('stroke', '#e5e7eb').attr('stroke-width', 0.5);
            }}
          }}
        }})();
        </script>
        """
        self.scripts.append(script)

    # ── Generate ───────────────────────────────────────────────────────────

    def generate(self) -> str:
        if not self.tabs:
            self.add_tab("Overview")
        
        tabs_html = ['<div class="tabs">']
        content_html = []
        
        for i, tab in enumerate(self.tabs):
            active = " active" if i == 0 else ""
            display = "" if i == 0 else ' style="display:none"'
            tabs_html.append(
                f'<button class="tab{active}" onclick="switchTab(\'{tab["id"]}\', this)">'
                f'{tab["title"]}</button>'
            )
            content_html.append(
                f'<div id="tab-{tab["id"]}"{display}>'
                + "".join(tab["sections"])
                + '</div>'
            )
        tabs_html.append("</div>")

        return HTML_TEMPLATE.format(
            title=self.title,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            badge_css=criticality_badge_css(),
            tabs_html="".join(tabs_html),
            content="".join(content_html),
            scripts="".join(self.scripts),
        )